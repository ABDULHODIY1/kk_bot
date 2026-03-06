import asyncio
import logging
import aiosqlite
import re
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest

# --- SOZLAMALAR ---
BOT_TOKEN = ""
ADMIN_IDS = [1234567890]
MOVIE_CHANNEL_ID = -10012345678 # Raqam ko'rinishida yozgan ma'qul

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- HOLATLAR (FSM) ---
class AdminStates(StatesGroup):
    waiting_for_channel_id = State()
    waiting_for_channel_url = State()
    waiting_for_broadcast = State()

# --- BAZA BILAN ISHLASH ---
async def init_db():
    async with aiosqlite.connect("bot.db") as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
        await db.execute("CREATE TABLE IF NOT EXISTS movies (code TEXT PRIMARY KEY, post_id INTEGER)")
        await db.execute("CREATE TABLE IF NOT EXISTS channels (channel_id TEXT PRIMARY KEY, url TEXT)")
        await db.commit()

# --- KODNI CAPTIONDAN AJRATISH ---
def extract_code_from_caption(caption):
    if not caption:
        return None
    # Template: "🔎 Kino kodi: 123" ni qidiradi
    match = re.search(r'Kino kodi:\s*(\d+)', caption, re.IGNORECASE)
    if match:
        return match.group(1)
    return None

# --- KANAL POSTINI QABUL QILISH (Yangi va Tahrirlangan postlar uchun) ---
@dp.channel_post(F.video | F.document)
@dp.edited_channel_post(F.video | F.document)
async def save_movie_from_channel(message: Message):
    if message.chat.id == MOVIE_CHANNEL_ID:
        code = extract_code_from_caption(message.caption)
        if code:
            post_id = message.message_id
            async with aiosqlite.connect("bot.db") as db:
                await db.execute("INSERT OR REPLACE INTO movies (code, post_id) VALUES (?, ?)", (code, post_id))
                await db.commit()
            logging.info(f"✅ Kino saqlandi: Kod={code}, Post ID={post_id}")

# --- MAJBURIY OBUNA TEKSHIRUVI ---
async def check_subscription(user_id: int) -> InlineKeyboardMarkup | None:
    async with aiosqlite.connect("bot.db") as db:
        async with db.execute("SELECT channel_id, url FROM channels") as cursor:
            channels = await cursor.fetchall()
            
    unsubscribed_channels = []
    for channel_id, url in channels:
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
                unsubscribed_channels.append((channel_id, url))
        except Exception:
            pass # Bot kanalda admin bo'lmasa yoki kanal topilmasa
            
    if not unsubscribed_channels:
        return None
        
    buttons = []
    for idx, (_, url) in enumerate(unsubscribed_channels, 1):
        buttons.append([InlineKeyboardButton(text=f"📢 {idx}-kanalga obuna bo'lish", url=url)])
    buttons.append([InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- FOYDALANUVCHI QISMI ---
@dp.message(CommandStart())
async def cmd_start(message: Message):
    async with aiosqlite.connect("bot.db") as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (message.from_user.id,))
        await db.commit()
        
    sub_keyboard = await check_subscription(message.from_user.id)
    if sub_keyboard:
        await message.answer("👋 Salom! Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:", reply_markup=sub_keyboard)
        return
        
    await message.answer("🍿 Salom! Qidirayotgan kinongiz kodini yuboring:")

@dp.callback_query(F.data == "check_sub")
async def process_sub_check(call: CallbackQuery):
    sub_keyboard = await check_subscription(call.from_user.id)
    if sub_keyboard:
        await call.answer("⚠️ Hali barcha kanallarga a'zo bo'lmapsiz!", show_alert=True)
    else:
        await call.message.delete()
        await call.message.answer("✅ Rahmat! Endi kino kodini yuborishingiz mumkin.")

@dp.message(F.text.regexp(r'^\d+$'))
async def find_movie(message: Message):
    # Majburiy obuna tekshiruvi
    sub_keyboard = await check_subscription(message.from_user.id)
    if sub_keyboard:
        await message.answer("⚠️ Botdan foydalanish uchun kanallarga obuna bo'ling:", reply_markup=sub_keyboard)
        return

    movie_code = message.text.strip()
    async with aiosqlite.connect("bot.db") as db:
        async with db.execute("SELECT post_id FROM movies WHERE code = ?", (movie_code,)) as cursor:
            result = await cursor.fetchone()
            
    if result:
        post_id = result[0]
        try:
            # copy_message o'rniga forward_message dan foydalanamiz
            await bot.forward_message(
                chat_id=message.chat.id,
                from_chat_id=MOVIE_CHANNEL_ID,
                message_id=post_id
            )
            # Forwarddan keyin xabar yuborish (ixtiyoriy)
            # await message.answer(f"🎬 {movie_code}-kodli kino topildi!") 
            
        except Exception as e:
            logging.error(f"Send error: {e}")
            await message.answer("❌ Xatolik: Bot videoni topa olmadi. Kanal sozlamalarida 'Restrict saving content' yopiqligini va bot adminligini tekshiring.")
    else:
        await message.answer("😔 Bunday kodli kino topilmadi.")

# --- ADMIN PANEL QISMI ---
def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Kanal qo'shish", callback_data="admin_add_channel"),
         InlineKeyboardButton(text="🗑 Kanallarni tozalash", callback_data="admin_del_channel")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats"),
         InlineKeyboardButton(text="✉️ Xabar yuborish", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🎬 Barcha kodlar", callback_data="admin_list_movies")]
    ])

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("👨‍💻 Admin panelga xush kelibsiz.", reply_markup=admin_keyboard())

