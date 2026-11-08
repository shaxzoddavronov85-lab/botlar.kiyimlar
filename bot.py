import asyncio
import hashlib
import hmac
import io
import json
import os
import urllib.parse

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from dotenv import load_dotenv

import db

load_dotenv()
BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "")
PORT = int(os.environ.get("PORT", 10000))

dp = Dispatcher(storage=MemoryStorage())


class Reg(StatesGroup):
    name = State()
    phone = State()
    address = State()


def fmt(n):
    return f"{n:,}".replace(",", " ")


def menu():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🛍 Katalog"), KeyboardButton(text="🛒 Savat")],
                  [KeyboardButton(text="📞 Yordam")]],
        resize_keyboard=True)


def addr_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Joylashuvni yuborish", request_location=True)]],
        resize_keyboard=True, one_time_keyboard=True)


async def require_reg(m: Message):
    if db.get_user(m.from_user.id):
        return True
    await m.answer("Avval ro'yxatdan o'ting: /start")
    return False


# ---------- Ro'yxatdan o'tish ----------

@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    if db.get_user(m.from_user.id):
        await m.answer("Xush kelibsiz!", reply_markup=menu())
        return
    await state.set_state(Reg.name)
    await m.answer("Assalomu alaykum! Ro'yxatdan o'tish uchun ismingizni yozing:",
                   reply_markup=ReplyKeyboardRemove())


@dp.message(Reg.name, F.text)
async def reg_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip())
    await state.set_state(Reg.phone)
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True)
    await m.answer("Telefon raqamingizni yuboring (tugma orqali):", reply_markup=kb)


@dp.message(Reg.phone, F.contact)
async def reg_phone(m: Message, state: FSMContext):
    if m.contact.user_id != m.from_user.id:
        await m.answer("Iltimos, o'zingizning raqamingizni yuboring.")
        return
    await state.update_data(phone=m.contact.phone_number)
    await state.set_state(Reg.address)
    await m.answer("Manzilingizni yuboring: pastdagi tugma orqali joylashuv yoki manzilni matn qilib yozing.",
                   reply_markup=addr_kb())


@dp.message(Reg.phone)
async def reg_phone_wrong(m: Message):
    await m.answer("Raqamni pastdagi tugma orqali yuboring.")


async def save_address(m: Message, state: FSMContext, addr: str):
    data = await state.get_data()
    if data.get("only_addr"):
        db.set_address(m.from_user.id, addr)
        await state.clear()
        await m.answer("Manzil saqlandi ✅", reply_markup=menu())
        return
    db.save_user(m.from_user.id, data["name"], data["phone"], addr)
    await state.clear()
    await m.bot.send_message(
        OWNER_ID,
        f"🆕 Yangi foydalanuvchi\n"
        f"Ism: {data['name']}\n"
        f"Telefon: {data['phone']}\n"
        f"Manzil: {addr}\n"
        f"Telegram ID: {m.from_user.id}")
    await m.answer("Ro'yxatdan o'tdingiz! ✅", reply_markup=menu())


@dp.message(Reg.address, F.location)
async def reg_location(m: Message, state: FSMContext):
    addr = f"https://maps.google.com/?q={m.location.latitude},{m.location.longitude}"
    await save_address(m, state, addr)


@dp.message(Reg.address, F.text)
async def reg_address_text(m: Message, state: FSMContext):
    await save_address(m, state, m.text.strip())


@dp.message(Reg.address)
async def reg_address_wrong(m: Message):
    await m.answer("Manzilni matn qilib yozing yoki joylashuvni yuboring.")


@dp.message(Command("manzil"))
async def set_address_cmd(m: Message, state: FSMContext):
    if not db.get_user(m.from_user.id):
        await m.answer("Avval ro'yxatdan o'ting: /start")
        return
    await state.set_state(Reg.address)
    await state.update_data(only_addr=True)
    await m.answer("Yangi manzilingizni yuboring: joylashuv tugmasi yoki matn.", reply_markup=addr_kb())


# ---------- Katalog va boshqa tugmalar ----------

@dp.message(F.text == "🛍 Katalog")
async def catalog(m: Message):
    if not await require_reg(m):
        return
    products = db.list_products()
    if not products:
        await m.answer("Hozircha mahsulot yo'q.")
        return
    for p in products:
        await m.answer(f"{p['name']}\n{fmt(p['price'])} so'm\n\n{p['description']}")


@dp.message(F.text == "🛒 Savat")
async def show_cart(m: Message):
    if not await require_reg(m):
        return
    await m.answer("Savatni saytdagi 🛒 tugmasi orqali ko'ring: pastdagi Open tugmasini bosing.")


@dp.message(F.text == "📞 Yordam")
async def help_(m: Message):
    if not await require_reg(m):
        return
    await m.answer("Savollar bo'lsa, bu yerga yozing. Admin tez orada javob beradi.")


# ---------- Saytdan buyurtma ----------

