# -*- coding: utf-8 -*-
import os
import sqlite3
import json
import re
import time
from datetime import datetime
from typing import List, Tuple, Optional, Dict

import telebot
from telebot import types
from telebot.types import InputMediaPhoto

# ================== АВТО-СБРОС БАЗЫ ==================
RESET_DB = False  # для продакшена False. если нужен чистый старт — поставь True

BASE_DIR = os.path.dirname(__file__)
DB_FILE = os.path.join(BASE_DIR, "store.db")
DB_JOURNAL = DB_FILE + "-journal"

try:
    if RESET_DB:
        if os.path.exists(DB_FILE):
            os.remove(DB_FILE)
            print("⚠️ store.db — удалена для чистого запуска!")
        if os.path.exists(DB_JOURNAL):
            os.remove(DB_JOURNAL)
            print("⚠️ journal файл удалён!")
except Exception as e:
    print("Ошибка при автосбросе базы:", e)
# ====================================================


# ================== НАСТРОЙКИ ==================
TOKEN = os.getenv("INKO_BOT_TOKEN", "7557908459:AAGdtEmMpbwTTroNzSuAqe9a9BeoJxWhfew")
ADMIN_ID = 7867809053
CHANNEL_USERNAME = "@Inkoshop"  # ✅ лучше с @
CURRENCY = "₽"

REFERRAL_BONUS = 0
REFERRAL_CAP = 40

PROMO_MAX_PERCENT = 25  # лимит скидки с промокода
# ==============================================

bot = telebot.TeleBot(TOKEN, parse_mode="HTML", threaded=False)

# ================== БАЗА ДАННЫХ ==================
DB_PATH = os.path.join(BASE_DIR, "store.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row


def db_exec(query: str, params: tuple = (), fetchone=False, fetchall=False, commit=True):
    cur = conn.cursor()
    cur.execute(query, params)
    if commit:
        conn.commit()
    if fetchone:
        return cur.fetchone()
    if fetchall:
        return cur.fetchall()
    return None


def init_db():
    db_exec("""
    CREATE TABLE IF NOT EXISTS users (
        user_id     INTEGER PRIMARY KEY,
        username    TEXT,
        created_at  TEXT,
        referrer_id INTEGER DEFAULT NULL
    )
    """)

    db_exec("""
    CREATE TABLE IF NOT EXISTS categories (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT UNIQUE,
        slug        TEXT UNIQUE
    )
    """)

    db_exec("""
    CREATE TABLE IF NOT EXISTS products (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id     INTEGER,
        title           TEXT,
        description     TEXT,
        price           INTEGER,
        is_preorder     INTEGER DEFAULT 0,
        photos_json     TEXT,
        created_at      TEXT,
        FOREIGN KEY(category_id) REFERENCES categories(id)
    )
    """)

    db_exec("""
    CREATE TABLE IF NOT EXISTS cart_items (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER,
        product_id  INTEGER,
        size        TEXT,
        qty         INTEGER,
        created_at  TEXT
    )
    """)

    db_exec("""
    CREATE TABLE IF NOT EXISTS favorites (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER,
        product_id  INTEGER
    )
    """)

    db_exec("""
    CREATE TABLE IF NOT EXISTS orders (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id          INTEGER,
        status           TEXT,
        total            INTEGER,
        discount_percent INTEGER DEFAULT 0,
        final_total      INTEGER,
        promo_code       TEXT,
        created_at       TEXT
    )
    """)

    db_exec("""
    CREATE TABLE IF NOT EXISTS order_items (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id    INTEGER,
        product_id  INTEGER,
        size        TEXT,
        qty         INTEGER,
        price       INTEGER
    )
    """)

    db_exec("""
    CREATE TABLE IF NOT EXISTS settings (
        key     TEXT PRIMARY KEY,
        value   TEXT
    )
    """)

    db_exec("""
    CREATE TABLE IF NOT EXISTS promo_codes (
        code             TEXT PRIMARY KEY,
        discount_percent INTEGER,
        max_uses         INTEGER,
        used             INTEGER DEFAULT 0,
        confirmed_uses   INTEGER DEFAULT 0,
        created_at       TEXT
    )
    """)

    # сохранённый промокод пользователя
    db_exec("""
    CREATE TABLE IF NOT EXISTS user_promos (
        user_id INTEGER PRIMARY KEY,
        code TEXT,
        discount_percent INTEGER,
        set_at TEXT
    )
    """)

    db_exec("""
    CREATE TABLE IF NOT EXISTS review_invites (
        user_id INTEGER PRIMARY KEY,
        invited_at TEXT,
        used INTEGER DEFAULT 0
    )
    """)

    db_exec("""
    CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        text TEXT,
        photos_json TEXT,
        is_approved INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)

    # ================== ПАРТНЁРЫ / АВТОРСКИЕ ПРОМО ==================
    db_exec("""
    CREATE TABLE IF NOT EXISTS partner_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        username TEXT,
        status TEXT DEFAULT 'pending',
        requested_at TEXT,
        decided_at TEXT
    )
    """)

    db_exec("""
    CREATE TABLE IF NOT EXISTS partners (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        code TEXT UNIQUE,
        discount_percent INTEGER DEFAULT 5,
        commission_percent INTEGER DEFAULT 5,
        balance INTEGER DEFAULT 0,
        total_earned INTEGER DEFAULT 0,
        total_sales INTEGER DEFAULT 0,
        confirmed_uses INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 1,
        created_at TEXT
    )
    """)


def ensure_columns():
    cols = {r["name"] for r in db_exec("PRAGMA table_info(orders)", fetchall=True)}
    if "partner_commission" not in cols:
        try:
            db_exec("ALTER TABLE orders ADD COLUMN partner_commission INTEGER DEFAULT 0", commit=True)
        except Exception as e:
            print("ALTER orders partner_commission fail:", e)

    if "partner_paid" not in cols:
        try:
            db_exec("ALTER TABLE orders ADD COLUMN partner_paid INTEGER DEFAULT 0", commit=True)
        except Exception as e:
            print("ALTER orders partner_paid fail:", e)


def get_setting(key: str) -> Optional[str]:
    row = db_exec("SELECT value FROM settings WHERE key=?", (key,), fetchone=True)
    return row["value"] if row else None


def set_setting(key: str, value: str):
    db_exec(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def promo_limit_str(max_uses: Optional[int]) -> str:
    try:
        mu = int(max_uses or 0)
    except:
        mu = 0
    return "∞" if mu <= 0 else str(mu)


# ================== ПОЛЬЗОВАТЕЛИ / РЕФЕРАЛКА ==================
def add_user(user_id: int, username: Optional[str], referrer_id: Optional[int] = None):
    exists = db_exec("SELECT user_id FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if exists:
        return

    valid_ref = None
    if referrer_id and referrer_id != user_id:
        count = db_exec(
            "SELECT COUNT(*) AS c FROM users WHERE referrer_id=?",
            (referrer_id,),
            fetchone=True,
        )["c"]
        if count < REFERRAL_CAP:
            valid_ref = referrer_id

    db_exec(
        "INSERT INTO users(user_id, username, created_at, referrer_id) VALUES (?,?,?,?)",
        (user_id, username, datetime.utcnow().isoformat(), valid_ref),
    )


def update_username(user_id: int, username: Optional[str]):
    db_exec("UPDATE users SET username=? WHERE user_id=?", (username, user_id))


def get_ref_stats(user_id: int) -> Tuple[int, int]:
    c = db_exec(
        "SELECT COUNT(*) AS c FROM users WHERE referrer_id=?",
        (user_id,),
        fetchone=True,
    )["c"]
    return c, REFERRAL_CAP


# ================== КАТЕГОРИИ / ТОВАРЫ ==================
def get_or_create_category(name: str) -> int:
    name = name.strip()
    slug = name.lower()
    row = db_exec("SELECT id FROM categories WHERE slug=?", (slug,), fetchone=True)
    if row:
        return row["id"]
    db_exec("INSERT INTO categories(name, slug) VALUES(?,?)", (name, slug))
    row = db_exec("SELECT id FROM categories WHERE slug=?", (slug,), fetchone=True)
    return row["id"]


def create_product(
    category_name: str,
    title: str,
    description: str,
    price: int,
    photo_ids: List[str],
    is_preorder: bool = False,
) -> int:
    cat_id = get_or_create_category(category_name)
    db_exec(
        """
        INSERT INTO products(category_id,title,description,price,is_preorder,photos_json,created_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            cat_id, title, description, price, int(is_preorder),
            json.dumps(photo_ids), datetime.utcnow().isoformat()
        ),
    )
    row = db_exec("SELECT id FROM products ORDER BY id DESC LIMIT 1", fetchone=True)
    return row["id"]


def get_categories() -> List[sqlite3.Row]:
    return db_exec("SELECT * FROM categories ORDER BY name", fetchall=True)


def get_products_by_category(cat_id: int) -> List[sqlite3.Row]:
    return db_exec(
        "SELECT * FROM products WHERE category_id=? ORDER BY id DESC",
        (cat_id,), fetchall=True
    )


def get_product(product_id: int) -> Optional[sqlite3.Row]:
    return db_exec("SELECT * FROM products WHERE id=?", (product_id,), fetchone=True)


# ================== КОРЗИНА / ЗАКАЗЫ ==================
def add_to_cart(user_id: int, product_id: int, size: str, qty: int = 1):
    db_exec(
        """
        INSERT INTO cart_items(user_id,product_id,size,qty,created_at)
        VALUES (?,?,?,?,?)
        """,
        (user_id, product_id, size, qty, datetime.utcnow().isoformat()),
    )


def get_cart(user_id: int) -> List[sqlite3.Row]:
    return db_exec(
        """
        SELECT c.*, p.title, p.price
        FROM cart_items c
        JOIN products p ON p.id=c.product_id
        WHERE c.user_id=?
        ORDER BY c.id
        """,
        (user_id,), fetchall=True
    )


