import asyncio
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove)
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

import db

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "")
PORT = int(os.environ.get("PORT", 10000))

dp = Dispatcher(storage=MemoryStorage())


class Reg(StatesGroup):
    name = State()
    phone = State()


def fmt(n):
    return f"{n:,}".replace(",", " ")


def menu():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🛍 Katalog"), KeyboardButton(text="🛒 Savat")],
                  [KeyboardButton(text="📞 Yordam")]],
        resize_keyboard=True)


async def require_reg(m: Message):
    if db.get_user(m.from_user.id):
        return True
    await m.answer("Avval ro'yxatdan o'ting: /start")
    return False


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
    data = await state.get_data()
    db.save_user(m.from_user.id, data["name"], m.contact.phone_number)
    await state.clear()
    await m.answer("Ro'yxatdan o'tdingiz! ✅", reply_markup=menu())


@dp.message(Reg.phone)
async def reg_phone_wrong(m: Message):
    await m.answer("Raqamni pastdagi tugma orqali yuboring.")


@dp.message(F.text == "🛍 Katalog")
async def catalog(m: Message):
    if not await require_reg(m):
        return
    products = db.list_products()
    if not products:
        await m.answer("Hozircha mahsulot yo'q.")
        return
    for p in products:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🛒 Savatga", callback_data=f"add:{p['id']}")]])
        await m.answer(f"{p['name']}\n{fmt(p['price'])} so'm\n\n{p['description']}",
                       reply_markup=kb)


@dp.callback_query(F.data.startswith("add:"))
async def add_to_cart(c: CallbackQuery):
    if not db.get_user(c.from_user.id):
        await c.answer("Avval ro'yxatdan o'ting: /start", show_alert=True)
        return
    db.add_to_cart(c.from_user.id, int(c.data.split(":")[1]))
    await c.answer("Savatga qo'shildi ✅")


@dp.message(F.text == "🛒 Savat")
async def show_cart(m: Message):
    if not await require_reg(m):
        return
    items = db.cart_items(m.from_user.id)
    if not items:
        await m.answer("Savat bo'sh.")
        return
    total = sum(i["price"] * i["qty"] for i in items)
    lines = [f"{i['name']} x{i['qty']} = {fmt(i['price'] * i['qty'])} so'm" for i in items]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Buyurtma berish", callback_data="checkout")],
        [InlineKeyboardButton(text="🗑 Tozalash", callback_data="clear")]])
    await m.answer("\n".join(lines) + f"\n\nJami: {fmt(total)} so'm", reply_markup=kb)


@dp.callback_query(F.data == "checkout")
async def checkout(c: CallbackQuery):
    result = db.create_order(c.from_user.id)
    if not result:
        await c.answer("Savat bo'sh", show_alert=True)
        return
    oid, total, items = result
    user = db.get_user(c.from_user.id)
    lines = [f"{i['name']} x{i['qty']}" for i in items]
    await c.bot.send_message(
        OWNER_ID,
        f"🆕 Yangi buyurtma #{oid}\n"
        f"Mijoz: {user['name']}\n"
        f"Telefon: {user['phone']}\n\n"
        + "\n".join(lines) + f"\n\nJami: {fmt(total)} so'm")
    await c.message.answer(f"Buyurtmangiz qabul qilindi (#{oid}). Tez orada siz bilan bog'lanamiz.")
    await c.answer()


@dp.callback_query(F.data == "clear")
async def clear(c: CallbackQuery):
    db.clear_cart(c.from_user.id)
    await c.message.answer("Savat tozalandi.")
    await c.answer()


@dp.message(F.text == "📞 Yordam")
async def help_(m: Message):
    await m.answer("Savollar bo'lsa, bu yerga yozing. Admin tez orada javob beradi.")


@dp.message(Command("addproduct"), F.from_user.id == OWNER_ID)
async def add_product(m: Message):
    args = m.text.partition(" ")[2]
    parts = [p.strip() for p in args.split("|")]
    if len(parts) < 3 or not parts[1].isdigit():
        await m.answer("Format:\n/addproduct Nomi | narx | tavsif\n\nMisol:\n"
                       "/addproduct Qora hoodie | 690000 | Oversize, paxta")
        return
    pid = db.add_product(parts[0], int(parts[1]), parts[2])
    await m.answer(f"Qo'shildi ✅ ID: {pid}")


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


async def on_startup(bot: Bot):
    if BASE_URL:
        await bot.set_webhook(f"{BASE_URL}/webhook", drop_pending_updates=True)


def main():
    db.init()
    bot = Bot(BOT_TOKEN)
    dp.startup.register(on_startup)

    app = web.Application()
    app.router.add_get("/", lambda request: web.Response(text="ok"))
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