@dp.callback_query(F.data == "admin_add_channel")
async def add_channel_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Kanal ID sini kiriting (masalan, -100...):")
    await state.set_state(AdminStates.waiting_for_channel_id)
    await call.answer()

@dp.message(AdminStates.waiting_for_channel_id)
async def add_channel_id(message: Message, state: FSMContext):
    await state.update_data(channel_id=message.text)
    await message.answer("Endi kanal ssilkasi (URL) yuboring:")
    await state.set_state(AdminStates.waiting_for_channel_url)

@dp.message(AdminStates.waiting_for_channel_url)
async def add_channel_url(message: Message, state: FSMContext):
    data = await state.get_data()
    channel_id = data['channel_id']
    url = message.text
    async with aiosqlite.connect("bot.db") as db:
        await db.execute("INSERT OR REPLACE INTO channels (channel_id, url) VALUES (?, ?)", (channel_id, url))
        await db.commit()
    await message.answer("✅ Kanal qo'shildi!")
    await state.clear()

@dp.callback_query(F.data == "admin_del_channel")
async def del_channel(call: CallbackQuery):
    async with aiosqlite.connect("bot.db") as db:
        await db.execute("DELETE FROM channels")
        await db.commit()
    await call.message.answer("🗑 Majburiy obuna ro'yxati tozalandi.")
    await call.answer()

@dp.callback_query(F.data == "admin_list_movies")
async def list_movies(call: CallbackQuery):
    async with aiosqlite.connect("bot.db") as db:
        async with db.execute("SELECT code FROM movies ORDER BY CAST(code AS INTEGER)") as cursor:
            movies = await cursor.fetchall()
    if not movies:
        await call.message.answer("Bazada kino yo'q.")
    else:
        text = "🎬 **Saqlangan kodlar:**\n\n" + ", ".join([f"`{m[0]}`" for m in movies])
        await call.message.answer(text, parse_mode="Markdown")
    await call.answer()

@dp.callback_query(F.data == "admin_stats")
async def show_stats(call: CallbackQuery):
    async with aiosqlite.connect("bot.db") as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c1:
            u_count = (await c1.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM movies") as c2:
            m_count = (await c2.fetchone())[0]
    await call.message.answer(f"📊 Statistika:\n\n👥 Foydalanuvchilar: {u_count}\n🎬 Kinolar: {m_count}")
    await call.answer()

@dp.callback_query(F.data == "admin_broadcast")
async def broadcast_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Xabarni yuboring:")
    await state.set_state(AdminStates.waiting_for_broadcast)
    await call.answer()

@dp.message(AdminStates.waiting_for_broadcast)
async def broadcast_message(message: Message, state: FSMContext):
    async with aiosqlite.connect("bot.db") as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()
    count = 0
    for (user_id,) in users:
        try:
            await message.send_copy(chat_id=user_id)
            count += 1
            await asyncio.sleep(0.05) # Spamdan himoya
        except: pass
    await message.answer(f"✅ {count} kishiga yuborildi.")
    await state.clear()

async def main():
    await init_db()
    print("Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())