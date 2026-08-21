import os
import sys
import io
import asyncio
import logging

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
from io import BytesIO
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from rembg import remove
from PIL import Image, ImageEnhance, ImageDraw
import numpy as np

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    print("ERROR: BOT_TOKEN not set!")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

DPI = 300
PASSPORT_W_MM = 35
PASSPORT_H_MM = 45
PASSPORT_W_PX = round(PASSPORT_W_MM / 25.4 * DPI)
PASSPORT_H_PX = round(PASSPORT_H_MM / 25.4 * DPI)


def remove_background_to_white(img: Image.Image) -> Image.Image:
    img_rgb = img.convert("RGB")
    result = remove(
        img_rgb,
        alpha_matting=True,
        alpha_matting_foreground_threshold=240,
        alpha_matting_background_threshold=20,
        alpha_matting_erode_size=10
    )
    white_bg = Image.new("RGBA", result.size, (255, 255, 255, 255))
    white_bg.paste(result, mask=result.split()[3])
    return white_bg.convert("RGB")


def enhance_photo(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Sharpness(img).enhance(1.15)
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Brightness(img).enhance(1.03)
    return img


def crop_to_passport(img: Image.Image) -> Image.Image:
    w, h = img.size
    target_ratio = PASSPORT_W_PX / PASSPORT_H_PX
    current_ratio = w / h

    if current_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 3
        img = img.crop((0, top, w, top + new_h))

    img = img.resize((PASSPORT_W_PX, PASSPORT_H_PX), Image.LANCZOS)
    return img


def process_image_full(original: Image.Image) -> Image.Image:
    no_bg = remove_background_to_white(original)
    enhanced = enhance_photo(no_bg)
    passport_photo = crop_to_passport(enhanced)
    return passport_photo


LAYOUTS = {
    4: {"sheet_w": 1050, "sheet_h": 1500, "cols": 2, "rows": 2},
    8: {"sheet_w": 1800, "sheet_h": 1200, "cols": 4, "rows": 2},
}


def create_print_sheet(photos: list[Image.Image], count: int) -> Image.Image:
    layout = LAYOUTS[count]
    sheet_w = layout["sheet_w"]
    sheet_h = layout["sheet_h"]
    cols = layout["cols"]
    rows = layout["rows"]

    sheet = Image.new("RGB", (sheet_w, sheet_h), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)

    gap_x = (sheet_w - cols * PASSPORT_W_PX) // (cols + 1)
    gap_y = (sheet_h - rows * PASSPORT_H_PX) // (rows + 1)

    for i, photo in enumerate(photos):
        row = i // cols
        col = i % cols
        x = gap_x + col * (PASSPORT_W_PX + gap_x)
        y = gap_y + row * (PASSPORT_H_PX + gap_y)
        sheet.paste(photo, (x, y))

    for r in range(rows):
        for c in range(cols):
            x = gap_x + c * (PASSPORT_W_PX + gap_x)
            y = gap_y + r * (PASSPORT_H_PX + gap_y)
            draw.rectangle(
                [x - 1, y - 1, x + PASSPORT_W_PX, y + PASSPORT_H_PX],
                outline=(200, 200, 200), width=1
            )

    return sheet


user_data_store = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "مرحباً!\n\n"
        "أرسل لي صورتك وسأقوم بتحويلها لصور جواز سفر جاهزة للطباعة.\n\n"
        "أرسل صورتك الآن:"
    )


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = context.bot_data.get("stats", {"count_4": 0, "count_8": 0})
    total_sheets = stats["count_4"] + stats["count_8"]
    await update.message.reply_text(
        f"تقرير استخدام البوت:\n\n"
        f"صور الحجم 4 (2x2): {stats['count_4']} ورقة\n"
        f"صور الحجم 8 (4x2): {stats['count_8']} ورقة\n"
        f"اجمالي الاوراق: {total_sheets}"
    )


async def photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    try:
        if update.message.photo:
            photo_file = await update.message.photo[-1].get_file()
        elif update.message.document and update.message.document.mime_type and \
             update.message.document.mime_type.startswith("image/"):
            photo_file = await update.message.document.get_file()
        else:
            await update.message.reply_text("يرجى إرسال صورة فقط.")
            return

        processing_msg = await update.message.reply_text("جاري تحميل الصورة...")

        photo_bytes = await photo_file.download_as_bytearray()
        original = Image.open(BytesIO(bytes(photo_bytes)))

        await processing_msg.edit_text("جاري إزالة الخلفية والمعالجة (قد يستغرق دقيقة)...")

        passport_photo = await asyncio.to_thread(process_image_full, original)

        user_data_store[user_id] = passport_photo

        keyboard = [
            [InlineKeyboardButton("4 صور (2x2)", callback_data="4")],
            [InlineKeyboardButton("8 صور (4x2)", callback_data="8")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await processing_msg.delete()

        await update.message.reply_text(
            "تم المعالجة! اختر عدد الصور:",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.exception("Error processing photo")
        try:
            await processing_msg.edit_text(f"حدث خطأ: {str(e)[:200]}")
        except:
            try:
                await update.message.reply_text(f"حدث خطأ: {str(e)[:200]}")
            except:
                pass


async def choose_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    count = int(query.data)

    if user_id not in user_data_store:
        await query.edit_message_text("انتهت الصلاحية. أرسل صورتك مرة أخرى.")
        return

    passport_photo = user_data_store[user_id]

    photos = [passport_photo.copy() for _ in range(count)]

    await query.edit_message_text(f"جاري إنشاء ورقة الطباعة ({count} صور)...")

    sheet = await asyncio.to_thread(create_print_sheet, photos, count)

    stats = context.bot_data.setdefault("stats", {"count_4": 0, "count_8": 0})
    stats[f"count_{count}"] += 1

    buf = BytesIO()
    sheet.save(buf, format="PNG", dpi=(DPI, DPI))
    buf.seek(0)

    restart_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("صورة جديدة /start", callback_data="restart")]
    ])

    await query.message.reply_document(
        document=buf,
        filename=f"passport_photos_{count}.png",
        caption=f"جاهز للطباعة! {count} صورة جواز 35x45مم بدقة {DPI} DPI",
        reply_markup=restart_keyboard
    )

    del user_data_store[user_id]


async def restart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "مرحباً!\n\n"
        "أرسل لي صورتك وسأقوم بتحويلها لصور جواز سفر جاهزة للطباعة.\n\n"
        "أرسل صورتك الآن:"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id in user_data_store:
        del user_data_store[user_id]
    await update.message.reply_text("تم الإلغاء. أرسل /start للبدء مرة أخرى.")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .read_timeout(120)
        .write_timeout(120)
        .connect_timeout(30)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("Report", report))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, photo_received))
    app.add_handler(CallbackQueryHandler(restart_callback, pattern=r"^restart$"))
    app.add_handler(CallbackQueryHandler(choose_count, pattern=r"^(4|8)$"))

    print("Bot is running...", flush=True)
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