def update_cart_item_qty(item_id: int, delta: int):
    row = db_exec("SELECT qty FROM cart_items WHERE id=?", (item_id,), fetchone=True)
    if not row:
        return
    qty = int(row["qty"] or 1) + delta
    if qty <= 0:
        db_exec("DELETE FROM cart_items WHERE id=?", (item_id,))
    else:
        db_exec("UPDATE cart_items SET qty=? WHERE id=?", (qty, item_id))


def clear_cart(user_id: int):
    db_exec("DELETE FROM cart_items WHERE user_id=?", (user_id,))


def get_user_orders(user_id: int) -> List[sqlite3.Row]:
    return db_exec(
        "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC",
        (user_id,), fetchall=True
    )


def get_order(order_id: int) -> Optional[sqlite3.Row]:
    return db_exec("SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True)


def set_order_status(order_id: int, status: str):
    db_exec("UPDATE orders SET status=? WHERE id=?", (status, order_id))


# ================== ИЗБРАННОЕ ==================
def toggle_favorite(user_id: int, product_id: int) -> bool:
    row = db_exec(
        "SELECT id FROM favorites WHERE user_id=? AND product_id=?",
        (user_id, product_id), fetchone=True
    )
    if row:
        db_exec("DELETE FROM favorites WHERE id=?", (row["id"],))
        return False
    db_exec("INSERT INTO favorites(user_id,product_id) VALUES(?,?)", (user_id, product_id))
    return True


def get_favorites(user_id: int) -> List[sqlite3.Row]:
    return db_exec(
        """
        SELECT f.*, p.title, p.price
        FROM favorites f JOIN products p ON p.id=f.product_id
        WHERE f.user_id=?
        ORDER BY f.id DESC
        """,
        (user_id,), fetchall=True
    )


# ================== ПРОМОКОДЫ ==================
def get_promo(code: str) -> Optional[sqlite3.Row]:
    code = code.strip().upper()
    if not code:
        return None
    return db_exec("SELECT * FROM promo_codes WHERE code=?", (code,), fetchone=True)


def validate_promo(code: str) -> Tuple[int, str]:
    code = code.strip().upper()
    if not code:
        return 0, ""
    row = get_promo(code)
    if not row:
        return 0, ""
    max_uses = row["max_uses"]
    used = row["used"]
    if max_uses and int(max_uses) > 0 and used >= max_uses:
        return 0, ""
    percent = int(row["discount_percent"] or 0)
    percent = min(percent, PROMO_MAX_PERCENT)
    return percent, code


def apply_promo_use(code: str) -> Tuple[int, str]:
    percent, code2 = validate_promo(code)
    if not code2 or percent <= 0:
        return 0, ""
    db_exec("UPDATE promo_codes SET used=used+1 WHERE code=?", (code2,))
    return percent, code2


def promo_confirm_use(code: str):
    if not code:
        return
    db_exec("UPDATE promo_codes SET confirmed_uses=confirmed_uses+1 WHERE code=?", (code,))


def set_user_promo(user_id: int, code: str, percent: int):
    db_exec("""
        INSERT INTO user_promos(user_id, code, discount_percent, set_at)
        VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            code=excluded.code,
            discount_percent=excluded.discount_percent,
            set_at=excluded.set_at
    """, (user_id, code, percent, datetime.utcnow().isoformat()))


def clear_user_promo(user_id: int):
    db_exec("DELETE FROM user_promos WHERE user_id=?", (user_id,))


def get_user_promo(user_id: int) -> Tuple[int, str]:
    row = db_exec("SELECT * FROM user_promos WHERE user_id=?", (user_id,), fetchone=True)
    if not row:
        return 0, ""
    return int(row["discount_percent"] or 0), (row["code"] or "")


def create_review_bonus_promo(user_id: int, review_id: int) -> str:
    base = f"REV{review_id}{str(user_id)[-4:]}"
    code = base.upper()
    i = 1
    while get_promo(code) or get_partner_by_code(code):
        code = f"{base}{i}"
        i += 1

    db_exec("""
        INSERT INTO promo_codes(code,discount_percent,max_uses,used,confirmed_uses,created_at)
        VALUES(?,?,?,?,?,?)
    """, (code, 5, 1, 0, 0, datetime.utcnow().isoformat()))

    return code


# ================== ПАРТНЁРЫ ==================
def get_partner_by_code(code: str) -> Optional[sqlite3.Row]:
    if not code:
        return None
    return db_exec("SELECT * FROM partners WHERE code=? AND is_active=1", (code.upper(),), fetchone=True)


def get_partner(user_id: int) -> Optional[sqlite3.Row]:
    return db_exec("SELECT * FROM partners WHERE user_id=?", (user_id,), fetchone=True)


def create_partner_code_for_user(user_id: int, username: str) -> str:
    base = (username or f"USER{user_id}").upper()
    base = re.sub(r"[^A-Z0-9]", "", base)[:8] or f"USER{user_id}"
    code = base
    i = 1
    while get_promo(code) or get_partner_by_code(code):
        code = f"{base}{i}"
        i += 1
    return code


def approve_partner_request(user_id: int):
    u = db_exec("SELECT * FROM users WHERE user_id=?", (user_id,), fetchone=True)
    username = u["username"] if u else None

    code = create_partner_code_for_user(user_id, username or "")
    discount_percent = 5
    commission_percent = 5

    db_exec("""
        INSERT INTO promo_codes(code,discount_percent,max_uses,used,confirmed_uses,created_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(code) DO NOTHING
    """, (code, discount_percent, 0, 0, 0, datetime.utcnow().isoformat()))

    db_exec("""
        INSERT INTO partners(user_id,username,code,discount_percent,commission_percent,created_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            code=excluded.code,
            discount_percent=excluded.discount_percent,
            commission_percent=excluded.commission_percent,
            is_active=1
    """, (user_id, username, code, discount_percent, commission_percent, datetime.utcnow().isoformat()))

    db_exec("""
        INSERT INTO partner_requests(user_id,username,status,requested_at,decided_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            status='approved',
            decided_at=excluded.decided_at
    """, (user_id, username, "approved", datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))

    return code, discount_percent, commission_percent


def reject_partner_request(user_id: int):
    db_exec("""
        INSERT INTO partner_requests(user_id,username,status,requested_at,decided_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            status='rejected',
            decided_at=excluded.decided_at
    """, (user_id, None, "rejected", datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))


# ================== ОТЗЫВЫ ==================
def get_pending_reviews() -> List[sqlite3.Row]:
    return db_exec("SELECT * FROM reviews WHERE is_approved=0 ORDER BY id ASC", fetchall=True)

def get_approved_reviews_all() -> List[sqlite3.Row]:
    return db_exec("SELECT * FROM reviews WHERE is_approved=1 ORDER BY id DESC", fetchall=True)

def approve_review(review_id: int):
    db_exec("UPDATE reviews SET is_approved=1 WHERE id=?", (review_id,))

def reject_review(review_id: int):
    db_exec("DELETE FROM reviews WHERE id=?", (review_id,))


# ================== БАННЕРЫ / ЛОГО ==================
def get_banner(section: str) -> Optional[str]:
    return get_setting(f"banner_{section}")

def set_banner(section: str, file_id: str):
    set_setting(f"banner_{section}", file_id)


# ================== UI / КНОПКИ ==================
def back_btn(data="sec:menu"):
    return types.InlineKeyboardButton("⬅️ Назад", callback_data=data)

def main_menu(user_id: int):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("🛍 Каталог", callback_data="sec:catalog"),
        types.InlineKeyboardButton("🧠 Поиск", callback_data="sec:search"),
    )
    kb.add(
        types.InlineKeyboardButton("🧺 Корзина", callback_data="sec:cart"),
        types.InlineKeyboardButton("⭐️ Избранное", callback_data="sec:favs"),
    )
    kb.add(
        types.InlineKeyboardButton("👤 Профиль", callback_data="sec:profile"),
        types.InlineKeyboardButton("📝 Отзывы", callback_data="sec:reviews"),
    )
    kb.add(
        types.InlineKeyboardButton("🏷 Промокод", callback_data="sec:promo"),
        types.InlineKeyboardButton("📘 Как пользоваться", callback_data="sec:help"),
    )
    if user_id == ADMIN_ID:
        kb.add(types.InlineKeyboardButton("👑 Админ-панель", callback_data="sec:admin"))
    return kb


def category_kb(cats):
    kb = types.InlineKeyboardMarkup()
    for c in cats:
        kb.add(types.InlineKeyboardButton(f"• {c['name']}", callback_data=f"cat:{c['id']}"))
    kb.add(back_btn("sec:menu"))
    return kb


def product_nav_kb(cat_id: int, idx: int, total: int, prod_id: int):
    kb = types.InlineKeyboardMarkup(row_width=2)
    prev_data = f"pnav:{cat_id}:{idx-1}" if idx > 0 else "noop"
    next_data = f"pnav:{cat_id}:{idx+1}" if idx < total-1 else "noop"
    kb.add(
        types.InlineKeyboardButton("⬅️", callback_data=prev_data),
        types.InlineKeyboardButton("➡️", callback_data=next_data),
    )
    kb.add(types.InlineKeyboardButton("Выбрать размер / в корзину", callback_data=f"prod:{prod_id}"))
    kb.add(types.InlineKeyboardButton("⭐️ В избранное", callback_data=f"fav:{prod_id}"))
    kb.add(back_btn("sec:catalog"))
    return kb


def size_kb(prod_id: int):
    kb = types.InlineKeyboardMarkup(row_width=5)
    for s in ["XS", "S", "M", "L", "XL"]:
        kb.add(types.InlineKeyboardButton(s, callback_data=f"size:{prod_id}:{s}"))
    kb.add(back_btn("sec:catalog"))
    return kb


def cart_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Оформить заказ", callback_data="cart:checkout"))
    kb.add(types.InlineKeyboardButton("🧹 Очистить корзину", callback_data="cart:clear"))
    kb.add(back_btn("sec:menu"))
    return kb


def favs_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(back_btn("sec:menu"))
    return kb


