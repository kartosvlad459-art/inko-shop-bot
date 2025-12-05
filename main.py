import os
import json
import re
import time
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
log = logging.getLogger("inko-shop-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# URL витрины (GitHub Pages)
SHOP_URL = os.getenv(
    "SHOP_URL",
    "https://kartosvlad459-art.github.io/inko-shop-bot/"
)

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

PRODUCTS_PATH = DATA_DIR / "products.json"
USERS_PATH = DATA_DIR / "users.json"

# Витрина на GitHub Pages берётся из /docs/products.json
DOCS_DIR = ROOT / "docs"
DOCS_DIR.mkdir(exist_ok=True)
WEBAPP_PRODUCTS_PATH = DOCS_DIR / "products.json"

DEFAULT_PRODUCTS: List[Dict[str, Any]] = []
DEFAULT_USERS: Dict[str, Any] = {}

# ---------- storage helpers ----------

def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        log.exception("Failed reading %s", path)
        return default

def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

def load_products() -> List[Dict[str, Any]]:
    return load_json(PRODUCTS_PATH, DEFAULT_PRODUCTS)

def save_products(products: List[Dict[str, Any]]):
    save_json(PRODUCTS_PATH, products)
    # синхроним в docs для витрины
    save_json(WEBAPP_PRODUCTS_PATH, products)

def load_users() -> Dict[str, Any]:
    return load_json(USERS_PATH, DEFAULT_USERS)

def save_users(users: Dict[str, Any]):
    save_json(USERS_PATH, users)

# ---------- parsing helpers ----------

HASHTAG_RE = re.compile(r"#([A-Za-zА-Яа-я0-9_]+)")
PRICE_RE = re.compile(r"(\d[\d\s]{1,10})\s?(₽|р\.?|руб\.?)", re.IGNORECASE)

def extract_hashtags(text: str) -> List[str]:
    tags = [m.group(1).lower() for m in HASHTAG_RE.finditer(text or "")]
    # выкидываем предзаказ
    tags = [t for t in tags if t != "предзаказ"]
    # оставляем только “целые” теги (как ты и говорил)
    return tags

def extract_price(text: str) -> Optional[int]:
    m = PRICE_RE.search(text or "")
    if not m:
        return None
    digits = re.sub(r"\s+", "", m.group(1))
    try:
        return int(digits)
    except:
        return None

def split_title_desc(caption: str) -> (str, str):
    caption = caption or ""
    lines = [l.strip() for l in caption.split("\n") if l.strip()]
    if not lines:
        return "Без названия", ""
    title = lines[0]
    desc = "\n".join(lines[1:]).strip()
    return title, desc

def make_product_id() -> str:
    return str(int(time.time() * 1000))

# ---------- bot UI ----------

def main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("🛍 Открыть витрину", web_app=WebAppInfo(url=SHOP_URL))],
        [
            InlineKeyboardButton("🧺 Корзина", callback_data="cart"),
            InlineKeyboardButton("❤️ Избранное", callback_data="fav"),
        ],
        [InlineKeyboardButton("📦 Мои заказы", callback_data="orders")],
    ]
    if is_admin:
        kb.append([InlineKeyboardButton("➕ Импорт товара (перешли пост)", callback_data="admin_help")])
    return InlineKeyboardMarkup(kb)

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back")]])

# ---------- commands ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_admin = update.effective_user and update.effective_user.id == ADMIN_ID
    await update.message.reply_text(
        "Йо! Это Inko Shop.\nОткрывай витрину кнопкой ниже 👇",
        reply_markup=main_menu_kb(is_admin)
    )

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        "Админка:\n"
        "Чтобы импортнуть товар — просто перешли сюда пост из канала с фото/текстом.\n"
        "Категория берётся из хештегов (#кроссовки и т.д.), кроме #предзаказ.",
        reply_markup=main_menu_kb(True)
    )