@dp.message(F.web_app_data)
async def webapp_order(m: Message):
    user = db.get_user(m.from_user.id)
    if not user:
        await m.answer("Buyurtma berish uchun avval ro'yxatdan o'ting: /start")
        return
    if not user["address"]:
        await m.answer("Buyurtma uchun avval manzilingizni kiriting: /manzil")
        return
    data = json.loads(m.web_app_data.data)
    lines = [f"{i['name']} x{i['qty']}" for i in data["items"]]
    text = (f"🆕 Yangi buyurtma\n"
            f"Mijoz: {user['name']}\n"
            f"Telefon: {user['phone']}\n"
            f"Manzil: {user['address']}\n\n"
            + "\n".join(lines)
            + f"\n\nJami: {fmt(data['total'])} so'm")
    await m.bot.send_message(OWNER_ID, text)
    await m.answer("Buyurtmangiz qabul qilindi ✅ Tez orada siz bilan bog'lanamiz.")


# ---------- Admin (faqat egasi) ----------

@dp.message(F.photo, F.caption.startswith("/addproduct"), F.from_user.id == OWNER_ID)
async def add_product_photo(m: Message):
    args = m.caption.partition(" ")[2]
    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 3 or not parts[1].isdigit():
        await m.answer("Format (rasm tagiga yozing):\n/addproduct Nomi | narx | tavsif")
        return
    file_id = m.photo[-1].file_id
    pid = db.add_product(parts[0], int(parts[1]), parts[2], file_id)
    await m.answer(f"Qo'shildi ✅ ID: {pid} (rasm bilan)\nSaytda ko'rinishi uchun sahifani yangilang.")


@dp.message(Command("addproduct"), F.from_user.id == OWNER_ID)
async def add_product(m: Message):
    args = m.text.partition(" ")[2]
    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 3 or not parts[1].isdigit():
        await m.answer("Format:\n/addproduct Nomi | narx | tavsif\n\nMisol:\n"
                       "/addproduct Qora hoodie | 690000 | Oversize, paxta")
        return
    pid = db.add_product(parts[0], int(parts[1]), parts[2])
    await m.answer(f"Qo'shildi ✅ ID: {pid}\nSaytda ko'rinishi uchun sahifani yangilang.")


@dp.message(Command("products"), F.from_user.id == OWNER_ID)
async def products(m: Message):
    items = db.list_products()
    if not items:
        await m.answer("Mahsulot yo'q.")
        return
    await m.answer("\n".join(f"{p['id']}. {p['name']} — {fmt(p['price'])} so'm" for p in items))


@dp.message(Command("delproduct"), F.from_user.id == OWNER_ID)
async def del_product(m: Message):
    arg = m.text.partition(" ")[2].strip()
    if not arg.isdigit():
        await m.answer("Format: /delproduct ID")
        return
    db.delete_product(int(arg))
    await m.answer("O'chirildi ✅")


@dp.message(Command("users"), F.from_user.id == OWNER_ID)
async def users(m: Message):
    rows = db.list_users()
    if not rows:
        await m.answer("Hali ro'yxatdan o'tgan foydalanuvchi yo'q.")
        return
    lines = []
    for i, r in enumerate(rows, 1):
        addr = r["address"] or "manzil yo'q"
        lines.append(f"{i}. {r['name']} — {r['phone']} — {addr} (ID {r['tg_id']})")
    await m.answer(f"Foydalanuvchilar: {len(rows)} ta\n\n" + "\n".join(lines))


# ---------- Sayt uchun API ----------

CORS = {"Access-Control-Allow-Origin": "*"}


async def products_api(request):
    rows = []
    for p in db.list_products():
        d = dict(p)
        d["image_url"] = f"{BASE_URL}/photo/{p['id']}" if (p["image"] and BASE_URL) else ""
        rows.append(d)
    return web.json_response(rows, headers=CORS)


async def photo_api(request):
    pid = request.match_info["pid"]
    row = db.get_product_image(pid)
    if not row or not row["image"]:
        raise web.HTTPNotFound()
    bot = request.app["bot"]
    f = await bot.get_file(row["image"])
    buf = io.BytesIO()
    await bot.download_file(f.file_path, destination=buf)
    return web.Response(body=buf.getvalue(), content_type="image/jpeg", headers=CORS)


def verify_init(init_data):
    """Telegram Mini App ma'lumotini tekshiradi, foydalanuvchi ID sini qaytaradi."""
    try:
        d = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received = d.pop("hash", None)
        if not received:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received):
            return None
        return json.loads(d["user"])["id"]
    except Exception:
        return None


async def me_api(request):
    uid = verify_init(request.query.get("initData", ""))
    if not uid:
        return web.json_response({"error": "auth"}, status=401, headers=CORS)
    u = db.get_user(uid)
    if not u:
        return web.json_response({"error": "reg"}, status=404, headers=CORS)
    return web.json_response({"name": u["name"], "phone": u["phone"], "address": u["address"] or ""},
                             headers=CORS)


async def on_startup(bot: Bot):
    if BASE_URL:
        await bot.set_webhook(f"{BASE_URL}/webhook", drop_pending_updates=True)


def main():
    db.init()
    bot = Bot(BOT_TOKEN)
    dp.startup.register(on_startup)

    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/", lambda request: web.Response(text="ok"))
    app.router.add_get("/products", products_api)
    app.router.add_get("/photo/{pid}", photo_api)
    app.router.add_get("/me", me_api)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