def profile_kb(user_id: int = None):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🧾 Мои заказы", callback_data="prof:orders"))
    kb.add(types.InlineKeyboardButton("🏷 Мои промокоды", callback_data="prof:promos"))
    kb.add(types.InlineKeyboardButton("🤝 Реферальная система", callback_data="prof:refs"))
    kb.add(types.InlineKeyboardButton("🤝 Партнёрский промокод", callback_data="prof:partner"))
    kb.add(back_btn("sec:menu"))
    return kb


def order_status_kb(order_id: int):
    kb = types.InlineKeyboardMarkup()
    for code, text in [
        ("в обработке", "⚙️ В обработке"),
        ("в пути", "📦 В пути"),
        ("доставлен", "✅ Доставлен"),
        ("отменён", "❌ Отменён"),
    ]:
        kb.add(types.InlineKeyboardButton(text, callback_data=f"ost:{order_id}:{code}"))
    return kb


def admin_order_actions_kb(order_id: int, user_id: int):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Подтвердить", callback_data=f"aocf:{order_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"aocn:{order_id}")
    )
    kb.add(types.InlineKeyboardButton("💬 Написать клиенту", callback_data=f"msg:{user_id}"))
    return kb


def review_pending_kb(review_id: int):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Принять", callback_data=f"revapp:{review_id}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"revrej:{review_id}")
    )
    kb.add(back_btn("sec:admin"))
    return kb


def admin_panel_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📥 Импорт товара", callback_data="adm:import_hint"))
    kb.add(types.InlineKeyboardButton("🖼 Баннеры", callback_data="adm:banners"))
    kb.add(types.InlineKeyboardButton("📦 Заказы", callback_data="adm:orders"))
    kb.add(types.InlineKeyboardButton("🏷 Промокоды", callback_data="adm:promos"))
    kb.add(types.InlineKeyboardButton("✉️ Инвайт на отзыв", callback_data="adm:review_invite"))
    kb.add(types.InlineKeyboardButton("📝 Непринятые отзывы", callback_data="adm:reviews_pending"))
    kb.add(types.InlineKeyboardButton("📣 Рассылка", callback_data="adm:broadcast"))
    kb.add(types.InlineKeyboardButton("📊 Статистика", callback_data="adm:stats"))
    kb.add(back_btn("sec:menu"))
    return kb


# ================== ОБЯЗАТЕЛЬНАЯ ПОДПИСКА НА КАНАЛ ==================
def _channel_ref() -> str:
    ch = CHANNEL_USERNAME.strip()
    return ch if ch.startswith("@") else f"@{ch}"

