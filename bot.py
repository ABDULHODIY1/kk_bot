
"""
Pro kino-bot — FSM + Inline Admin Panel (aiogram 3.7+)
Faqat /start komandasi, barcha admin funksiyalari inline tugmalar orqali
Universal Broadcast: Istalgan formatdagi xabarni yuborish
"""
import os
import re
import time
import asyncio
import logging
from typing import Optional, Dict, Any, Tuple, List
from contextlib import asynccontextmanager
from io import BytesIO
from dotenv import load_dotenv, find_dotenv

print("=" * 60)
print("ENVIRONMENT LOADING DEBUG")
print("=" * 60)
dotenv_path = find_dotenv(usecwd=True)
if dotenv_path:
    print(f"✅ .env fayl topildi: {dotenv_path}")
    load_dotenv(dotenv_path, override=True, verbose=True)
else:
    print("⚠️ .env fayl topilmadi, environment variables ishlatiladi")
    load_dotenv(verbose=True)

BOT_TOKEN_DEBUG = os.getenv("BOT_TOKEN", "")
print(f"BOT_TOKEN loaded: {'✅ YES' if BOT_TOKEN_DEBUG else '❌ NO'}")
print(f"ADMIN_IDS: {os.getenv('ADMIN_IDS', '❌ Not set')}")
print(f"DB_PATH: {os.getenv('DB_PATH', 'bot.db')}")
print(f"Current working directory: {os.getcwd()}")
print(f"Script location: {os.path.dirname(os.path.abspath(__file__))}")
print("=" * 60)

try:
    import uvloop
    uvloop.install()
except Exception:
    pass

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, Chat, BotCommand
)
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
USE_TELETHON = False
try:
    TELETHON_SESSION = os.getenv("TELETHON_SESSION", "").strip()
    TG_API_ID = int(os.getenv("TG_API_ID", "0") or 0)
    TG_API_HASH = os.getenv("TG_API_HASH", "").strip()
    if TELETHON_SESSION and TG_API_ID and TG_API_HASH:
        from telethon import TelegramClient  # type: ignore
        USE_TELETHON = True
except Exception:
    USE_TELETHON = False

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN kerak — .env ga qo'ying")

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().lstrip("-").isdigit()]
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS kerak — .env ga qo'ying")

MOVIE_CHANNEL_ID_RAW = os.getenv("MOVIE_CHANNEL_ID", "").strip()
if MOVIE_CHANNEL_ID_RAW == "":
    MOVIE_CHANNEL_ID: Optional[int] = None
else:
    try:
        MOVIE_CHANNEL_ID = int(MOVIE_CHANNEL_ID_RAW)
    except Exception:
        MOVIE_CHANNEL_ID = None

DB_PATH = os.getenv("DB_PATH", "bot.db")
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))
BROADCAST_CONCURRENT = int(os.getenv("BROADCAST_CONCURRENT", "20"))
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s: %(message)s")
logger = logging.getLogger("kino_bot")


bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class BroadcastStates(StatesGroup):
    waiting_for_content = State()
    waiting_for_confirm = State()  

class BanStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_reason = State()
    waiting_for_confirm = State()

class EditTitleStates(StatesGroup):
    waiting_for_code = State()
    waiting_for_new_title = State()

class DeleteCodeStates(StatesGroup):
    waiting_for_code = State()
    waiting_for_confirm = State()

class AddChannelStates(StatesGroup):
    waiting_for_channel_id = State()
    waiting_for_channel_url = State()

class AddAdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_confirm = State()

scan_sessions: Dict[int, float] = {}
sub_cache: Dict[int, bool] = {}  

db: Optional[aiosqlite.Connection] = None

