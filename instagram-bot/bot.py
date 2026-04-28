import os
import asyncio
from pathlib import Path
from uuid import uuid4
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import yt_dlp
import tempfile

# ==================== НАСТРОЙКИ ====================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

COOKIES_FILE = Path(__file__).parent / "instagram.cookies.txt"

# Временное хранилище ссылок (ключ - короткий ID, значение - URL)
links = {}

print("=" * 40)
print("БОТ ЗАПУЩЕН")
print(f"Куки: {'✅ ЕСТЬ' if COOKIES_FILE.exists() else '❌ НЕТ'}")
print("=" * 40)

# ==================== /start ====================
@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "👋 Привет! Отправь ссылку на видео.\n\n"
        "📱 Instagram Reels / Posts\n"
        "🎬 YouTube / Shorts\n"
        "🎵 TikTok\n\n"
        "🔗 Просто вставь ссылку!"
    )

# ==================== КНОПКИ КАЧЕСТВА ====================
def get_keyboard(link_id):
    """Создаёт кнопки выбора качества"""
    buttons = [
        [InlineKeyboardButton(text="🔴 SD (низкое)", callback_data=f"sd:{link_id}")],
        [InlineKeyboardButton(text="🟡 HD (среднее)", callback_data=f"hd:{link_id}")],
        [InlineKeyboardButton(text="🟢 Full HD (высокое)", callback_data=f"fhd:{link_id}")],
        [InlineKeyboardButton(text="🎵 Только MP3", callback_data=f"mp3:{link_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==================== ОБРАБОТКА ССЫЛКИ ====================
@dp.message()
async def handle_link(message: types.Message):
    url = message.text.strip()
    
    if not url.startswith("http"):
        await message.reply("❌ Отправь корректную ссылку (http:// или https://)")
        return
    
    # Создаём короткий ID
    link_id = str(uuid4())[:8]
    links[link_id] = url
    
    # Удаляем из памяти через 10 минут
    asyncio.create_task(delete_link_later(link_id, 600))
    
    keyboard = get_keyboard(link_id)
    await message.answer(
        "🎯 **Выбери качество:**",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def delete_link_later(link_id, delay):
    await asyncio.sleep(delay)
    links.pop(link_id, None)

# ==================== ОБРАБОТКА КНОПОК ====================
@dp.callback_query(lambda c: c.data.startswith(("sd:", "hd:", "fhd:", "mp3:")))
async def quality_chosen(callback: CallbackQuery):
    # Парсим данные
    data = callback.data.split(":")
    quality = data[0]  # sd, hd, fhd, mp3
    link_id = data[1]
    
    url = links.get(link_id)
    if not url:
        await callback.answer("❌ Ссылка устарела. Отправь заново.", show_alert=True)
        await callback.message.delete()
        return
    
    # Выбор формата для yt-dlp
    formats = {
        'sd': 'worst[ext=mp4]/worst',
        'hd': 'best[height<=480][ext=mp4]/best[height<=480]',
        'fhd': 'best[height<=720][ext=mp4]/best[height<=720]',
        'mp3': 'bestaudio/best',
    }
    
    fmt = formats.get(quality, 'best')
    
    # Удаляем кнопки, показываем статус
    await callback.message.edit_text("⏳ Скачиваю...")
    await callback.answer("Начинаю загрузку")
    
    try:
        # Скачиваем
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': fmt,
            'outtmpl': '%(id)s.%(ext)s',
            'max_filesize': 50 * 1024 * 1024,
            'socket_timeout': 30,
            'retries': 3,
        }
        
        if COOKIES_FILE.exists():
            ydl_opts['cookiefile'] = str(COOKIES_FILE)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts['outtmpl'] = f'{tmpdir}/%(id)s.%(ext)s'
            
            # Скачиваем в фоне
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(
                None,
                lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=True)
            )
            
            filename = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(info)
            
            if not Path(filename).exists():
                raise Exception("Файл не создан")
            
            # Отправляем
            await callback.message.edit_text("📤 Отправляю...")
            
            title = info.get('title', 'Видео')[:100]
            ext = info.get('ext', '').lower()
            filesize = Path(filename).stat().st_size / (1024 * 1024)
            
            if ext in ['mp4', 'webm', 'mkv', 'mov']:
                await bot.send_video(
                    chat_id=callback.message.chat.id,
                    video=types.FSInputFile(filename),
                    caption=f"🎬 {title}\n📊 {filesize:.1f} МБ",
                    supports_streaming=True
                )
            elif ext in ['mp3', 'm4a', 'opus']:
                await bot.send_audio(
                    chat_id=callback.message.chat.id,
                    audio=types.FSInputFile(filename),
                    title=title
                )
            elif ext in ['jpg', 'jpeg', 'png']:
                await bot.send_photo(
                    chat_id=callback.message.chat.id,
                    photo=types.FSInputFile(filename),
                    caption=f"📸 {title}"
                )
            else:
                await bot.send_document(
                    chat_id=callback.message.chat.id,
                    document=types.FSInputFile(filename),
                    caption=f"📄 {title}"
                )
            
            # Удаляем сообщение со статусом
            await callback.message.delete()
            print(f"✅ {title[:50]}")
            
    except Exception as e:
        error = str(e)
        print(f"❌ {error}")
        
        if "format" in error.lower() and "not available" in error.lower():
            # Если формат не найден — пробуем любой
            await callback.message.edit_text("🔄 Формат недоступен, пробую другой...")
            try:
                await retry_download(url, callback.message)
            except Exception as e2:
                await callback.message.edit_text(f"❌ Ошибка:\n`{str(e2)[:200]}`")
        elif "login" in error.lower() or "private" in error.lower():
            await callback.message.edit_text("🔒 Видео приватное или аккаунт закрыт")
        elif "timeout" in error.lower():
            await callback.message.edit_text("⏱️ Таймаут. Попробуй ещё раз.")
        else:
            await callback.message.edit_text(f"❌ `{error[:200]}`")

# ==================== ЗАПАСНОЙ ВАРИАНТ (если формат не найден) ====================
async def retry_download(url, message):
    """Пробует скачать в любом доступном формате"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'best',
        'outtmpl': '%(id)s.%(ext)s',
        'max_filesize': 50 * 1024 * 1024,
        'socket_timeout': 30,
        'retries': 3,
    }
    
    if COOKIES_FILE.exists():
        ydl_opts['cookiefile'] = str(COOKIES_FILE)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        ydl_opts['outtmpl'] = f'{tmpdir}/%(id)s.%(ext)s'
        
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(
            None,
            lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=True)
        )
        
        filename = yt_dlp.YoutubeDL(ydl_opts).prepare_filename(info)
        
        await message.edit_text("📤 Отправляю...")
        
        title = info.get('title', 'Видео')[:100]
        ext = info.get('ext', '').lower()
        filesize = Path(filename).stat().st_size / (1024 * 1024)
        
        if ext in ['mp4', 'webm', 'mkv', 'mov']:
            await bot.send_video(
                chat_id=message.chat.id,
                video=types.FSInputFile(filename),
                caption=f"🎬 {title}\n📊 {filesize:.1f} МБ",
                supports_streaming=True
            )
        else:
            await bot.send_document(
                chat_id=message.chat.id,
                document=types.FSInputFile(filename),
                caption=f"📄 {title}"
            )
        
        await message.delete()

# ==================== ЗАПУСК ====================
async def main():
    print("🚀 Бот готов!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())