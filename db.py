import sqlite3

DB_PATH = "shop.db"


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _run(sql, args=(), fetch=None):
    c = _conn()
    cur = c.execute(sql, args)
    c.commit()
    if fetch == "all":
        res = cur.fetchall()
    elif fetch == "one":
        res = cur.fetchone()
    else:
        res = cur.lastrowid
    c.close()
    return res


def init():
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        tg_id INTEGER PRIMARY KEY, name TEXT, phone TEXT);
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER,
        description TEXT, active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS cart (
        tg_id INTEGER, product_id INTEGER, qty INTEGER DEFAULT 0,
        PRIMARY KEY (tg_id, product_id));
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, total INTEGER,
        status TEXT DEFAULT 'new', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS order_items (
        order_id INTEGER, product_id INTEGER, name TEXT, price INTEGER, qty INTEGER);
    """)
    c.commit()
    c.close()


def save_user(tg_id, name, phone):
    _run("INSERT OR REPLACE INTO users VALUES (?, ?, ?)", (tg_id, name, phone))


def get_user(tg_id):
    return _run("SELECT * FROM users WHERE tg_id = ?", (tg_id,), "one")


def add_product(name, price, description):
    return _run("INSERT INTO products (name, price, description) VALUES (?, ?, ?)",
                (name, price, description))


def list_products():
    return _run("SELECT * FROM products WHERE active = 1", fetch="all")


def delete_product(pid):
    _run("UPDATE products SET active = 0 WHERE id = ?", (pid,))


def add_to_cart(tg_id, pid):
    _run("""INSERT INTO cart (tg_id, product_id, qty) VALUES (?, ?, 1)
            ON CONFLICT (tg_id, product_id) DO UPDATE SET qty = qty + 1""",
         (tg_id, pid))


def cart_items(tg_id):
    return _run("""SELECT p.id, p.name, p.price, c.qty
                   FROM cart c JOIN products p ON p.id = c.product_id
                   WHERE c.tg_id = ? AND p.active = 1""", (tg_id,), "all")


def clear_cart(tg_id):
    _run("DELETE FROM cart WHERE tg_id = ?", (tg_id,))


def create_order(tg_id):
    items = cart_items(tg_id)
    if not items:
        return None
    total = sum(i["price"] * i["qty"] for i in items)
    oid = _run("INSERT INTO orders (tg_id, total) VALUES (?, ?)", (tg_id, total))
    for i in items:
        _run("INSERT INTO order_items VALUES (?, ?, ?, ?, ?)",
             (oid, i["id"], i["name"], i["price"], i["qty"]))
    clear_cart(tg_id)
    return oid, total, items
