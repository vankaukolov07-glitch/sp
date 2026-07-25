import os
import io
import asyncio
import logging
import aiosqlite
from aiohttp import web
from PIL import Image, ImageDraw, ImageFont
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8022903371:AAGRn8HDmh4hnrfq23vyHNyDHtoEFdvCacg" 
ADMIN_GROUP_ID = -1003856239103        # ID группы модераторов
CHANNEL_ID = -1002299762880            # ID твоего канала
DB_PATH = "predlozhka.db"
# ===================================================

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class AdminReply(StatesGroup):
    waiting_for_reply = State()

# ----------------- БАЗА ДАННЫХ И СТАТИСТИКА -----------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY)")
        await db.execute("CREATE TABLE IF NOT EXISTS posts (msg_id INTEGER PRIMARY KEY, user_id INTEGER)")
        await db.commit()

async def is_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM banned_users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone() is not None

async def ban_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO banned_users VALUES (?)", (user_id,))
        await db.commit()

async def unban_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        await db.commit()

async def save_post(msg_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO posts VALUES (?, ?)", (msg_id, user_id))
        await db.commit()

async def get_post_author(msg_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM posts WHERE msg_id = ?", (msg_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM posts") as c1:
            posts = (await c1.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM banned_users") as c2:
            bans = (await c2.fetchone())[0]
        return posts, bans

# ----------------- КЛАВИАТУРЫ -----------------
def get_admin_keyboard(user_id: int):
    buttons = [
        [
            InlineKeyboardButton(text="✅ В канал", callback_data="pub"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data="rej")
        ],
        [
            InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply_{user_id}"),
            InlineKeyboardButton(text="🚫 Бан", callback_data=f"ban_{user_id}"),
            InlineKeyboardButton(text="🟢 Разбан", callback_data=f"unban_{user_id}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ----------------- ХЕНДЛЕРЫ ПОЛЬЗОВАТЕЛЕЙ -----------------
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    # Проверяем, не пишет ли это бот из группы
    if message.chat.type == "private":
        await message.answer(
            "👋 **Добро пожаловать в предложку Сплетни Мурома!**\n\n"
            "Присылай новости прямо сюда. Всё модерируется анонимно. Мы опубликуем самое интересное!",
            parse_mode="Markdown"
        )

# НОВЫЙ ХЕНДЛЕР: Ловим только ФОТО для наложения ватермарки
@dp.message(F.chat.type == "private", F.photo)
async def handle_photo_submission(message: types.Message):
    if await is_banned(message.from_user.id):
        await message.answer("❌ Вы заблокированы.")
        return

    # Скачиваем фото в оперативную память
    photo = message.photo[-1]
    photo_bytes = io.BytesIO()
    await bot.download(photo, destination=photo_bytes)
    photo_bytes.seek(0)
    
    # Открываем изображение для обработки
    img = Image.open(photo_bytes)
    draw = ImageDraw.Draw(img)
    
    # Настройки шрифта
    try:
        font = ImageFont.truetype("ArialBlack.ttf", size=80) 
    except IOError:
        font = ImageFont.load_default()
        
    watermark_text = "spletni murom"
    
    # Вычисляем размеры, чтобы поставить текст в правый нижний угол
    text_bbox = draw.textbbox((0, 0), watermark_text, font=font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    
    width, height = img.size
    x = width - text_width - 80 
    y = height - text_height - 80 
    
    # Рисуем тень и сам белый текст
    draw.text((x + 2, y + 2), watermark_text, font=font, fill=(0, 0, 0, 150))
    draw.text((x, y), watermark_text, font=font, fill=(255, 255, 255, 150))
    
    # Сохраняем обработанную картинку обратно в байты
    output_bytes = io.BytesIO()
    img.save(output_bytes, format="JPEG")
    output_bytes.seek(0)
    
    photo_file = BufferedInputFile(output_bytes.read(), filename="watermarked.jpg")
    
    # Отправляем фото в админку (с оригинальным текстом от юзера)
    sent_photo = await bot.send_photo(
        chat_id=ADMIN_GROUP_ID,
        photo=photo_file,
        caption=message.caption
    )
    
    # Сохраняем ID сообщения для публикации
    await save_post(sent_photo.message_id, message.from_user.id)
    
    # Отправляем инфо и кнопки в ответ на фотку (как в оригинальном коде)
    user_info = f"👤 **От:** {message.from_user.full_name}\nID: `{message.from_user.id}`"
    await bot.send_message(
        chat_id=ADMIN_GROUP_ID, 
        text=user_info,
        reply_to_message_id=sent_photo.message_id,
        reply_markup=get_admin_keyboard(message.from_user.id),
        parse_mode="Markdown"
    )
    
    await message.reply("✈️ Твое фото отправлено!")

# Ловим обычные сообщения (текст, видео и т.д.)
@dp.message(F.chat.type == "private", ~F.text.startswith('/'))
async def handle_user_submission(message: types.Message):
    if await is_banned(message.from_user.id):
        await message.answer("❌ Вы заблокированы.")
        return

    # Пересылаем в группу админов
    forwarded = await bot.forward_message(chat_id=ADMIN_GROUP_ID, from_chat_id=message.chat.id, message_id=message.message_id)
    await save_post(forwarded.message_id, message.from_user.id)

    user_info = f"👤 **От:** {message.from_user.full_name}\nID: `{message.from_user.id}`"
    await bot.send_message(
        chat_id=ADMIN_GROUP_ID, text=user_info,
        reply_to_message_id=forwarded.message_id,
        reply_markup=get_admin_keyboard(message.from_user.id),
        parse_mode="Markdown"
    )
    await message.reply("✈️ Твоя новость отправлена!")

# ----------------- ХЕНДЛЕРЫ АДМИН ГРУППЫ -----------------
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    # Разрешаем статистику только в админской группе
    if message.chat.id == ADMIN_GROUP_ID:
        posts, bans = await get_stats()
        await message.answer(f"📊 **Статистика бота:**\n\n✉️ Всего заявок: {posts}\n🚫 В бане: {bans} чел.", parse_mode="Markdown")

@dp.callback_query(F.data == "pub")
async def publish_post(callback: CallbackQuery):
    # Разрешаем действия только в админской группе
    if callback.message.chat.id != ADMIN_GROUP_ID: return
    
    target_msg = callback.message.reply_to_message
    if target_msg:
        await bot.copy_message(chat_id=CHANNEL_ID, from_chat_id=ADMIN_GROUP_ID, message_id=target_msg.message_id)
        
        # Обновляем кнопку, чтобы было понятно, кто опубликовал
        admin_name = callback.from_user.first_name
        await callback.message.edit_text(f"{callback.message.text}\n\n✅ **ОПУБЛИКОВАНО ({admin_name})**")
        
        author_id = await get_post_author(target_msg.message_id)
        if author_id:
            try: await bot.send_message(author_id, "🎉 Твоя новость опубликована в канале!")
            except: pass
    await callback.answer("Опубликовано!")

@dp.callback_query(F.data == "rej")
async def reject_post(callback: CallbackQuery):
    if callback.message.chat.id != ADMIN_GROUP_ID: return
    
    admin_name = callback.from_user.first_name
    await callback.message.edit_text(f"{callback.message.text}\n\n❌ **ОТКЛОНЕНО ({admin_name})**")
    await callback.answer("Отклонено")

@dp.callback_query(F.data.startswith("ban_"))
async def ban_user_handler(callback: CallbackQuery):
    if callback.message.chat.id != ADMIN_GROUP_ID: return
    user_id = int(callback.data.split("_")[1])
    await ban_user(user_id)
    await callback.message.answer(f"⛔ Пользователь `{user_id}` забанен админом {callback.from_user.first_name}!")
    await callback.answer()

@dp.callback_query(F.data.startswith("unban_"))
async def unban_user_handler(callback: CallbackQuery):
    if callback.message.chat.id != ADMIN_GROUP_ID: return
    user_id = int(callback.data.split("_")[1])
    await unban_user(user_id)
    await callback.message.answer(f"🟢 Пользователь `{user_id}` разбанен админом {callback.from_user.first_name}!")
    await callback.answer()

@dp.callback_query(F.data.startswith("reply_"))
async def start_reply(callback: CallbackQuery, state: FSMContext):
    if callback.message.chat.id != ADMIN_GROUP_ID: return
    
    user_id = int(callback.data.split("_")[1])
    await state.update_data(reply_to_user=user_id)
    await state.set_state(AdminReply.waiting_for_reply)
    
    # Отвечаем в группе
    await callback.message.answer(f"@{callback.from_user.username or callback.from_user.first_name}, напиши ответ пользователю следующим сообщением:")
    await callback.answer()

@dp.message(AdminReply.waiting_for_reply)
async def send_reply_to_user(message: types.Message, state: FSMContext):
    # Проверяем, что ответ пишется в группе админов
    if message.chat.id != ADMIN_GROUP_ID: return
    
    data = await state.get_data()
    user_id = data.get("reply_to_user")
    
    try:
        await bot.send_message(user_id, f"💬 **Ответ от администрации:**\n\n{message.text}")
        await message.reply("✅ Ответ доставлен пользователю!")
    except Exception as e:
        await message.reply(f"❌ Ошибка отправки: {e}")
        
    await state.clear()

# ----------------- СТАРТ И ВЕБ-СЕРВЕР -----------------
async def handle_ping(request):
    return web.Response(text="Бот работает!")

async def main():
    await init_db()
    print("🚀 Предложка (режим группы) запущена!")
    
    # Настройка и запуск веб-сервера для Render
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    # Запуск самого бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