# ---------- callbacks ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    users = load_users()
    uid = str(q.from_user.id)
    user = users.get(uid, {"cart": [], "fav": [], "orders": []})
    users[uid] = user

    products = load_products()
    prod_by_id = {p["id"]: p for p in products}

    if data == "back":
        await q.edit_message_text("Меню:", reply_markup=main_menu_kb(q.from_user.id == ADMIN_ID))
        return

    if data == "admin_help":
        await q.edit_message_text(
            "Перешли сюда пост (фото + описание + хештег категории).\n"
            "Пример:\n"
            "Nike Air Max\n"
            "Цена 5900₽\n"
            "#кроссовки\n"
            "#предзаказ (если это предзаказ — он НЕ станет категорией)\n",
            reply_markup=back_kb()
        )
        return

    if data == "cart":
        items = user["cart"]
        if not items:
            await q.edit_message_text("Корзина пустая.", reply_markup=back_kb())
            return
        text = "🧺 Корзина:\n\n"
        total = 0
        for i, pid in enumerate(items, 1):
            p = prod_by_id.get(pid)
            if not p:
                continue
            total += p.get("price") or 0
            text += f"{i}) {p['title']} — {p.get('price','?')}₽\n"
        text += f"\nИтого: {total}₽"
        kb = [[InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout")],
              [InlineKeyboardButton("🗑 Очистить корзину", callback_data="clear_cart")],
              [InlineKeyboardButton("⬅️ Назад", callback_data="back")]]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "fav":
        items = user["fav"]
        if not items:
            await q.edit_message_text("Избранное пустое.", reply_markup=back_kb())
            return
        text = "❤️ Избранное:\n\n"
        for i, pid in enumerate(items, 1):
            p = prod_by_id.get(pid)
            if p:
                text += f"{i}) {p['title']} — {p.get('price','?')}₽\n"
        await q.edit_message_text(text, reply_markup=back_kb())
        return

    if data == "orders":
        orders = user["orders"]
        if not orders:
            await q.edit_message_text("Заказов пока нет.", reply_markup=back_kb())
            return
        text = "📦 Мои заказы:\n\n"
        for i, o in enumerate(orders, 1):
            text += f"{i}) {o['title']} x{o['qty']} — {o.get('price','?')}₽\n"
        await q.edit_message_text(text, reply_markup=back_kb())
        return

    if data == "clear_cart":
        user["cart"] = []
        save_users(users)
        await q.edit_message_text("Корзина очищена.", reply_markup=back_kb())
        return

    if data == "checkout":
        if not user["cart"]:
            await q.edit_message_text("Корзина пустая.", reply_markup=back_kb())
            return
        # превращаем корзину в заказы
        for pid in user["cart"]:
            p = prod_by_id.get(pid)
            if not p:
                continue
            user["orders"].append({
                "id": pid,
                "title": p["title"],
                "price": p.get("price"),
                "qty": 1,
                "ts": int(time.time())
            })
        user["cart"] = []
        save_users(users)
        await q.edit_message_text("✅ Заказ оформлен! Смотри в «Мои заказы».", reply_markup=back_kb())
        return

# ---------- WebApp data from vitrina ----------

async def on_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Ждём JSON от витрины:
    {"action":"add_to_cart","product_id":"..."}
    {"action":"toggle_fav","product_id":"..."}
    """
    if not update.message or not update.message.web_app_data:
        return

    try:
        payload = json.loads(update.message.web_app_data.data)
    except Exception:
        await update.message.reply_text("Не понял данные из витрины 😅")
        return

    action = payload.get("action")
    pid = payload.get("product_id")
    if not pid:
        return

    users = load_users()
    uid = str(update.effective_user.id)
    user = users.get(uid, {"cart": [], "fav": [], "orders": []})
    users[uid] = user

    if action == "add_to_cart":
        user["cart"].append(pid)
        save_users(users)
        await update.message.reply_text("Добавил в корзину ✅", reply_markup=main_menu_kb(uid == str(ADMIN_ID)))

    elif action == "toggle_fav":
        if pid in user["fav"]:
            user["fav"].remove(pid)
            txt = "Убрал из избранного 💔"
        else:
            user["fav"].append(pid)
            txt = "Добавил в избранное ❤️"
        save_users(users)
        await update.message.reply_text(txt, reply_markup=main_menu_kb(uid == str(ADMIN_ID)))

# ---------- admin import ----------

async def on_admin_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Админ пересылает пост — мы делаем из него товар.
    Поддержка:
      - фото + подпись
      - текстовый пост
    Категория = 1й хештег кроме #предзаказ
    """

    if update.effective_user.id != ADMIN_ID:
        return

    msg = update.message
    caption = msg.caption or msg.text or ""
    tags = extract_hashtags(caption)
    category = tags[0] if tags else "без категории"

    title, desc = split_title_desc(caption)
    price = extract_price(caption)

    photos = []
    if msg.photo:
        # берём самую большую
        photos = [msg.photo[-1].file_id]

    products = load_products()
    pid = make_product_id()

    product = {
        "id": pid,
        "title": title,
        "description": desc,
        "price": price,
        "category": category,      # по хештегу
        "hashtags": tags,          # все теги (кроме предзаказ)
        "photos": photos,
        "created_at": int(time.time())
    }
    products.insert(0, product)
    save_products(products)

    await msg.reply_text(
        f"✅ Товар добавлен!\n\n"
        f"Название: {title}\n"
        f"Категория: #{category}\n"
        f"Цена: {price if price else 'не найдена'}\n"
        f"Хештеги: {', '.join(['#'+t for t in tags]) if tags else 'нет'}"
    )

# ---------- run ----------

def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))

    app.add_handler(CallbackQueryHandler(on_callback))

    # данные из витрины
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data))

    # импорт товаров: форвард/сообщение админа
    app.add_handler(MessageHandler(
        (filters.FORWARDED | filters.PHOTO | filters.TEXT) & filters.User(ADMIN_ID),
        on_admin_forward
    ))

    return app

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN env")

    if not PRODUCTS_PATH.exists():
        save_products(DEFAULT_PRODUCTS)

    if not USERS_PATH.exists():
        save_users(DEFAULT_USERS)

    application = build_app()
    log.info("Bot started")
    application.run_polling()