async def init_db():
    global db, ADMIN_IDS  
    db = await aiosqlite.connect(DB_PATH)
    await db.execute("PRAGMA journal_mode=WAL;")
    await db.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        joined_at INTEGER DEFAULT (strftime('%s','now'))
    )
    """)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS movies (
        code TEXT PRIMARY KEY,
        post_id INTEGER NOT NULL,
        channel_id TEXT DEFAULT '',
        title TEXT DEFAULT '',
        saved_at INTEGER DEFAULT (strftime('%s','now')),
        search_count INTEGER DEFAULT 0
    )
    """)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        channel_id TEXT PRIMARY KEY,
        url TEXT NOT NULL
    )
    """)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS banned_users (
        user_id INTEGER PRIMARY KEY,
        banned_at INTEGER DEFAULT (strftime('%s','now')),
        reason TEXT DEFAULT ''
    )
    """)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        added_at INTEGER DEFAULT (strftime('%s','now'))
    )
    """)
    await db.execute("""
    CREATE TABLE IF NOT EXISTS searches (
        code TEXT,
        user_id INTEGER,
        searched_at INTEGER DEFAULT (strftime('%s','now'))
    )
    """)
    await db.commit()
    try:
        await db.execute("CREATE INDEX IF NOT EXISTS idx_movies_searchcount ON movies(search_count);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_movies_channel_post ON movies(channel_id, post_id);")
    except Exception:
        logger.exception("Index creation non-fatal")
    await db.commit()
    
    async with db.execute("SELECT user_id FROM admins") as cur:
        db_admins = [row[0] for row in await cur.fetchall()]
    
    if not db_admins:
        for aid in ADMIN_IDS:
            await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (aid,))
        await db.commit()
        logger.info("Seeded initial admins from ENV to DB")
        async with db.execute("SELECT user_id FROM admins") as cur:
            db_admins = [row[0] for row in await cur.fetchall()]
    
    ADMIN_IDS = db_admins  
    logger.info("Loaded %d admins from database into runtime", len(ADMIN_IDS))
    logger.info("DB ready: %s", DB_PATH)

@asynccontextmanager
async def get_db():
    yield db


def make_markup(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[btn for btn in row] for row in rows])


class SimpleTTLCache:
    def __init__(self, ttl: int = 300, maxsize: int = 5000):
        self.ttl = ttl
        self.maxsize = maxsize
        self.store: Dict[str, Tuple[float, Any]] = {}
    
    def get(self, key: str):
        ent = self.store.get(key)
        if not ent:
            return None
        exp, val = ent
        if time.time() > exp:
            self.store.pop(key, None)
            return None
        return val
    
    def set(self, key: str, value: Any):
        if len(self.store) >= self.maxsize:
            oldest = min(self.store.items(), key=lambda kv: kv[1][0])[0]
            self.store.pop(oldest, None)
        self.store[key] = (time.time() + self.ttl, value)
    
    def delete(self, key: str):
        self.store.pop(key, None)
    
    def clear(self):
        self.store.clear()

code_cache = SimpleTTLCache(ttl=CACHE_TTL, maxsize=5000)


class TokenBucket:
    def __init__(self, rate_per_min: int):
        self.capacity = rate_per_min
        self.tokens: Dict[int, float] = {}
        self.updated: Dict[int, float] = {}
    
    def allow(self, user_id: int) -> bool:
        now = time.time()
        last = self.updated.get(user_id, now)
        tokens = self.tokens.get(user_id, self.capacity)
        tokens = min(self.capacity, tokens + (now - last) * (self.capacity / 60.0))
        if tokens >= 1.0:
            tokens -= 1.0
            self.tokens[user_id] = tokens
            self.updated[user_id] = now
            return True
        else:
            self.tokens[user_id] = tokens
            self.updated[user_id] = now
            return False

rate_limiter = TokenBucket(RATE_LIMIT_PER_MIN)

def is_admin(user_id: int) -> bool:
    """Check if user is admin - uses synced global ADMIN_IDS"""
    return int(user_id) in [int(aid) for aid in ADMIN_IDS]

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional

async def check_subscription(user_id: int) -> Optional[InlineKeyboardMarkup]:
    channels = await get_channels()  

    if not channels:
        return None

    buttons = []
    not_subscribed = False

    for channel_id_or_username, url in channels:

        url = url.strip()
        if url.startswith("@"):
            url = f"https://t.me/{url[1:]}"
        elif not url.startswith("http"):
            url = f"https://t.me/{url}"


        username = channel_id_or_username
        if isinstance(channel_id_or_username, str) and channel_id_or_username.startswith("@"):
            username = channel_id_or_username
        elif isinstance(channel_id_or_username, str) and "t.me/" in channel_id_or_username:
            username = "@" + channel_id_or_username.split("t.me/")[1]

        try:
            member = await bot.get_chat_member(username, user_id)
            if member.status in ["left", "kicked"]:
                not_subscribed = True
                buttons.append([InlineKeyboardButton(text=f"📢 Kanalga obuna bo‘lish", url=url)])
        except Exception:
            not_subscribed = True
            buttons.append([InlineKeyboardButton(text=f"📢 Kanalga obuna bo‘lish", url=url)])

    if not_subscribed:
        buttons.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    return None
def is_scanning(admin_id: int) -> bool:
    """Check if admin is in scan mode with TTL check"""
    expiry = scan_sessions.get(admin_id)
    if expiry is None:
        return False
    if time.time() > expiry:
        scan_sessions.pop(admin_id, None)
        return False
    return True

async def start_scan_for(admin_id: int, minutes: int):
    """Start scan session for admin"""
    expiry = time.time() + max(1, minutes) * 60
    scan_sessions[admin_id] = expiry

def stop_scan_for(admin_id: int):
    """Stop scan session for admin"""
    scan_sessions.pop(admin_id, None)

async def del_later(chat_id: int, msg_id: int, delay: int):
    """Delete message after delay with robust error handling"""
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception as e:
        if "message to delete not found" not in str(e).lower():
            logger.warning("Failed to delete message %s in chat %s: %s", msg_id, chat_id, e)


def fix_channel_url(link: str) -> str:
    link = link.strip()

    if link.startswith("@"):
        return f"https://t.me/{link[1:]}"
    
    if not link.startswith("http"):
        return f"https://t.me/{link}"
    
    return link

async def save_movie(code: str, post_id: int, channel_id: Optional[str] = None, title: str = ""):
    ch = str(channel_id) if channel_id is not None else ""
    try:
        await db.execute(
            "INSERT OR REPLACE INTO movies (code, post_id, channel_id, title, saved_at) VALUES (?, ?, ?, ?, strftime('%s','now'))",
            (code, post_id, ch, title)
        )
        await db.commit()
        code_cache.delete(code)
    except Exception:
        logger.exception("save_movie failed for code=%s", code)

async def get_movie_by_code(code: str) -> Optional[dict]:
    cached = code_cache.get(code)
    if cached is not None:
        return cached if cached else None 
    
    async with db.execute("SELECT code, post_id, channel_id, title, search_count FROM movies WHERE code = ?", (code,)) as cur:
        row = await cur.fetchone()
        if not row:
            code_cache.set(code, None)
            return None
        res = {"code": row[0], "post_id": row[1], "channel_id": row[2], "title": row[3], "search_count": row[4]}
        code_cache.set(code, res)
        return res

async def incr_search_count(code: str, user_id: Optional[int] = None):
    try:
        await db.execute("UPDATE movies SET search_count = search_count + 1 WHERE code = ?", (code,))
        if user_id:
            await db.execute("INSERT INTO searches (code, user_id) VALUES (?, ?)", (code, user_id))
        await db.commit()
    except Exception:
        logger.exception("search_count update failed")

async def get_channels():
    async with db.execute("SELECT channel_id, url FROM channels") as cur:
        return await cur.fetchall()

async def get_movie_page(offset: int = 0, limit: int = 20):
    async with db.execute("SELECT code, title, channel_id, post_id, saved_at FROM movies ORDER BY saved_at DESC LIMIT ? OFFSET ?", (limit, offset)) as cur:
        return await cur.fetchall()

async def is_banned(user_id: int) -> bool:
    async with db.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
        return bool(row)

async def ban_user_db(user_id: int, reason: str = ""):
    await db.execute("INSERT OR REPLACE INTO banned_users (user_id, banned_at, reason) VALUES (?, strftime('%s','now'), ?)", (user_id, reason))
    await db.commit()

async def unban_user_db(user_id: int):
    await db.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
    await db.commit()

async def add_admin_db(user_id: int):
    """Add admin to DB and sync global list"""
    user_id_int = int(user_id)
    if user_id_int not in ADMIN_IDS:
        await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id_int,))
        await db.commit()
        ADMIN_IDS.append(user_id_int)  
        logger.info("Added admin %s to runtime and DB", user_id_int)

async def remove_admin_db(user_id: int):
    """Remove admin from DB and sync global list"""
    user_id_int = int(user_id)
    await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id_int,))
    await db.commit()
    try:
        ADMIN_IDS.remove(user_id_int)
        logger.info("Removed admin %s from runtime and DB", user_id_int)
    except ValueError:
        pass  

async def notify_admins_new_code(code: str, channel_obj: Optional[Chat], post_id: int, title: str = ""):
    link = None
    if channel_obj:
        try:
            if getattr(channel_obj, "username", None):
                link = f"https://t.me/{channel_obj.username}/{post_id}"
            else:
                cid = getattr(channel_obj, "id", None)
                if isinstance(cid, int) and cid < 0:
                    link = f"https://t.me/c/{abs(cid)}/{post_id}"
        except Exception:
            logger.exception("Failed to generate channel link")
    
    text = f"🎯 <b>Yangi kino kodi</b>\n<b>Kod:</b> {code}\n"
    if title:
        text += f"<b>Nomi:</b> {title}\n"
    text += f"<b>Post:</b> {link or 'Noma\'lum (bot kanalga admin bo\'lishi yoki kanal username kerak)'}"
    
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=aid, text=text)
        except Exception as e:
            if "bot was blocked" not in str(e).lower() and "user is deactivated" not in str(e).lower():
                logger.exception("notify_admins send failed to %s", aid)

def only_code_kb(admin: bool = False) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🍿 Kino kodini yubor", callback_data="noop")]
    ]
    if admin:
        rows.append([InlineKeyboardButton(text="⚙️ Admin Panel", callback_data="open_admin")])
    return make_markup(rows)

def admin_main_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats"),
         InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="admin_list_users:0")],
        [InlineKeyboardButton(text="⛔ Ban qilish", callback_data="admin_ban"),
         InlineKeyboardButton(text="✅ Bandan chiqarish", callback_data="admin_unban")],
        [InlineKeyboardButton(text="🗑️ Kod o'chirish", callback_data="admin_delcode"),
         InlineKeyboardButton(text="✏️ Nomi tahrirlash", callback_data="admin_edittitle")],
        [InlineKeyboardButton(text="🔥 Top kodlar", callback_data="admin_top_codes")],
        [InlineKeyboardButton(text="📤 Reklama yuborish", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton(text="🧹 Cache tozalash", callback_data="admin_clear_cache"),
         InlineKeyboardButton(text="💾 DB backup", callback_data="admin_backup")],
        [InlineKeyboardButton(text="➕ Admin qo'shish", callback_data="admin_addadmin"),
         InlineKeyboardButton(text="➖ Admin o'chirish", callback_data="admin_removeadmin")],
        [InlineKeyboardButton(text="📺 Kanal qo'shish", callback_data="admin_addchannel"),
         InlineKeyboardButton(text="🗑️ Kanallarni tozalash", callback_data="admin_clearchannels")],
    ]
    return make_markup(rows)

def back_kb(callback_str: str = "open_admin") -> InlineKeyboardMarkup:
    return make_markup([[InlineKeyboardButton(text="🔙 Orqaga", callback_data=callback_str)]])

async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if await is_banned(message.from_user.id):
        await message.reply("Siz bloklangan ekansiz.")
        return
    if not rate_limiter.allow(message.from_user.id):
        await message.reply("Sekinroq — keyinroq urin 😅")
        return
    try:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,))
        await db.commit()
    except Exception:
        logger.exception("user save failed")
    
    sub_kb = await check_subscription(message.from_user.id)
    admin_flag = is_admin(message.from_user.id)
    if sub_kb:
        await message.answer("Kanallarga obuna bo'ling:", reply_markup=sub_kb)
    else:
        if admin_flag:
            await message.answer(
                "🍿 Salom! Faqat kino kodini yuboring (faqat raqamlar).\n"
                "⚙️ Admin panelni ochish uchun tugmani bosing.",
                reply_markup=only_code_kb(admin=True)
            )
        else:
            await message.answer(
                "🍿 Salom! Faqat kino kodini yuboring (faqat raqamlar).",
                reply_markup=only_code_kb(admin=False)
            )

async def cb_check_sub(call: CallbackQuery):
    sub_kb = await check_subscription(call.from_user.id)

    if sub_kb:
        await call.answer("❌ Avval kanallarga obuna bo‘ling!", show_alert=True)
        return

    try:
        await call.message.delete()
    except:
        pass

    await call.message.answer(
        "✅ Rahmat! Endi kino kodini yuborishingiz mumkin.",
        reply_markup=only_code_kb(admin=is_admin(call.from_user.id))
    )

    await call.answer()
async def handle_code(message: Message):
    if await is_banned(message.from_user.id):
        await message.reply("Siz bloklangan ekansiz.")
        return
    if not (message.text and re.fullmatch(r'\d+', message.text.strip())):
        return
    if not rate_limiter.allow(message.from_user.id):
        await message.reply("Ko'p so'rov — biroz kuting.")
        return
    sub_kb = await check_subscription(message.from_user.id)
    if sub_kb:
        await message.answer("Iltimos, avval kanallarga obuna bo'ling:", reply_markup=sub_kb)
        return
    code = message.text.strip()
    movie = await get_movie_by_code(code)
    await incr_search_count(code, user_id=message.from_user.id)
    if not movie:
        await message.answer("😔 Bu kod bilan kino topilmadi.")
        return
    try:
        from_chat = None
        if movie["channel_id"] and str(movie["channel_id"]).lstrip("-").isdigit():
            from_chat = int(movie["channel_id"])
        elif MOVIE_CHANNEL_ID is not None:
            from_chat = MOVIE_CHANNEL_ID
        
        if from_chat is None:
            await message.answer(f"🎬 Kino kodi: <b>{code}</b>\n<b>Nomi:</b> {movie.get('title','')}")
            return
        
        sent = await bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=from_chat,
            message_id=movie["post_id"]
        )
        asyncio.create_task(del_later(message.chat.id, sent.message_id, CACHE_TTL))
    except Exception:
        logger.exception("copy_message failed for code=%s", code)
        await message.answer("Kechirasiz, kino yuborishda xatolik yuz berdi.")

async def fallback_message(message: Message):
    if message.chat and getattr(message.chat, "type", None) == "channel":
        return
    if await is_banned(message.from_user.id):
        return
    if not rate_limiter.allow(message.from_user.id):
        return
    await message.reply("Kodni yubor (faqat raqamlar). Agar ko'rsatma kerak bo'lsa /start yoz.")

async def on_channel_post(message: Message):
    if not message.chat or getattr(message.chat, "type", None) != "channel":
        return
    if not (message.video or message.document or message.photo or message.animation or message.text or message.caption):
        return
    try:
        ch = message.chat
        caption = message.caption or message.text or ""
        m = re.search(r'Kino kodi[:\-]?\s*(\d+)', caption, re.I)
        if m:
            code = m.group(1)
            existing = await get_movie_by_code(code)
            if existing:
                logger.info("Live post code exists: %s", code)
                return
            title = re.sub(r'Kino kodi[:\-]?\s*\d+', '', caption, flags=re.I).strip()
            title = (title.splitlines()[0].strip()[:180]) if title else ""
            await save_movie(code, message.message_id, channel_id=str(ch.id), title=title)
            logger.info("Saved live code=%s from ch=%s post=%s", code, ch.id, message.message_id)
            await notify_admins_new_code(code=code, channel_obj=ch, post_id=message.message_id, title=title)
    except Exception:
        logger.exception("on_channel_post error")

async def on_forwarded_message(message: Message):
    if not message.forward_from_chat:
        return
    admin_id = message.from_user.id
    if not is_admin(admin_id) or not is_scanning(admin_id):
        return
    try:
        origin_chat = message.forward_from_chat
        origin_channel_id = origin_chat.id if origin_chat else None
        origin_msg_id = message.forward_from_message_id or message.message_id
    except Exception:
        origin_channel_id = None
        origin_msg_id = message.message_id
    
    caption = message.caption or (message.text or "")
    m = re.search(r'Kino kodi[:\-]?\s*(\d+)', caption or "", re.I)
    if not m:
        logger.info("Forwarded message (no code) skipped by admin during scan")
        return
    code = m.group(1)
    existing = await get_movie_by_code(code)
    title = re.sub(r'Kino kodi[:\-]?\s*\d+', '', caption, flags=re.I).strip()
    
    if existing:
        existing_ch = str(existing.get("channel_id") or "")
        existing_post = existing.get("post_id")
        msg_ch = str(origin_channel_id) if origin_channel_id is not None else ""
        if existing_ch != msg_ch or existing_post != origin_msg_id:
            await save_movie(code, origin_msg_id, channel_id=msg_ch, title=title)
            logger.info("Updated code=%s with new origin ch=%s post=%s", code, msg_ch, origin_msg_id)
        else:
            logger.info("Forwarded code already exists: %s", code)
        return
    
    await save_movie(code, origin_msg_id, channel_id=str(origin_channel_id) if origin_channel_id is not None else "", title=title)
    try:
        await message.reply(f"✅ Saqlandi: {code}", quote=True)
    except Exception:
        pass
    logger.info("Imported forwarded code=%s from origin ch=%s post=%s", code, origin_channel_id, origin_msg_id)


async def open_admin_panel(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("❌ Bu funksiya faqat adminlar uchun", show_alert=True)
        return
    await state.clear()
    if call.message:
        await call.message.edit_text(
            "⚙️ <b>Admin Panel</b>\bQuyidagi amallardan birini tanlang:",
            reply_markup=admin_main_kb()
        )
    await call.answer()

async def cb_admin_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    async with db.execute("SELECT COUNT(*) FROM users") as cur:
        u = (await cur.fetchone())[0]
    async with db.execute("SELECT COUNT(*) FROM movies") as cur:
        m = (await cur.fetchone())[0]
    async with db.execute("SELECT COUNT(*) FROM banned_users") as cur:
        b = (await cur.fetchone())[0]
    text = (
        f"📊 <b>Statistika</b>\n"
        f"👥 Foydalanuvchilar: <b>{u}</b>\n"
        f"🎬 Kinolar: <b>{m}</b>\n"
        f"⛔ Bloklanganlar: <b>{b}</b>"
    )
    if call.message:
        await call.message.edit_text(text, reply_markup=back_kb())
    await call.answer()

async def admin_list_users(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    data = call.data or ""
    page = 0
    try:
        _, page_s = data.split(":", 1)
        page = int(page_s)
    except Exception:
        page = 0
    
    per_page = 10
    offset = page * per_page
    async with db.execute("SELECT user_id, joined_at FROM users ORDER BY joined_at DESC LIMIT ? OFFSET ?", (per_page, offset)) as cur:
        rows = await cur.fetchall()
        if not rows:
            await call.answer("Foydalanuvchi topilmadi", show_alert=True)
            return
    
    lines = [f"• <code>{r[0]}</code> — {time.strftime('%d.%m.%Y', time.localtime(r[1]))}" for r in rows]
    text = f"👥 <b>Foydalanuvchilar</b> (Sahifa {page + 1})\n" + "\n".join(lines)
    
    async with db.execute("SELECT COUNT(*) FROM users") as cur:
        total = (await cur.fetchone())[0]
    
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"admin_list_users:{page-1}"))
    nav.append(InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"admin_list_users:{page}"))
    if offset + per_page < total:
        nav.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"admin_list_users:{page+1}"))
    nav.append(InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="open_admin"))
    
    if call.message:
        await call.message.edit_text(text, reply_markup=make_markup([nav]))
    await call.answer()

async def cb_admin_broadcast_start(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(BroadcastStates.waiting_for_content)
    if call.message:
        await call.message.edit_text(
            "📤 <b>Reklama yuborish</b>\n"
            "Istalgan formatdagi reklamani yuboring:\n"
            "• Matn\n• Rasm\n• Video\n• Fayl\n• Audio\n• Dokument\n"
            "<i>Yoki bekor qilish uchun /cancel yozing</i>",
            reply_markup=back_kb("open_admin")
        )
    await call.answer()

async def broadcast_content_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    broadcast_data = {
        "message_id": message.message_id,
        "chat_id": message.chat.id
    }
    await state.update_data(broadcast_data=broadcast_data)
    await state.set_state(BroadcastStates.waiting_for_confirm)
    await message.answer(
        "📤 <b>Reklama tayyor</b>\n"
        "Reklamani barcha foydalanuvchilarga yuborishni xohlaysizmi?",
        reply_markup=make_markup([
            [InlineKeyboardButton(text="✅ Ha, yuborish", callback_data="broadcast_confirm:yes"),
             InlineKeyboardButton(text="❌ Yo'q, bekor qilish", callback_data="broadcast_confirm:no")]
        ])
    )

async def cb_broadcast_confirm(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    
    parts = call.data.split(":", 1)
    if len(parts) < 2:
        await call.answer("Xatolik: Noto'g'ri ma'lumot", show_alert=True)
        return
    choice = parts[1]
    
    if choice == "no":
        await state.clear()
        if call.message:
            await call.message.edit_text("❌ Reklama bekor qilindi.", reply_markup=back_kb())
        await call.answer()
        return
    
    data = await state.get_data()
    broadcast_data = data.get("broadcast_data")
    if not broadcast_data:
        await call.answer("Xatolik yuz berdi", show_alert=True)
        await state.clear()
        return

    if call.message:
        await call.message.edit_text("📤 Reklama yuborilmoqda...")
    
    async with db.execute("SELECT user_id FROM users") as cur:
        rows = await cur.fetchall()
        users = [r[0] for r in rows]
    
    sem = asyncio.Semaphore(BROADCAST_CONCURRENT)
    lock = asyncio.Lock()
    sent = 0
    failed = 0
    
    async def send_to_user(uid):
        nonlocal sent, failed
        try:
            async with sem:
                await bot.copy_message(
                    chat_id=uid,
                    from_chat_id=broadcast_data["chat_id"],
                    message_id=broadcast_data["message_id"]
                )
                async with lock:
                    sent += 1
        except Exception as e:
            if "bot was blocked" not in str(e).lower() and "user is deactivated" not in str(e).lower():
                logger.warning("Broadcast failed to user %s: %s", uid, e)
            async with lock:
                failed += 1
    
    tasks = [asyncio.create_task(send_to_user(u)) for u in users]
    await asyncio.gather(*tasks, return_exceptions=True)
    
    await state.clear()
    result_text = (
        f"✅ <b>Reklama yuborildi!</b>\n"
        f"📤 Yuborildi: <b>{sent}</b>\n"
        f"❌ Xatolik: <b>{failed}</b>\n"
        f"👥 Jami: <b>{len(users)}</b>"
    )
    if call.message:
        try:
            await call.message.edit_text(result_text, reply_markup=back_kb())
        except Exception:
            try:
                await call.message.answer(result_text, reply_markup=back_kb())
            except Exception:
                logger.exception("Failed to send broadcast result")
    await call.answer()

async def cmd_cancel(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return
    await state.clear()
    await message.answer("❌ Amal bekor qilindi.", reply_markup=back_kb())
async def cb_admin_ban(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(BanStates.waiting_for_user_id)
    if call.message:
        await call.message.edit_text(
            "⛔ <b>Ban qilish</b>\nFoydalanuvchi ID sini yuboring:",
            reply_markup=back_kb()
        )
    await call.answer()

async def ban_user_id_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri ID. Faqat raqamlar yuboring.", reply_markup=back_kb())
        return
    await state.update_data(ban_user_id=user_id)
    await state.set_state(BanStates.waiting_for_reason)
    await message.answer(
        f"👤 Foydalanuvchi ID: <code>{user_id}</code>\n"
        f"Sababni yozing (yoki 'yo'q' deb yozing):",
        reply_markup=make_markup([
            [InlineKeyboardButton(text="⏭️ Sababsiz davom etish", callback_data="ban_skip_reason")]
        ])
    )
async def cb_ban_skip_reason(call: CallbackQuery, state: FSMContext):
    await state.update_data(ban_reason="")
    await state.set_state(BanStates.waiting_for_confirm)
    data = await state.get_data()
    user_id = data.get("ban_user_id")
    if call.message:
        await call.message.edit_text(
            f"⛔ <b>Ban tasdiqlash</b>\n"
            f"👤 Foydalanuvchi: <code>{user_id}</code>\n"
            f"📝 Sabab: Yo'q\n"
            f"Ban qilishni xohlaysizmi?",
            reply_markup=make_markup([
                [InlineKeyboardButton(text="✅ Ha, ban qilish", callback_data="ban_confirm:yes"),
                 InlineKeyboardButton(text="❌ Yo'q", callback_data="ban_confirm:no")]
            ])
        )
    await call.answer()

async def ban_reason_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    reason = message.text.strip() if message.text.strip().lower() != "yo'q" else ""
    await state.update_data(ban_reason=reason)
    await state.set_state(BanStates.waiting_for_confirm)
    data = await state.get_data()
    user_id = data.get("ban_user_id")
    await message.answer(
        f"⛔ <b>Ban tasdiqlash</b>\n"
        f"👤 Foydalanuvchi: <code>{user_id}</code>\n"
        f"📝 Sabab: {reason or 'Yo\'q'}\n"
        f"Ban qilishni xohlaysizmi?",
        reply_markup=make_markup([
            [InlineKeyboardButton(text="✅ Ha, ban qilish", callback_data="ban_confirm:yes"),
             InlineKeyboardButton(text="❌ Yo'q", callback_data="ban_confirm:no")]
        ])
    )

async def cb_ban_confirm(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    
    parts = call.data.split(":", 1)
    if len(parts) < 2:
        await call.answer("Xatolik: Noto'g'ri ma'lumot", show_alert=True)
        return
    choice = parts[1]
    
    if choice == "no":
        await state.clear()
        if call.message:
            await call.message.edit_text("❌ Ban bekor qilindi.", reply_markup=back_kb())
        await call.answer()
        return
    
    data = await state.get_data()
    user_id = data.get("ban_user_id")
    reason = data.get("ban_reason", "")
    if user_id is None:
        await call.answer("Xatolik: Foydalanuvchi ID topilmadi", show_alert=True)
        await state.clear()
        return
    
    await ban_user_db(user_id, reason)
    await state.clear()
    if call.message:
        await call.message.edit_text(
            f"✅ Foydalanuvchi <code>{user_id}</code> muvaffaqiyatli ban qilindi!\n"
            f"📝 Sabab: {reason or 'Yo\'q'}",
            reply_markup=back_kb()
        )
    await call.answer()

async def cb_admin_unban(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(BanStates.waiting_for_user_id)
    if call.message:
        await call.message.edit_text(
            "✅ <b>Bandan chiqarish</b>\nFoydalanuvchi ID sini yuboring:",
            reply_markup=back_kb()
        )
    await call.answer()

async def unban_user_id_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri ID. Faqat raqamlar yuboring.", reply_markup=back_kb())
        return
    await unban_user_db(user_id)
    await state.clear()
    await message.answer(
        f"✅ Foydalanuvchi <code>{user_id}</code> bandan chiqarildi!",
        reply_markup=back_kb()
    )


async def cb_admin_delcode(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(DeleteCodeStates.waiting_for_code)
    if call.message:
        await call.message.edit_text(
            "🗑️ <b>Kodni o'chirish</b>\nO'chirmoqchi bo'lgan kino kodini yuboring:",
            reply_markup=back_kb()
        )
    await call.answer()

async def delcode_code_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    code = message.text.strip()
    await state.update_data(delcode_code=code)
    await state.set_state(DeleteCodeStates.waiting_for_confirm)
    await message.answer(
        f"🗑️ <b>Kodni o'chirish tasdiqlash</b>\n"
        f"Kod: <code>{code}</code>\n"
        f"O'chirishni xohlaysizmi?",
        reply_markup=make_markup([
            [InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data="delcode_confirm:yes"),
             InlineKeyboardButton(text="❌ Yo'q", callback_data="delcode_confirm:no")]
        ])
    )

async def cb_delcode_confirm(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    
    parts = call.data.split(":", 1)
    if len(parts) < 2:
        await call.answer("Xatolik: Noto'g'ri ma'lumot", show_alert=True)
        return
    choice = parts[1]
    
    if choice == "no":
        await state.clear()
        if call.message:
            await call.message.edit_text("❌ O'chirish bekor qilindi.", reply_markup=back_kb())
        await call.answer()
        return
    
    data = await state.get_data()
    code = data.get("delcode_code")
    if not code:
        await call.answer("Xatolik: Kod topilmadi", show_alert=True)
        await state.clear()
        return
    
    await db.execute("DELETE FROM movies WHERE code = ?", (code,))
    await db.commit()
    code_cache.delete(code)
    await state.clear()
    if call.message:
        await call.message.edit_text(
            f"✅ Kod <code>{code}</code> muvaffaqiyatli o'chirildi!",
            reply_markup=back_kb()
        )
    await call.answer()


async def cb_admin_edittitle(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(EditTitleStates.waiting_for_code)
    if call.message:
        await call.message.edit_text(
            "✏️ <b>Nomi tahrirlash</b>\nKino kodini yuboring:",
            reply_markup=back_kb()
        )
    await call.answer()

async def edittitle_code_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    code = message.text.strip()
    movie = await get_movie_by_code(code)
    if not movie:
        await message.answer("❌ Bunday kod topilmadi.", reply_markup=back_kb())
        return
    await state.update_data(edittitle_code=code)
    await state.set_state(EditTitleStates.waiting_for_new_title)
    await message.answer(
        f"✏️ <b>Yangi nom</b>\n"
        f"Joriy nom: {movie.get('title', 'Yo\'q')}\n"
        f"Yangi nomni yuboring:",
        reply_markup=back_kb()
    )

async def edittitle_new_title_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    new_title = message.text.strip()
    data = await state.get_data()
    code = data.get("edittitle_code")
    if not code:
        await message.answer("Xatolik: Kod topilmadi", reply_markup=back_kb())
        await state.clear()
        return
    
    await db.execute("UPDATE movies SET title = ? WHERE code = ?", (new_title, code))
    await db.commit()
    code_cache.delete(code)
    await state.clear()
    await message.answer(
        f"✅ Kod <code>{code}</code> uchun yangi nom saqlandi!\n"
        f"📝 Yangi nom: {new_title}",
        reply_markup=back_kb()
    )


async def cb_admin_addchannel(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(AddChannelStates.waiting_for_channel_id)
    if call.message:
        await call.message.edit_text(
            "📺 <b>Kanal qo'shish</b>\nKanal ID sini yuboring:\n"
            f"<i>Misol: -1001234567890</i>",
            reply_markup=back_kb()
        )
    await call.answer()

async def addchannel_id_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    channel_id = message.text.strip()
    await state.update_data(addchannel_id=channel_id)
    await state.set_state(AddChannelStates.waiting_for_channel_url)
    await message.answer(
        f"📺 Kanal ID: <code>{channel_id}</code>\n"
        f"Kanal URL sini yuboring:\n"
        f"<i>Misol: https://t.me/example</i>",
        reply_markup=back_kb()
    )

async def addchannel_url_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    url = message.text.strip()
    data = await state.get_data()
    channel_id = data.get("addchannel_id")
    if not channel_id:
        await message.answer("Xatolik: Kanal ID topilmadi", reply_markup=back_kb())
        await state.clear()
        return
    
    await db.execute("INSERT OR REPLACE INTO channels (channel_id, url) VALUES (?, ?)", (channel_id, url))
    await db.commit()
    await state.clear()
    await message.answer(
        f"✅ Kanal qo'shildi!\n"
        f"📺 ID: <code>{channel_id}</code>\n"
        f"🔗 URL: {url}",
        reply_markup=back_kb()
    )


async def cb_admin_clearchannels(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    await db.execute("DELETE FROM channels")
    await db.commit()
    if call.message:
        await call.message.edit_text("✅ Barcha kanallar tozalandi!", reply_markup=back_kb())
    await call.answer()

async def cb_admin_addadmin(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    await state.set_state(AddAdminStates.waiting_for_user_id)
    if call.message:
        await call.message.edit_text(
            "➕ <b>Admin qo'shish</b>\nFoydalanuvchi ID sini yuboring:",
            reply_markup=back_kb()
        )
    await call.answer()

async def addadmin_id_received(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Noto'g'ri ID. Faqat raqamlar yuboring.", reply_markup=back_kb())
        return
    await state.update_data(addadmin_id=user_id)
    await state.set_state(AddAdminStates.waiting_for_confirm)
    await message.answer(
        f"➕ <b>Admin qo'shish tasdiqlash</b>\n"
        f"👤 Foydalanuvchi ID: <code>{user_id}</code>\n"
        f"Admin qilishni xohlaysizmi?",
        reply_markup=make_markup([
            [InlineKeyboardButton(text="✅ Ha, admin qilish", callback_data="addadmin_confirm:yes"),
             InlineKeyboardButton(text="❌ Yo'q", callback_data="addadmin_confirm:no")]
        ])
    )

async def cb_addadmin_confirm(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    
    parts = call.data.split(":", 1)
    if len(parts) < 2:
        await call.answer("Xatolik: Noto'g'ri ma'lumot", show_alert=True)
        return
    choice = parts[1]
    
    if choice == "no":
        await state.clear()
        if call.message:
            await call.message.edit_text("❌ Admin qo'shish bekor qilindi.", reply_markup=back_kb())
        await call.answer()
        return
    
    data = await state.get_data()
    user_id = data.get("addadmin_id")
    if user_id is None:
        await call.answer("Xatolik: Foydalanuvchi ID topilmadi", show_alert=True)
        await state.clear()
        return
    
    await add_admin_db(user_id)
    await state.clear()
    if call.message:
        await call.message.edit_text(
            f"✅ Foydalanuvchi <code>{user_id}</code> admin qilindi!",
            reply_markup=back_kb()
        )
    await call.answer()

async def cb_admin_removeadmin(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    
    current_admin_id = call.from_user.id
    async with db.execute("SELECT user_id FROM admins") as cur:
        rows = await cur.fetchall()
    
    if not rows:
        if call.message:
            await call.message.edit_text("❌ Adminlar topilmadi.", reply_markup=back_kb())
        await call.answer()
        return
    
    buttons = []
    other_admins_exist = False
    for row in rows:
        uid = row[0]
        if uid == current_admin_id:
            continue
        buttons.append([InlineKeyboardButton(text=f"👤 {uid}", callback_data=f"removeadmin:{uid}")])
        other_admins_exist = True
    
    if not other_admins_exist:
        if call.message:
            await call.message.edit_text(
                "⚠️ Boshqa adminlar yo'q. O'zingizni o'chira olmaysiz.",
                reply_markup=back_kb()
            )
        await call.answer()
        return
    
    buttons.append([InlineKeyboardButton(text="🔙 Orqaga", callback_data="open_admin")])
    if call.message:
        await call.message.edit_text(
            "➖ <b>Adminni o'chirish</b>\nO'chirmoqchi bo'lgan adminni tanlang:",
            reply_markup=make_markup(buttons)
        )
    await call.answer()

async def cb_removeadmin_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    
    parts = call.data.split(":", 1)
    if len(parts) < 2:
        await call.answer("Xatolik: Noto'g'ri ma'lumot", show_alert=True)
        return
    try:
        user_id = int(parts[1])
    except ValueError:
        await call.answer("Xatolik: Noto'g'ri foydalanuvchi ID", show_alert=True)
        return
    
    if user_id == call.from_user.id:
        await call.answer("❌ O'zingizni adminlar ro'yxatidan o'chira olmaysiz!", show_alert=True)
        return
    
    await remove_admin_db(user_id)
    if call.message:
        await call.message.edit_text(
            f"✅ Admin <code>{user_id}</code> o'chirildi!",
            reply_markup=back_kb()
        )
    await call.answer()


async def cb_admin_top_codes(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    async with db.execute("SELECT code, search_count FROM movies ORDER BY search_count DESC LIMIT 20") as cur:
        rows = await cur.fetchall()
        if not rows:
            if call.message:
                await call.message.edit_text("❌ Hech narsa topilmadi.", reply_markup=back_kb())
            await call.answer()
            return
    
    lines = [f"{i+1}. <code>{r[0]}</code> — {r[1]} ta" for i, r in enumerate(rows)]
    text = "🔥 <b>Top kodlar</b>\n" + "\n".join(lines)
    if call.message:
        await call.message.edit_text(text, reply_markup=back_kb())
    await call.answer()

async def cb_admin_clear_cache(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    code_cache.clear()
    if call.message:
        await call.message.edit_text("✅ Cache tozalandi!", reply_markup=back_kb())
    await call.answer()

async def cb_admin_backup(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q", show_alert=True)
        return
    if call.message:
        await call.message.edit_text("💾 DB backup tayyorlanmoqda...")
    try:
        await call.message.answer_document(
            FSInputFile(DB_PATH), 
            caption="💾 Database backup (exported at " + time.strftime('%Y-%m-%d %H:%M:%S') + ")",
            reply_markup=back_kb()
        )
    except Exception:
        logger.exception("DB backup failed")
        if call.message:
            await call.message.edit_text("❌ Backup yaratishda xatolik yuz berdi.", reply_markup=back_kb())
    await call.answer()

def register_handlers():
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_cancel, Command(commands=["cancel"]))
    dp.message.register(broadcast_content_received, StateFilter(BroadcastStates.waiting_for_content))
    dp.message.register(ban_user_id_received, StateFilter(BanStates.waiting_for_user_id))
    dp.message.register(ban_reason_received, StateFilter(BanStates.waiting_for_reason))
    dp.message.register(unban_user_id_received, StateFilter(BanStates.waiting_for_user_id))  
    dp.message.register(delcode_code_received, StateFilter(DeleteCodeStates.waiting_for_code))
    dp.message.register(edittitle_code_received, StateFilter(EditTitleStates.waiting_for_code))
    dp.message.register(edittitle_new_title_received, StateFilter(EditTitleStates.waiting_for_new_title))
    dp.message.register(addchannel_id_received, StateFilter(AddChannelStates.waiting_for_channel_id))
    dp.message.register(addchannel_url_received, StateFilter(AddChannelStates.waiting_for_channel_url))
    dp.message.register(addadmin_id_received, StateFilter(AddAdminStates.waiting_for_user_id))
    dp.message.register(on_forwarded_message, 
                       F.forward_from_chat,
                       F.chat.type == "private")  
    dp.message.register(on_channel_post, 
                       F.chat.type == "channel",
                       F.content_type.in_({"video", "document", "photo", "animation", "text"}))
    
    dp.message.register(handle_code, 
                       F.text,
                       ~F.text.startswith("/"),
                       F.text.regexp(r"^\d+$"),
                       F.chat.type == "private")  
    dp.message.register(fallback_message, F.chat.type == "private")
    dp.callback_query.register(open_admin_panel, F.data == "open_admin")
    dp.callback_query.register(cb_admin_stats, F.data == "admin_stats")
    dp.callback_query.register(admin_list_users, F.data.startswith("admin_list_users:"))
    dp.callback_query.register(cb_admin_broadcast_start, F.data == "admin_broadcast_start")
    dp.callback_query.register(cb_broadcast_confirm, F.data.startswith("broadcast_confirm:"))
    dp.callback_query.register(cb_admin_ban, F.data == "admin_ban")
    dp.callback_query.register(cb_ban_skip_reason, F.data == "ban_skip_reason")
    dp.callback_query.register(cb_ban_confirm, F.data.startswith("ban_confirm:"))
    dp.callback_query.register(cb_admin_unban, F.data == "admin_unban")
    dp.callback_query.register(cb_admin_delcode, F.data == "admin_delcode")
    dp.callback_query.register(cb_delcode_confirm, F.data.startswith("delcode_confirm:"))
    dp.callback_query.register(cb_admin_edittitle, F.data == "admin_edittitle")
    dp.callback_query.register(cb_admin_addchannel, F.data == "admin_addchannel")
    dp.callback_query.register(cb_admin_clearchannels, F.data == "admin_clearchannels")
    dp.callback_query.register(cb_admin_addadmin, F.data == "admin_addadmin")
    dp.callback_query.register(cb_addadmin_confirm, F.data.startswith("addadmin_confirm:"))
    dp.callback_query.register(cb_admin_removeadmin, F.data == "admin_removeadmin")
    dp.callback_query.register(cb_removeadmin_confirm, F.data.startswith("removeadmin:"))
    dp.callback_query.register(cb_admin_top_codes, F.data == "admin_top_codes")
    dp.callback_query.register(cb_admin_clear_cache, F.data == "admin_clear_cache")
    dp.callback_query.register(cb_admin_backup, F.data == "admin_backup")
    dp.callback_query.register(cb_check_sub, F.data == "check_sub")

async def main():
    logger.info("DB bilan ulanmoqda...")
    await init_db()
    register_handlers()
    logger.info("Handlers registered successfully with correct priority order")
    logger.info("Bot ishga tushmoqda... (Admins: %s)", ADMIN_IDS)
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_member"])
    finally:
        try:
            if db:
                await db.close()
                logger.info("Database connection closed")
        except Exception:
            logger.exception("Error closing database")
        try:
            await bot.session.close()
        except Exception:
            logger.exception("Error closing bot session")
        logger.info("Bot to'xtadi")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("To'xtatildi (User interrupt)")
    except Exception:
        logger.exception("Fatal error occurred")