def is_subscribed(user_id: int) -> bool:
    try:
        member = bot.get_chat_member(_channel_ref(), user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        print("Sub check error:", e)
        return False

def subscribe_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Подписаться", url=f"https://t.me/{_channel_ref()[1:]}"))
    kb.add(types.InlineKeyboardButton("🔄 Проверить подписку", callback_data="sub:check"))
    return kb

def send_subscribe_gate(chat_id: int):
    text = (
        "⚠️ <b>Подпишись на наш канал, чтобы пользоваться ботом</b>\n\n"
        f"Канал: {_channel_ref()}\n\n"
        "После подписки нажми «Проверить подписку»."
    )
    bot.send_message(chat_id, text, reply_markup=subscribe_kb())
# ===================================================================


# ====== Отзывы (листать по одному) ======
USER_REVIEW_INDEX: Dict[int, int] = {}

def reviews_nav_kb(idx: int, total: int):
    kb = types.InlineKeyboardMarkup(row_width=2)
    prev_data = f"revnav:{idx-1}" if idx > 0 else "noop"
    next_data = f"revnav:{idx+1}" if idx < total-1 else "noop"
    kb.add(
        types.InlineKeyboardButton("⬅️", callback_data=prev_data),
        types.InlineKeyboardButton("➡️", callback_data=next_data),
    )
    kb.add(back_btn("sec:menu"))
    return kb


def show_review(chat_id: int, user_id: int, idx: int):
    rows = get_approved_reviews_all()
    if not rows:
        bot.send_message(chat_id, "Пока нет отзывов 😔",
                         reply_markup=types.InlineKeyboardMarkup().add(back_btn("sec:menu")))
        return

    idx = max(0, min(idx, len(rows) - 1))
    USER_REVIEW_INDEX[user_id] = idx

    r = rows[idx]
    photos = json.loads(r["photos_json"]) if r["photos_json"] else []
    txt = (r["text"] or "").strip()

    caption = f"📝 <b>Отзыв</b>\n\n{txt}\n\n<i>{idx+1} из {len(rows)}</i>"
    kb = reviews_nav_kb(idx, len(rows))

    if photos:
        media = [InputMediaPhoto(pid) for pid in photos[:10]]
        media[-1].caption = caption
        media[-1].parse_mode = "HTML"
        bot.send_media_group(chat_id, media)
        bot.send_message(chat_id, "Листай отзывы:", reply_markup=kb)
    else:
        bot.send_message(chat_id, caption, reply_markup=kb)


# ================== АВТОПОДМЕНА СООБЩЕНИЙ ==================
def smart_send(chat_id: int, text: str, kb=None, origin_msg: types.Message = None, photo_id: str = None):
    try:
        if origin_msg and origin_msg.message_id:
            mid = origin_msg.message_id
            if photo_id:
                media = InputMediaPhoto(photo_id, caption=text, parse_mode="HTML")
                bot.edit_message_media(media, chat_id, mid, reply_markup=kb)
            else:
                bot.edit_message_text(text, chat_id, mid, reply_markup=kb, parse_mode="HTML")
            return
    except Exception as e:
        print("smart_send edit fail:", e)

    if photo_id:
        bot.send_photo(chat_id, photo_id, caption=text, reply_markup=kb)
    else:
        bot.send_message(chat_id, text, reply_markup=kb)


# ================== ВСПОМОГАТЕЛЬНОЕ ==================
def send_section_banner(chat_id: int, section: str, text: str, kb=None, origin_msg: types.Message = None):
    banner_id = get_banner(section)
    if banner_id:
        smart_send(chat_id, text, kb, origin_msg=origin_msg, photo_id=banner_id)
    else:
        smart_send(chat_id, text, kb, origin_msg=origin_msg)


def parse_post_to_product(caption: str) -> Tuple[str, str, str, int, bool]:
    lines = [l.strip() for l in caption.splitlines() if l.strip()]
    title = lines[0] if lines else "Без названия"
    description = "\n".join(lines[1:]) if len(lines) > 1 else ""

    price = 0
    m = re.search(r"(\d+)\s*[₽рp]", caption)
    if m:
        price = int(m.group(1))

    cat = "Разное"
    hashtags = re.findall(r"#([\wА-Яа-я0-9_]+)", caption)
    for h in hashtags:
        if h.lower() != "предзаказ":
            cat = h
            break

    is_pre = any(h.lower() == "предзаказ" for h in hashtags)
    return cat, title, description, price, is_pre


# ================== /START, /MENU, /WHOAMI ==================
@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    referrer_id = None
    try:
        parts = (message.text or "").split()
        if len(parts) > 1 and parts[1].isdigit():
            referrer_id = int(parts[1])
    except:
        pass

    add_user(message.from_user.id, message.from_user.username, referrer_id)

    # ✅ ОБЯЗАТЕЛЬНАЯ САБКА СРАЗУ ПОСЛЕ /start
    if not is_subscribed(message.from_user.id):
        send_subscribe_gate(message.chat.id)
        return

    caption = (
        "<b>Привет! Ты в официальном боте магазина Inko Shop 👋</b>\n"
        "<b>Нажми кнопку ниже, чтобы начать:</b>"
    )

    logo_id = get_setting("logo_file_id")
    if logo_id:
        try:
            bot.send_photo(message.chat.id, logo_id, caption=caption,
                           reply_markup=main_menu(message.from_user.id))
            return
        except Exception as e:
            print("Logo send error:", e)

    bot.send_message(message.chat.id, caption, reply_markup=main_menu(message.from_user.id))


@bot.message_handler(commands=["menu"])
def cmd_menu(message: types.Message):
    bot.send_message(message.chat.id, "Меню:", reply_markup=main_menu(message.from_user.id))


@bot.message_handler(commands=["whoami"])
def cmd_whoami(message: types.Message):
    bot.reply_to(
        message,
        f"Твой Telegram ID: <code>{message.from_user.id}</code>\n"
        f"username: @{message.from_user.username or '—'}"
    )


@bot.message_handler(commands=["set_logo"])
def set_logo_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    msg = bot.send_message(message.chat.id, "Пришли фото логотипа одним сообщением.")
    bot.register_next_step_handler(msg, _save_logo)


def _save_logo(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    if not message.photo:
        bot.reply_to(message, "Это не фото.")
        return
    file_id = message.photo[-1].file_id
    set_setting("logo_file_id", file_id)
    bot.reply_to(message, "✅ Логотип сохранён!")


# ================== АДМИН ИМПОРТ (ФОТО/АЛЬБОМ) ==================
@bot.message_handler(commands=["import"])
def cmd_import_hint(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    bot.reply_to(message, "Просто перешли сюда пост из канала с фото и текстом — бот импортнёт товар.")


ADMIN_IMPORT_CACHE: Dict[str, Dict] = {}
ADMIN_WAITING_FLUSH: Dict[int, str] = {}


def _finalize_admin_import(chat_id: int, caption: str, photos: List[str]):
    if not caption:
        bot.send_message(chat_id, "Нужен пост с подписью (описанием).")
        return

    cat, title, description, price, is_pre = parse_post_to_product(caption)

    if price <= 0:
        bot.send_message(chat_id, "❗️ Не нашёл цену. Укажи число перед ₽ или р.")
        return

    product_id = create_product(cat, title, description, price, photos, is_pre)

    if len(photos) >= 2:
        media = [InputMediaPhoto(pid) for pid in photos[:10]]
        media[-1].caption = (
            f"✅ Импортировано (превью):\n"
            f"<b>{title}</b>\n"
            f"Категория: <b>{cat}</b>\n"
            f"Цена: <b>{price}{CURRENCY}</b>\n"
            f"ID: <code>{product_id}</code>"
        )
        media[-1].parse_mode = "HTML"
        bot.send_media_group(chat_id, media)
    else:
        bot.send_photo(
            chat_id,
            photos[-1],
            caption=(
                f"✅ Импортировано:\n"
                f"<b>{title}</b>\n"
                f"Категория: <b>{cat}</b>\n"
                f"Цена: <b>{price}{CURRENCY}</b>\n"
                f"ID: <code>{product_id}</code>"
            )
        )


@bot.message_handler(func=lambda m: m.from_user and m.from_user.id == ADMIN_ID, content_types=["photo"])
def admin_import_product(message: types.Message):
    caption = message.caption or ""

    if message.media_group_id:
        mg_id = message.media_group_id
        ADMIN_IMPORT_CACHE.setdefault(mg_id, {"photos": [], "caption": ""})
        ADMIN_IMPORT_CACHE[mg_id]["photos"].append(message.photo[-1].file_id)
        if caption:
            ADMIN_IMPORT_CACHE[mg_id]["caption"] = caption

        ADMIN_WAITING_FLUSH[ADMIN_ID] = mg_id
        return

    photos = [message.photo[-1].file_id]
    _finalize_admin_import(message.chat.id, caption, photos)


# ================== РАЗДЕЛЫ ==================
def open_catalog(chat_id: int):
    cats = get_categories()
    if not cats:
        send_section_banner(chat_id, "catalog", "Каталог пуст.",
                            types.InlineKeyboardMarkup().add(back_btn("sec:menu")))
        return
    send_section_banner(chat_id, "catalog", "<b>Категории:</b>", category_kb(cats))


USER_CAT_INDEX: Dict[Tuple[int, int], int] = {}
USER_PRODUCT_CTRL_MSG: Dict[Tuple[int, int], int] = {}

# ✅ НОВОЕ: храним id медиа-сообщений по (user_id, cat_id)
USER_PRODUCT_MEDIA_MSGS: Dict[Tuple[int, int], List[int]] = {}


def _delete_old_product_media(chat_id: int, key: Tuple[int, int]):
    mids = USER_PRODUCT_MEDIA_MSGS.get(key, [])
    if not mids:
        return
    for mid in mids:
        try:
            bot.delete_message(chat_id, mid)
        except:
            pass
    USER_PRODUCT_MEDIA_MSGS[key] = []


def show_product(chat_id: int, user_id: int, cat_id: int, idx: int):
    prods = get_products_by_category(cat_id)
    if not prods:
        bot.send_message(chat_id, "В этой категории пока нет товаров.",
                         reply_markup=types.InlineKeyboardMarkup().add(back_btn("sec:catalog")))
        return

    idx = max(0, min(idx, len(prods) - 1))
    USER_CAT_INDEX[(user_id, cat_id)] = idx

    p = prods[idx]
    photos = json.loads(p["photos_json"]) if p["photos_json"] else []
    text = (
        f"<b>{p['title']}</b>\n"
        f"Цена: <b>{p['price']}{CURRENCY}</b>\n"
        f"Размеры: XS / S / M / L / XL\n"
        f"\n<i>{idx+1} из {len(prods)}</i>"
    )
    kb = product_nav_kb(cat_id, idx, len(prods), p["id"])

    key = (user_id, cat_id)

    # ✅ при любом показе после первого — удаляем старые медиа
    _delete_old_product_media(chat_id, key)

    new_media_mids: List[int] = []

    # 1) Отправляем новые медиа
    if len(photos) >= 2:
        media = [InputMediaPhoto(pid) for pid in photos[:10]]
        media[-1].caption = text
        media[-1].parse_mode = "HTML"
        msgs = bot.send_media_group(chat_id, media)
        for m in msgs:
            new_media_mids.append(m.message_id)
    elif photos:
        m = bot.send_photo(chat_id, photos[-1], caption=text, parse_mode="HTML")
        new_media_mids.append(m.message_id)
    else:
        m = bot.send_message(chat_id, text, parse_mode="HTML")
        new_media_mids.append(m.message_id)

    USER_PRODUCT_MEDIA_MSGS[key] = new_media_mids

    # 2) Контрольное сообщение с кнопками (одно на категорию)
    ctrl_mid = USER_PRODUCT_CTRL_MSG.get(key)
    if not ctrl_mid:
        ctrl = bot.send_message(chat_id, "Выбери действие:", reply_markup=kb)
        USER_PRODUCT_CTRL_MSG[key] = ctrl.message_id
        return

    try:
        bot.edit_message_text(
            text="Выбери действие:",
            chat_id=chat_id,
            message_id=ctrl_mid,
            reply_markup=kb
        )
    except Exception as e:
        print("ctrl msg edit fail:", e)
        USER_PRODUCT_CTRL_MSG.pop(key, None)
        ctrl = bot.send_message(chat_id, "Выбери действие:", reply_markup=kb)
        USER_PRODUCT_CTRL_MSG[key] = ctrl.message_id


def open_cart(chat_id: int, user_id: int, origin_msg: types.Message = None):
    items = get_cart(user_id)
    if not items:
        send_section_banner(chat_id, "cart", "Корзина пустая 🧺", cart_kb(), origin_msg=origin_msg)
        return

    lines = []
    total = 0
    kb = types.InlineKeyboardMarkup()

    for i in items:
        subtotal = i["price"] * i["qty"]
        total += subtotal
        lines.append(f"<b>{i['title']}</b> — {i['qty']} шт., {i['size']}, {subtotal}{CURRENCY}")

        kb.row(
            types.InlineKeyboardButton("➖", callback_data=f"cqty:{i['id']}:-1"),
            types.InlineKeyboardButton(f"{i['qty']} шт.", callback_data="noop"),
            types.InlineKeyboardButton("➕", callback_data=f"cqty:{i['id']}:1"),
            types.InlineKeyboardButton("🗑", callback_data=f"cdel:{i['id']}")
        )

    text = "<b>Корзина:</b>\n" + "\n".join(lines) + f"\n\nИтого: <b>{total}{CURRENCY}</b>"
    kb.add(types.InlineKeyboardButton("✅ Оформить заказ", callback_data="cart:checkout"))
    kb.add(types.InlineKeyboardButton("🧹 Очистить корзину", callback_data="cart:clear"))
    kb.add(back_btn("sec:menu"))

    send_section_banner(chat_id, "cart", text, kb, origin_msg=origin_msg)


def open_favs(chat_id: int, user_id: int, origin_msg: types.Message = None):
    favs = get_favorites(user_id)
    if not favs:
        send_section_banner(chat_id, "favs", "Избранное пустое ⭐️", favs_kb(), origin_msg=origin_msg)
        return
    text = "<b>Избранное:</b>\n\n" + "\n".join([f"• {f['title']} — {f['price']}{CURRENCY}" for f in favs])
    send_section_banner(chat_id, "favs", text, favs_kb(), origin_msg=origin_msg)


def open_profile(chat_id: int, user_id: int, origin_msg: types.Message = None):
    send_section_banner(chat_id, "profile", "<b>Профиль</b>\nВыбери раздел:", profile_kb(user_id), origin_msg=origin_msg)


def open_reviews(chat_id: int, user_id: int):
    show_review(chat_id, user_id, 0)


def promo_section_kb(user_id: int):
    kb = types.InlineKeyboardMarkup()
    percent, code = get_user_promo(user_id)
    if code:
        kb.add(types.InlineKeyboardButton("🧹 Сбросить промокод", callback_data="promo:clear"))
    kb.add(back_btn("sec:menu"))
    return kb


def open_promo_section(chat_id: int, user_id: int, origin_msg: types.Message = None):
    percent, code = get_user_promo(user_id)
    if code:
        text = (
            "<b>🏷 Промокод активен</b>\n\n"
            f"Код: <code>{code}</code>\n"
            f"Скидка: <b>{percent}%</b>\n\n"
            "Если хочешь заменить — просто введи новый код сообщением."
        )
    else:
        text = (
            "<b>🏷 Промокод</b>\n\n"
            "Введи промокод сообщением.\n"
            "Скидка сохранится и применится при следующей покупке."
        )
    send_section_banner(chat_id, "promo", text, promo_section_kb(user_id), origin_msg=origin_msg)
    if user_id != ADMIN_ID:
        msg = bot.send_message(chat_id, "Введи промокод:")
        bot.register_next_step_handler(msg, lambda m: handle_user_promo_input(m, user_id))


def open_help(chat_id: int, origin_msg: types.Message = None):
    text = (
        "<b>Как пользоваться:</b>\n\n"
        "1) Открой Каталог → выбери категорию.\n"
        "2) Листай товары стрелками.\n"
        "3) Нажми «Выбрать размер / в корзину».\n"
        "4) В Корзине нажми «Оформить” заказ».\n"
        "5) Дальше админ свяжется с тобой.\n"
        "Промокод можно ввести заранее в меню «Промокод».\n"
    )
    send_section_banner(chat_id, "help", text,
                        types.InlineKeyboardMarkup().add(back_btn("sec:menu")),
                        origin_msg=origin_msg)


def open_admin_panel(chat_id: int, user_id: int, origin_msg: types.Message = None):
    if user_id != ADMIN_ID:
        smart_send(chat_id, "Доступ только админу.", origin_msg=origin_msg)
        return
    send_section_banner(chat_id, "admin", "<b>Админ-панель</b>", admin_panel_kb(), origin_msg=origin_msg)


# ================== CALLBACKS ==================
@bot.callback_query_handler(func=lambda c: c.data == "noop")
def cb_noop(c: types.CallbackQuery):
    bot.answer_callback_query(c.id)


@bot.callback_query_handler(func=lambda c: c.data == "sub:check")
def cb_sub_check(c: types.CallbackQuery):
    uid = c.from_user.id
    bot.answer_callback_query(c.id)

    if not is_subscribed(uid):
        bot.answer_callback_query(c.id, "Ты ещё не подписан 😔", show_alert=True)
        return

    try:
        bot.delete_message(c.message.chat.id, c.message.message_id)
    except:
        pass

    bot.send_message(c.message.chat.id, "✅ Спасибо за подписку! Вот меню:", reply_markup=main_menu(uid))


@bot.callback_query_handler(func=lambda c: c.data.startswith("sec:"))
def cb_section(c: types.CallbackQuery):
    sec = c.data.split(":", 1)[1]
    uid = c.from_user.id
    bot.answer_callback_query(c.id)

    if sec == "menu":
        smart_send(c.message.chat.id, "Меню:", main_menu(uid), origin_msg=c.message)

    elif sec == "catalog":
        open_catalog(c.message.chat.id)

    elif sec == "reviews":
        open_reviews(c.message.chat.id, uid)

    elif sec == "cart":
        open_cart(c.message.chat.id, uid, origin_msg=c.message)

    elif sec == "favs":
        open_favs(c.message.chat.id, uid, origin_msg=c.message)

    elif sec == "profile":
        open_profile(c.message.chat.id, uid, origin_msg=c.message)

    elif sec == "promo":
        open_promo_section(c.message.chat.id, uid, origin_msg=c.message)

    elif sec == "help":
        open_help(c.message.chat.id, origin_msg=c.message)

    elif sec == "admin":
        open_admin_panel(c.message.chat.id, uid, origin_msg=c.message)

    elif sec == "search":
        msg = bot.send_message(c.message.chat.id, "Напиши часть названия товара:")
        bot.register_next_step_handler(msg, search_products)


@bot.callback_query_handler(func=lambda c: c.data == "promo:clear")
def cb_promo_clear(c: types.CallbackQuery):
    uid = c.from_user.id
    clear_user_promo(uid)
    bot.answer_callback_query(c.id, "Промокод сброшен")
    open_promo_section(c.message.chat.id, uid, origin_msg=c.message)


def handle_user_promo_input(message: types.Message, user_id: int):
    code = (message.text or "").strip().upper()
    percent, norm_code = validate_promo(code)
    if not norm_code:
        bot.reply_to(message, "Промокод не найден или закончился лимит.")
        return
    set_user_promo(user_id, norm_code, percent)
    bot.reply_to(
        message,
        f"✅ Промокод <code>{norm_code}</code> активирован.\n"
        f"Скидка: <b>{percent}%</b>\n"
        "Применится при следующей покупке.",
        reply_markup=types.InlineKeyboardMarkup().add(back_btn("sec:menu"))
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("cat:"))
def cb_open_category(c: types.CallbackQuery):
    cat_id = int(c.data.split(":", 1)[1])
    uid = c.from_user.id
    bot.answer_callback_query(c.id)
    show_product(c.message.chat.id, uid, cat_id, 0)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pnav:"))
def cb_product_nav(c: types.CallbackQuery):
    _, cat_id, idx = c.data.split(":")
    cat_id = int(cat_id)
    idx = int(idx)
    uid = c.from_user.id
    bot.answer_callback_query(c.id)
    show_product(c.message.chat.id, uid, cat_id, idx)


@bot.callback_query_handler(func=lambda c: c.data.startswith("revnav:"))
def cb_review_nav(c: types.CallbackQuery):
    idx = int(c.data.split(":", 1)[1])
    uid = c.from_user.id
    bot.answer_callback_query(c.id)
    show_review(c.message.chat.id, uid, idx)


@bot.callback_query_handler(func=lambda c: c.data.startswith("cqty:"))
def cb_cart_qty(c: types.CallbackQuery):
    _, item_id, delta = c.data.split(":")
    item_id = int(item_id); delta = int(delta)
    bot.answer_callback_query(c.id)
    update_cart_item_qty(item_id, delta)
    open_cart(c.message.chat.id, c.from_user.id, origin_msg=c.message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("cdel:"))
def cb_cart_del(c: types.CallbackQuery):
    item_id = int(c.data.split(":")[1])
    bot.answer_callback_query(c.id)
    db_exec("DELETE FROM cart_items WHERE id=?", (item_id,))
    open_cart(c.message.chat.id, c.from_user.id, origin_msg=c.message)


def search_products(message: types.Message):
    text = (message.text or "").strip()
    if not text:
        bot.reply_to(message, "Пустой запрос.")
        return
    rows = db_exec("SELECT * FROM products WHERE title LIKE ? ORDER BY id DESC",
                   (f"%{text}%",), fetchall=True)
    if not rows:
        bot.reply_to(message, "Ничего не найдено.")
        return
    bot.reply_to(message, f"Найдено: {len(rows)}. Показываю первые:")
    for p in rows[:5]:
        photos = json.loads(p["photos_json"]) if p["photos_json"] else []
        caption = f"<b>{p['title']}</b>\nЦена: <b>{p['price']}{CURRENCY}</b>"
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Выбрать размер / в корзину", callback_data=f"prod:{p['id']}"))
        kb.add(back_btn("sec:menu"))
        if photos:
            bot.send_photo(message.chat.id, photos[-1], caption=caption, reply_markup=kb)
        else:
            bot.send_message(message.chat.id, caption, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("prod:"))
def cb_product(c: types.CallbackQuery):
    prod_id = int(c.data.split(":", 1)[1])
    bot.answer_callback_query(c.id)
    bot.send_message(c.message.chat.id, "Выбери размер:", reply_markup=size_kb(prod_id))


@bot.callback_query_handler(func=lambda c: c.data.startswith("size:"))
def cb_choose_size(c: types.CallbackQuery):
    _, prod_id, size = c.data.split(":")
    prod_id = int(prod_id)
    p = get_product(prod_id)
    if not p:
        bot.answer_callback_query(c.id, "Товар не найден.")
        return
    add_to_cart(c.from_user.id, prod_id, size)
    bot.answer_callback_query(c.id, f"Добавлено ({size})")
    bot.send_message(
        c.message.chat.id,
        f"✅ {p['title']} ({size}) добавлен в корзину.",
        reply_markup=types.InlineKeyboardMarkup().add(back_btn("sec:catalog"))
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("fav:"))
def cb_fav(c: types.CallbackQuery):
    prod_id = int(c.data.split(":", 1)[1])
    added = toggle_favorite(c.from_user.id, prod_id)
    bot.answer_callback_query(c.id, "Добавлено ⭐️" if added else "Удалено")


# ====== ЧЕКАУТ БЕЗ запроса промокода ======
@bot.callback_query_handler(func=lambda c: c.data.startswith("cart:"))
def cb_cart(c: types.CallbackQuery):
    act = c.data.split(":", 1)[1]
    uid = c.from_user.id
    bot.answer_callback_query(c.id)

    if act == "clear":
        clear_cart(uid)
        open_cart(c.message.chat.id, uid, origin_msg=c.message)
        return

    if act == "checkout":
        _process_checkout_by_code(c.message.chat.id, uid)


def _process_checkout_by_code(chat_id: int, user_id: int):
    items = get_cart(user_id)
    if not items:
        bot.send_message(chat_id, "Корзина пустая.")
        return

    total = sum(i["price"] * i["qty"] for i in items)

    saved_percent, saved_code = get_user_promo(user_id)
    discount_percent = 0
    promo_code = ""

    if saved_code:
        discount_percent, promo_code = apply_promo_use(saved_code)
        if not promo_code:
            clear_user_promo(user_id)
            discount_percent = 0

    final_total = int(round(total * (100 - discount_percent) / 100)) if discount_percent else total

    db_exec(
        """
        INSERT INTO orders(user_id,status,total,discount_percent,final_total,promo_code,created_at,partner_commission,partner_paid)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (user_id, "новый", total, discount_percent, final_total,
         promo_code or None, datetime.utcnow().isoformat(), 0, 0),
    )
    order_id = db_exec("SELECT id FROM orders ORDER BY id DESC LIMIT 1", fetchone=True)["id"]

    for i in items:
        db_exec(
            "INSERT INTO order_items(order_id,product_id,size,qty,price) VALUES (?,?,?,?,?)",
            (order_id, i["product_id"], i["size"], i["qty"], i["price"]),
        )

    clear_cart(user_id)

    user_text = (
        f"✅ Заказ <b>#{order_id}</b> оформлен!\n"
        f"Сумма: <b>{total}{CURRENCY}</b>\n"
    )
    if discount_percent:
        user_text += (
            f"Промокод: <code>{promo_code}</code>\n"
            f"Скидка: <b>{discount_percent}%</b>\n"
            f"К оплате: <b>{final_total}{CURRENCY}</b>\n"
        )
    else:
        user_text += f"К оплате: <b>{final_total}{CURRENCY}</b>\n"

    user_text += "\nАдмин свяжется с тобой."
    bot.send_message(chat_id, user_text,
                     reply_markup=types.InlineKeyboardMarkup().add(back_btn("sec:menu")))

    order_lines = [
        f"{i['title']} — {i['qty']} шт., {i['size']}, {i['price']}{CURRENCY}"
        for i in items
    ]
    adm_text = (
        f"🆕 <b>Новый заказ #{order_id}</b>\n"
        f"Покупатель: <a href='tg://user?id={user_id}'>{user_id}</a>\n\n"
        + "\n".join(order_lines)
        + f"\n\nСумма: <b>{total}{CURRENCY}</b>\n"
    )
    if discount_percent:
        adm_text += (
            f"Скидка: {discount_percent}% по <code>{promo_code}</code>\n"
            f"Итог: <b>{final_total}{CURRENCY}</b>\n"
        )
    else:
        adm_text += f"Итог: <b>{final_total}{CURRENCY}</b>\n"

    bot.send_message(ADMIN_ID, adm_text,
                     reply_markup=admin_order_actions_kb(order_id, user_id))


# ================== АДМИН: ПОДТВЕРДИТЬ/ОТКЛОНИТЬ ==================
@bot.callback_query_handler(func=lambda c: c.data.startswith("aocf:"))
def cb_admin_confirm(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    order_id = int(c.data.split(":", 1)[1])
    o = get_order(order_id)
    if not o:
        bot.answer_callback_query(c.id, "Заказ не найден.")
        return

    set_order_status(order_id, "подтверждён")
    if o["promo_code"]:
        promo_confirm_use(o["promo_code"])

    if o["promo_code"] and not int(o.get("partner_paid") or 0):
        partner = get_partner_by_code(o["promo_code"])
        if partner:
            commission_percent = int(partner["commission_percent"] or 0)
            final_total = int(o["final_total"] or o["total"] or 0)
            commission = int(round(final_total * commission_percent / 100)) if commission_percent else 0

            if commission > 0:
                db_exec("""
                    UPDATE partners
                    SET balance = balance + ?,
                        total_earned = total_earned + ?,
                        total_sales = total_sales + ?,
                        confirmed_uses = confirmed_uses + 1
                    WHERE user_id=?
                """, (commission, commission, final_total, partner["user_id"]))

                db_exec("""
                    UPDATE orders
                    SET partner_commission=?, partner_paid=1
                    WHERE id=?
                """, (commission, order_id))

                try:
                    bot.send_message(
                        partner["user_id"],
                        "💸 По твоему промокоду подтверждена покупка!\n"
                        f"Сумма после скидки: <b>{final_total}{CURRENCY}</b>\n"
                        f"Твоя комиссия {commission_percent}%: <b>{commission}{CURRENCY}</b>\n"
                        f"Баланс обновлён ✅"
                    )
                except:
                    pass

    bot.answer_callback_query(c.id, "Подтверждено.")
    try:
        bot.send_message(o["user_id"], f"✅ Заказ #{order_id} подтверждён админом.")
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("aocn:"))
def cb_admin_cancel(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    order_id = int(c.data.split(":", 1)[1])
    o = get_order(order_id)
    if not o:
        bot.answer_callback_query(c.id, "Заказ не найден.")
        return

    set_order_status(order_id, "отклонён")
    bot.answer_callback_query(c.id, "Отклонено.")
    try:
        bot.send_message(o["user_id"], f"❌ Заказ #{order_id} отклонён.")
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("msg:"))
def cb_admin_msg_client(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    user_id = int(c.data.split(":", 1)[1])
    bot.answer_callback_query(c.id)
    msg = bot.send_message(ADMIN_ID, f"Напиши сообщение клиенту {user_id}:")
    bot.register_next_step_handler(msg, lambda m: _send_admin_message_to_user(m, user_id))


def _send_admin_message_to_user(message: types.Message, user_id: int):
    if message.from_user.id != ADMIN_ID:
        return
    text = message.text or ""
    try:
        bot.send_message(user_id, f"💬 Сообщение от администратора:\n\n{text}")
        bot.reply_to(message, "✅ Отправлено.")
    except Exception as e:
        bot.reply_to(message, f"Не смог отправить: {e}")


@bot.callback_query_handler(func=lambda c: c.data.startswith("ost:"))
def cb_order_status(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Только для админа.")
        return
    _, order_id, status = c.data.split(":", 2)
    order_id = int(order_id)
    set_order_status(order_id, status)
    bot.answer_callback_query(c.id, f"Статус: {status}")

    o = get_order(order_id)
    if o:
        try:
            bot.send_message(o["user_id"], f"🔔 Статус заказа #{order_id}: <b>{status}</b>")
        except:
            pass


# ================== ПАРТНЁР: ОДОБРЕНИЕ/ОТКЛОНИТЬ ==================
@bot.callback_query_handler(func=lambda c: c.data.startswith("prapp:"))
def cb_partner_req_approve(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    uid = int(c.data.split(":", 1)[1])
    code, dp, cp = approve_partner_request(uid)
    bot.answer_callback_query(c.id, "Одобрено")
    bot.send_message(c.message.chat.id, f"✅ Партнёрство одобрено. Код: {code}")

    try:
        bot.send_message(
            uid,
            "🎉 Тебя одобрили как партнёра!\n\n"
            f"Твой промокод: <code>{code}</code>\n"
            f"Скидка клиенту: <b>{dp}%</b>\n"
            f"Твоя комиссия: <b>{cp}%</b> от суммы после скидки.\n\n"
            "Статистика будет в Профиле → Партнёрский промокод."
        )
    except:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("prrej:"))
def cb_partner_req_reject(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    uid = int(c.data.split(":", 1)[1])
    reject_partner_request(uid)
    bot.answer_callback_query(c.id, "Отклонено")
    bot.send_message(c.message.chat.id, "❌ Заявка партнёра отклонена.")
    try:
        bot.send_message(uid, "❌ Заявка на партнёрство отклонена админом.")
    except:
        pass


# ================== ПРОФИЛЬ (вкладки) ==================
@bot.callback_query_handler(func=lambda c: c.data.startswith("prof:"))
def cb_profile_tabs(c: types.CallbackQuery):
    tab = c.data.split(":", 1)[1]
    uid = c.from_user.id
    bot.answer_callback_query(c.id)

    if tab == "orders":
        orders = get_user_orders(uid)
        if not orders:
            smart_send(
                c.message.chat.id,
                "У тебя пока нет заказов.",
                types.InlineKeyboardMarkup().add(back_btn("sec:profile")),
                origin_msg=c.message
            )
            return

        text = "<b>Твои заказы:</b>\n\n"
        kb = types.InlineKeyboardMarkup()
        for o in orders:
            base = o["total"]
            final = o["final_total"] or base
            text += f"• #{o['id']} — {base}{CURRENCY} → {final}{CURRENCY} | <b>{o['status']}</b>\n"
            kb.add(types.InlineKeyboardButton(f"Подробнее #{o['id']}", callback_data=f"ordview:{o['id']}"))
        kb.add(back_btn("sec:profile"))

        smart_send(c.message.chat.id, text, kb, origin_msg=c.message)

    elif tab == "promos":
        rows = db_exec("SELECT * FROM promo_codes ORDER BY created_at DESC", fetchall=True)
        if not rows:
            smart_send(
                c.message.chat.id,
                "Промокодов пока нет.",
                types.InlineKeyboardMarkup().add(back_btn("sec:profile")),
                origin_msg=c.message
            )
            return
        text = "<b>Промокоды магазина:</b>\n\n"
        for r in rows:
            dp = min(int(r['discount_percent'] or 0), PROMO_MAX_PERCENT)
            used = int(r["used"] or 0)
            conf = int(r["confirmed_uses"] or 0)
            mu_str = promo_limit_str(r["max_uses"])
            left = "∞" if mu_str == "∞" else str(max(int(mu_str) - used, 0))

            text += (
                f"• <code>{r['code']}</code> — {dp}%\n"
                f"  Использовано (резерв): <b>{used}/{mu_str}</b>\n"
                f"  Подтверждено покупок: <b>{conf}</b>\n"
                f"  Осталось: <b>{left}</b>\n\n"
            )
        smart_send(c.message.chat.id, text,
                   types.InlineKeyboardMarkup().add(back_btn("sec:profile")),
                   origin_msg=c.message)

    elif tab == "refs":
        count, cap = get_ref_stats(uid)
        link = f"https://t.me/{bot.get_me().username}?start={uid}"
        text = (
            "<b>Реферальная система</b>\n\n"
            f"Твоя ссылка:\n<code>{link}</code>\n\n"
            f"Приглашено: <b>{count}/{cap}</b>"
        )
        smart_send(c.message.chat.id, text,
                   types.InlineKeyboardMarkup().add(back_btn("sec:profile")),
                   origin_msg=c.message)

    elif tab == "partner":
        p = get_partner(uid)
        req = db_exec("SELECT * FROM partner_requests WHERE user_id=?", (uid,), fetchone=True)

        if p and p["is_active"] == 1:
            text = (
                "<b>🤝 Ты партнёр магазина</b>\n\n"
                f"Твой промокод: <code>{p['code']}</code>\n"
                f"Скидка клиенту: <b>{p['discount_percent']}%</b>\n"
                f"Твоя комиссия: <b>{p['commission_percent']}%</b> от суммы после скидки\n\n"
                f"Подтверждённых покупок: <b>{p['confirmed_uses']}</b>\n"
                f"Продаж на сумму: <b>{p['total_sales']}{CURRENCY}</b>\n"
                f"Заработано всего: <b>{p['total_earned']}{CURRENCY}</b>\n"
                f"Баланс: <b>{p['balance']}{CURRENCY}</b>\n\n"
                "Чтобы вывести баланс — напиши админу."
            )
            smart_send(c.message.chat.id, text,
                       types.InlineKeyboardMarkup().add(back_btn("sec:profile")),
                       origin_msg=c.message)
            return

        if req and req["status"] == "pending":
            smart_send(c.message.chat.id,
                       "⏳ Твоя заявка на партнёрство уже на рассмотрении.",
                       types.InlineKeyboardMarkup().add(back_btn("sec:profile")),
                       origin_msg=c.message)
            return

        db_exec("""
            INSERT INTO partner_requests(user_id,username,status,requested_at)
            VALUES(?,?, 'pending', ?)
            ON CONFLICT(user_id) DO UPDATE SET
                status='pending',
                requested_at=excluded.requested_at
        """, (uid, c.from_user.username, datetime.utcnow().isoformat()))

        smart_send(
            c.message.chat.id,
            "✅ Заявка на партнёрский промокод отправлена админу.\n"
            "После одобрения ты получишь свой код и статистику в профиле.",
            types.InlineKeyboardMarkup().add(back_btn("sec:profile")),
            origin_msg=c.message
        )

        adm_kb = types.InlineKeyboardMarkup(row_width=2)
        adm_kb.add(
            types.InlineKeyboardButton("✅ Одобрить", callback_data=f"prapp:{uid}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"prrej:{uid}")
        )
        bot.send_message(
            ADMIN_ID,
            f"🤝 <b>Новая заявка на партнёрство</b>\n"
            f"Пользователь: <a href='tg://user?id={uid}'>{uid}</a>\n"
            f"@{c.from_user.username or '—'}",
            reply_markup=adm_kb
        )


@bot.callback_query_handler(func=lambda c: c.data.startswith("ordview:"))
def cb_order_view(c: types.CallbackQuery):
    order_id = int(c.data.split(":")[1])
    bot.answer_callback_query(c.id)

    o = get_order(order_id)
    if not o:
        smart_send(c.message.chat.id, "Заказ не найден.",
                   types.InlineKeyboardMarkup().add(back_btn("sec:profile")),
                   origin_msg=c.message)
        return

    items = db_exec("""
        SELECT oi.*, p.title
        FROM order_items oi
        LEFT JOIN products p ON p.id=oi.product_id
        WHERE oi.order_id=?
    """, (order_id,), fetchall=True)

    lines = []
    for it in items:
        lines.append(f"• {it['title']} — {it['qty']} шт., {it['size']}")

    base = o["total"]
    final = o["final_total"] or base
    promo = o["promo_code"] or "нет"

    text = (
        f"<b>Заказ #{order_id}</b>\n"
        f"Статус: <b>{o['status']}</b>\n"
        f"Промо: <code>{promo}</code>\n"
        f"Сумма: {base}{CURRENCY} → <b>{final}{CURRENCY}</b>\n\n"
        "<b>Позиции:</b>\n" + "\n".join(lines)
    )

    smart_send(c.message.chat.id, text,
               types.InlineKeyboardMarkup().add(back_btn("sec:profile")),
               origin_msg=c.message)


# ================== АДМИН: IMPORT HINT КНОПКА ==================
@bot.callback_query_handler(func=lambda c: c.data == "adm:import_hint")
def cb_adm_import_hint(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    bot.answer_callback_query(c.id)
    txt = (
        "📥 <b>Импорт товара</b>\n\n"
        "Перешли в бота пост из канала.\n"
        "Пост может быть альбомом с несколькими фото.\n"
        "Цена — числом перед ₽ или р.\n"
        "Категория — первым #хэштегом.\n"
        "Если есть #предзаказ — отметится как предзаказ.\n\n"
        "Если переслал альбом — после него отправь любое сообщение, "
        "чтобы бот завершил импорт."
    )
    smart_send(c.message.chat.id, txt,
               types.InlineKeyboardMarkup().add(back_btn("sec:admin")),
               origin_msg=c.message)


# ================== АДМИН: БАННЕРЫ ==================
@bot.callback_query_handler(func=lambda c: c.data == "adm:banners")
def cb_adm_banners(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    bot.answer_callback_query(c.id)
    kb = types.InlineKeyboardMarkup()
    for sec, name in [
        ("catalog", "Каталог"),
        ("search", "Поиск"),
        ("cart", "Корзина"),
        ("favs", "Избранное"),
        ("profile", "Профиль"),
        ("reviews", "Отзывы"),
        ("promo", "Промокод"),
        ("help", "Как пользоваться"),
        ("admin", "Админ-панель"),
    ]:
        kb.add(types.InlineKeyboardButton(name, callback_data=f"setb:{sec}"))
    kb.add(back_btn("sec:admin"))
    smart_send(c.message.chat.id, "Выбери раздел для баннера:",
               kb, origin_msg=c.message)


@bot.callback_query_handler(func=lambda c: c.data.startswith("setb:"))
def cb_setb(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    section = c.data.split(":", 1)[1]
    bot.answer_callback_query(c.id)
    msg = bot.send_message(c.message.chat.id, f"Пришли одно фото для баннера {section}:")
    bot.register_next_step_handler(msg, lambda m: save_banner_photo(m, section))


def save_banner_photo(message: types.Message, section: str):
    if message.from_user.id != ADMIN_ID:
        return
    if not message.photo:
        bot.reply_to(message, "Это не фото.")
        return
    file_id = message.photo[-1].file_id
    set_banner(section, file_id)
    bot.reply_to(message, "✅ Баннер сохранён.")


# ================== АДМИН: ЗАКАЗЫ ==================
@bot.callback_query_handler(func=lambda c: c.data == "adm:orders")
def cb_adm_orders(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    bot.answer_callback_query(c.id)
    rows = db_exec("SELECT * FROM orders ORDER BY id DESC LIMIT 20", fetchall=True)
    if not rows:
        smart_send(c.message.chat.id, "Заказов пока нет.",
                   types.InlineKeyboardMarkup().add(back_btn("sec:admin")),
                   origin_msg=c.message)
        return
    for o in rows:
        base = o["total"]
        final = o["final_total"] or base
        promo = o["promo_code"] or "нет"
        text = (
            f"Заказ #{o['id']}\n"
            f"User: {o['user_id']}\n"
            f"{base}{CURRENCY} → {final}{CURRENCY}\n"
            f"Промо: {promo}\n"
            f"Статус: <b>{o['status']}</b>"
        )
        bot.send_message(c.message.chat.id, text, reply_markup=order_status_kb(o["id"]))


# ================== АДМИН: ПРОМОКОДЫ ==================
@bot.callback_query_handler(func=lambda c: c.data == "adm:promos")
def cb_adm_promos(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    bot.answer_callback_query(c.id)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Создать промокод", callback_data="adm:promo_new"))
    kb.add(types.InlineKeyboardButton("📋 Список промокодов", callback_data="adm:promo_list"))
    kb.add(back_btn("sec:admin"))
    smart_send(c.message.chat.id, "Промокоды:", kb, origin_msg=c.message)


@bot.callback_query_handler(func=lambda c: c.data == "adm:promo_new")
def cb_adm_promo_new(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    bot.answer_callback_query(c.id)
    msg = bot.send_message(
        c.message.chat.id,
        "Формат:\n<code>КОД СКИДКА% МАКС_ИСП</code>\n"
        f"Максимальная скидка всё равно {PROMO_MAX_PERCENT}%.\n"
        "Пример: <code>SUMMER10 10 50</code>"
    )
    bot.register_next_step_handler(msg, admin_create_promo)


def admin_create_promo(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split()
    if len(parts) < 3:
        bot.reply_to(message, "Неверный формат.")
        return
    code = parts[0].upper()
    try:
        percent = int(parts[1]); max_uses = int(parts[2])
    except:
        bot.reply_to(message, "Скидка и лимит должны быть числами.")
        return

    db_exec("""
        INSERT INTO promo_codes(code,discount_percent,max_uses,used,confirmed_uses,created_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(code) DO UPDATE SET
            discount_percent=excluded.discount_percent,
            max_uses=excluded.max_uses
    """, (code, percent, max_uses, 0, 0, datetime.utcnow().isoformat()))

    bot.reply_to(message, f"✅ Промокод <code>{code}</code> создан.")


@bot.callback_query_handler(func=lambda c: c.data == "adm:promo_list")
def cb_adm_promo_list(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    bot.answer_callback_query(c.id)
    rows = db_exec("SELECT * FROM promo_codes ORDER BY created_at DESC", fetchall=True)
    if not rows:
        smart_send(c.message.chat.id, "Промокодов нет.",
                   types.InlineKeyboardMarkup().add(back_btn("sec:admin")),
                   origin_msg=c.message)
        return
    text = "<b>Промокоды:</b>\n\n"
    for r in rows:
        dp = min(int(r["discount_percent"] or 0), PROMO_MAX_PERCENT)
        used = int(r["used"] or 0)
        conf = int(r["confirmed_uses"] or 0)
        mu_str = promo_limit_str(r["max_uses"])
        left = "∞" if mu_str == "∞" else str(max(int(mu_str) - used, 0))

        text += (
            f"• <code>{r['code']}</code> — {dp}% (лимит {mu_str})\n"
            f"  Использовано (резерв): {used}/{mu_str}\n"
            f"  Подтверждённых: {conf}\n"
            f"  Осталось: {left}\n\n"
        )
    smart_send(c.message.chat.id, text,
               types.InlineKeyboardMarkup().add(back_btn("sec:admin")),
               origin_msg=c.message)


# ================== АДМИН: РАССЫЛКА ==================
@bot.callback_query_handler(func=lambda c: c.data == "adm:broadcast")
def cb_adm_broadcast(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    bot.answer_callback_query(c.id)
    msg = bot.send_message(
        c.message.chat.id,
        "📣 Пришли сообщение для рассылки.\n"
        "Можно текст или одно фото с подписью.\n"
        "Отправь сейчас одним сообщением."
    )
    bot.register_next_step_handler(msg, admin_do_broadcast)


def admin_do_broadcast(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    users = db_exec("SELECT user_id FROM users", fetchall=True)
    uids = [int(u["user_id"]) for u in users]

    sent = 0
    fails = 0

    for uid in uids:
        try:
            if message.photo:
                file_id = message.photo[-1].file_id
                caption = message.caption or ""
                bot.send_photo(uid, file_id, caption=caption)
            else:
                text = message.text or ""
                if not text.strip():
                    continue
                bot.send_message(uid, text)

            sent += 1
            time.sleep(0.05)
        except:
            fails += 1

    bot.reply_to(
        message,
        f"✅ Рассылка завершена.\nОтправлено: {sent}\nОшибок: {fails}"
    )


# ================== АДМИН: ИНВАЙТ НА ОТЗЫВ (ПО @USERNAME) ==================
@bot.callback_query_handler(func=lambda c: c.data == "adm:review_invite")
def cb_adm_review_invite(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    bot.answer_callback_query(c.id)
    msg = bot.send_message(
        c.message.chat.id,
        "Введи @username пользователя для инвайта на отзыв.\n"
        "Пример: <code>@someuser</code>"
    )
    bot.register_next_step_handler(msg, admin_send_review_invite)


def admin_send_review_invite(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    raw = (message.text or "").strip()
    if not raw:
        bot.reply_to(message, "Пусто. Введи @username.")
        return

    if raw.startswith("@"):
        username = raw[1:]
    else:
        username = raw

    if username.isdigit():
        uid = int(username)
    else:
        try:
            chat = bot.get_chat(username)
            uid = chat.id
        except Exception as e:
            bot.reply_to(message, f"Не нашёл пользователя @{username}. Ошибка: {e}")
            return

    db_exec(
        "INSERT INTO review_invites(user_id,invited_at,used) VALUES(?,?,0) "
        "ON CONFLICT(user_id) DO UPDATE SET invited_at=excluded.invited_at, used=0",
        (uid, datetime.utcnow().isoformat()),
    )

    invite_text = (
        "✍️ Админ приглашает тебя оставить отзыв.\n\n"
        "Напиши одним сообщением:\n"
        "• что понравилось/не понравилось\n"
        "• как сидит, размер\n"
        "• качество ткани/принта\n"
        "• можно фото (альбом)\n\n"
        "Как отправишь — я передам админу на модерацию ✅"
    )

    try:
        bot.send_message(uid, invite_text)
        bot.reply_to(message, f"✅ Инвайт отправлен пользователю @{username} (id {uid}).")
    except Exception as e:
        bot.reply_to(message, f"Не смог отправить инвайт: {e}")


# ================== АДМИН: НЕПРИНЯТЫЕ ОТЗЫВЫ ==================
@bot.callback_query_handler(func=lambda c: c.data == "adm:reviews_pending")
def cb_adm_reviews_pending(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    bot.answer_callback_query(c.id)

    rows = get_pending_reviews()
    if not rows:
        smart_send(
            c.message.chat.id,
            "Непринятых отзывов нет ✅",
            types.InlineKeyboardMarkup().add(back_btn("sec:admin")),
            origin_msg=c.message
        )
        return

    for r in rows:
        photos = json.loads(r["photos_json"]) if r["photos_json"] else []
        txt = (r["text"] or "").strip()
        caption = (
            f"📝 <b>Отзыв #{r['id']}</b>\n"
            f"От пользователя: <code>{r['user_id']}</code>\n\n"
            f"{txt}"
        )

        if photos:
            media = [InputMediaPhoto(pid) for pid in photos[:10]]
            media[-1].caption = caption
            media[-1].parse_mode = "HTML"
            bot.send_media_group(c.message.chat.id, media)
            bot.send_message(c.message.chat.id, "Модерация:", reply_markup=review_pending_kb(r["id"]))
        else:
            bot.send_message(c.message.chat.id, caption, reply_markup=review_pending_kb(r["id"]))


@bot.callback_query_handler(func=lambda c: c.data.startswith("revapp:"))
def cb_review_approve(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    rid = int(c.data.split(":", 1)[1])
    approve_review(rid)
    bot.answer_callback_query(c.id, "✅ Принято")
    bot.send_message(c.message.chat.id, f"Отзыв #{rid} принят ✅",
                     reply_markup=types.InlineKeyboardMarkup().add(back_btn("sec:admin")))

    r = db_exec("SELECT * FROM reviews WHERE id=?", (rid,), fetchone=True)
    if r:
        try:
            bonus_code = create_review_bonus_promo(r["user_id"], rid)
            bot.send_message(
                r["user_id"],
                "🎁 Спасибо за отзыв! Дарю промокод на 5% (одноразовый):\n"
                f"<code>{bonus_code}</code>\n\n"
                "Введи его в меню «Промокод» — применится при следующей покупке."
            )
        except Exception as e:
            print("bonus promo send fail:", e)

        try:
            bot.send_message(r["user_id"], "✅ Твой отзыв опубликован. Спасибо!")
        except:
            pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("revrej:"))
def cb_review_reject(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    rid = int(c.data.split(":", 1)[1])
    r = db_exec("SELECT * FROM reviews WHERE id=?", (rid,), fetchone=True)
    reject_review(rid)
    bot.answer_callback_query(c.id, "❌ Отклонено")
    bot.send_message(c.message.chat.id, f"Отзыв #{rid} отклонён ❌",
                     reply_markup=types.InlineKeyboardMarkup().add(back_btn("sec:admin")))
    if r:
        try:
            bot.send_message(r["user_id"], "❌ Твой отзыв отклонён админом.")
        except:
            pass


# ================== ПРИЁМ ОТЗЫВОВ (ТОЛЬКО ПО ИНВАЙТУ) ==================
MG_CACHE: Dict[str, Dict] = {}

@bot.message_handler(content_types=["photo"], func=lambda m: m.from_user and m.from_user.id != ADMIN_ID)
def user_review_photo_or_album(message: types.Message):
    inv = db_exec("SELECT * FROM review_invites WHERE user_id=?", (message.from_user.id,), fetchone=True)
    if not inv or inv["used"] == 1:
        return

    if message.media_group_id:
        MG_CACHE.setdefault(
            message.media_group_id,
            {"user_id": message.from_user.id, "chat_id": message.chat.id, "photos": [], "caption": ""}
        )
        MG_CACHE[message.media_group_id]["photos"].append(message.photo[-1].file_id)
        if message.caption:
            MG_CACHE[message.media_group_id]["caption"] = message.caption
        return

    photos = [message.photo[-1].file_id]
    text = (message.caption or "").strip() or "Без текста"
    _save_user_review(message.from_user.id, text, photos, message.chat.id)


@bot.message_handler(content_types=["text"], func=lambda m: m.from_user and m.from_user.id != ADMIN_ID)
def user_review_text_only(message: types.Message):
    inv = db_exec("SELECT * FROM review_invites WHERE user_id=?", (message.from_user.id,), fetchone=True)
    if not inv or inv["used"] == 1:
        return

    text = (message.text or "").strip()
    if not text:
        return
    _save_user_review(message.from_user.id, text, [], message.chat.id)


def _save_user_review(user_id: int, text: str, photos: List[str], chat_id: int):
    db_exec(
        "INSERT INTO reviews(user_id,text,photos_json,is_approved,created_at) VALUES(?,?,?,?,?)",
        (user_id, text, json.dumps(photos), 0, datetime.utcnow().isoformat())
    )
    db_exec("UPDATE review_invites SET used=1 WHERE user_id=?", (user_id,))

    bot.send_message(chat_id, "✅ Спасибо! Отзыв отправлен админу на модерацию.",
                     reply_markup=types.InlineKeyboardMarkup().add(back_btn("sec:menu")))

    rid = db_exec("SELECT id FROM reviews ORDER BY id DESC LIMIT 1", fetchone=True)["id"]
    adm_caption = (
        f"🆕 <b>Новый отзыв #{rid}</b>\n"
        f"От пользователя: <code>{user_id}</code>\n\n"
        f"{text}"
    )
    if photos:
        media = [InputMediaPhoto(pid) for pid in photos[:10]]
        media[-1].caption = adm_caption
        media[-1].parse_mode = "HTML"
        bot.send_media_group(ADMIN_ID, media)
        bot.send_message(ADMIN_ID, "Модерация:", reply_markup=review_pending_kb(rid))
    else:
        bot.send_message(ADMIN_ID, adm_caption, reply_markup=review_pending_kb(rid))


# ================== FLUSH АЛЬБОМОВ (ПОЧИНЕННЫЙ) ==================
@bot.message_handler(content_types=["text", "photo", "video", "document", "audio", "voice", "sticker"])
def media_group_flush(message: types.Message):
    if message.from_user and message.from_user.id == ADMIN_ID:
        if ADMIN_ID in ADMIN_WAITING_FLUSH and not message.media_group_id and message.content_type == "text":
            mg_id = ADMIN_WAITING_FLUSH.pop(ADMIN_ID, None)
            if mg_id and mg_id in ADMIN_IMPORT_CACHE:
                data = ADMIN_IMPORT_CACHE.pop(mg_id)
                _finalize_admin_import(message.chat.id, data.get("caption", ""), data.get("photos", []))

    if message.media_group_id:
        return

    if not MG_CACHE:
        return

    uid_sender = message.from_user.id if message.from_user else None
    if not uid_sender:
        return

    done = []
    for mg_id, data in list(MG_CACHE.items()):
        if data["photos"] and data["user_id"] == uid_sender:
            inv = db_exec("SELECT * FROM review_invites WHERE user_id=?", (uid_sender,), fetchone=True)
            if inv and inv["used"] == 0:
                text = (data.get("caption") or "").strip() or "Без текста"
                _save_user_review(uid_sender, text, data["photos"], data.get("chat_id", uid_sender))
            done.append(mg_id)

    for mg_id in done:
        MG_CACHE.pop(mg_id, None)


# ================== АДМИН: СТАТИСТИКА ==================
@bot.callback_query_handler(func=lambda c: c.data == "adm:stats")
def cb_adm_stats(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        bot.answer_callback_query(c.id, "Нет доступа.")
        return
    bot.answer_callback_query(c.id)

    users = db_exec("SELECT COUNT(*) AS c FROM users", fetchone=True)["c"]
    prods = db_exec("SELECT COUNT(*) AS c FROM products", fetchone=True)["c"]
    orders = db_exec("SELECT COUNT(*) AS c FROM orders", fetchone=True)["c"]

    revenue = db_exec(
        "SELECT SUM(final_total) AS s FROM orders WHERE status='подтверждён'",
        fetchone=True
    )["s"] or 0

    promo_totals = db_exec(
        "SELECT SUM(used) AS u, SUM(confirmed_uses) AS c FROM promo_codes",
        fetchone=True
    )
    used_sum = promo_totals["u"] or 0
    conf_sum = promo_totals["c"] or 0

    text = (
        "<b>📊 Статистика:</b>\n\n"
        f"👥 Пользователей: <b>{users}</b>\n"
        f"🧥 Товаров: <b>{prods}</b>\n"
        f"🧾 Заказов: <b>{orders}</b>\n"
        f"💰 Подтверждённая выручка: <b>{revenue}{CURRENCY}</b>\n\n"
        "<b>Промокоды:</b>\n"
        f"Использовано при оформлении: <b>{used_sum}</b>\n"
        f"Подтверждённых покупок: <b>{conf_sum}</b>\n"
    )

    smart_send(
        c.message.chat.id,
        text,
        types.InlineKeyboardMarkup().add(back_btn("sec:admin")),
        origin_msg=c.message
    )


# ================== ФОЛЛБЭК ==================
@bot.message_handler(content_types=["text"])
def fallback(message: types.Message):
    if message.text.startswith("/"):
        return
    if message.from_user and message.from_user.username:
        update_username(message.from_user.id, message.from_user.username)
    bot.send_message(message.chat.id, "Нажми меню ниже 👇", reply_markup=main_menu(message.from_user.id))


# ================== RUN ==================
if __name__ == "__main__":
    init_db()
    ensure_columns()
    me = bot.get_me()
    print(f"✅ INKO SHOP Bot is running as @{me.username} (id {me.id})")
    bot.remove_webhook()
    bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
