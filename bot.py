import os
import sys
import time
import html
import asyncio
from datetime import datetime, timezone
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton,
    BotCommand, BotCommandScopeDefault, BotCommandScopeChat
)
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest

from config import (
    BOT_TOKEN, OWNER_ID, LOG_CHANNEL_ID, BOT_START_TIME,
    BRAND_NAME, BOT_USERNAME, COMMUNITY_URL,
    RENDER_EMAIL, RENDER_SERVICE_NAME, RENDER_REPO,
    RENDER_USED_HOURS, DEFAULT_PROVIDER, CATBOX_USERHASH, IST
)
import database as db
from uploader_service import (
    smart_upload, format_file_size, generate_embed_codes, delete_from_catbox
)
from keep_alive import start_web_server

dp = Dispatcher()
HTTP_SESSION: aiohttp.ClientSession | None = None

# ==============================================================================
# SYSTEM & METRICS HELPERS
# ==============================================================================

def get_uptime_string() -> str:
    uptime_sec = int((datetime.now(IST) - BOT_START_TIME).total_seconds())
    days, rem = divmod(uptime_sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)

    parts = []
    if days > 0: parts.append(f"{days}d")
    if hours > 0 or days > 0: parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0: parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)

def get_process_memory_mb() -> float:
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return 38.4

async def measure_gateway_latency(bot: Bot) -> float:
    t0 = time.perf_counter()
    try:
        await bot.get_me()
    except Exception:
        pass
    return (time.perf_counter() - t0) * 1000.0

async def build_usage_card_text(bot: Bot) -> tuple[str, InlineKeyboardMarkup]:
    now = datetime.now(IST)
    uptime_str = get_uptime_string()

    session_hours = (now - BOT_START_TIME).total_seconds() / 3600.0
    total_used_hours = min(750.0, RENDER_USED_HOURS + session_hours)
    remaining_hours = max(0.0, 750.0 - total_used_hours)
    used_pct = min(100.0, (total_used_hours / 750.0) * 100.0)
    days_left_capacity = remaining_hours / 24.0

    mem_mb = get_process_memory_mb()
    mem_pct = min(100.0, (mem_mb / 512.0) * 100.0)
    ping_ms = await measure_gateway_latency(bot)

    stats = await db.get_bot_statistics()
    total_mb_str = format_file_size(stats["total_bytes"])

    text = (
        f"<b>{BRAND_NAME} — Host & Service Metrics</b>\n\n"
        "<b>Host & Cloud Account:</b>\n"
        "• <b>Cloud:</b> Render (Frankfurt, EU-Central)\n"
        f"• <b>Account Email:</b> <code>{html.escape(RENDER_EMAIL)}</code>\n"
        f"• <b>Service Name:</b> <code>{html.escape(RENDER_SERVICE_NAME)}</code>\n"
        f"• <b>Repository:</b> <code>{html.escape(RENDER_REPO)}</code>\n\n"
        "<b>Monthly Free Usage (Render Limit: 750h):</b>\n"
        f"• <b>Active Uptime:</b> <code>{uptime_str}</code>\n"
        f"• <b>Hours This Month:</b> <code>{total_used_hours:.1f}h / 750h ({used_pct:.1f}%)</code>\n"
        f"• <b>Remaining Free:</b> <code>{remaining_hours:.1f}h (~{days_left_capacity:.1f} days)</code>\n"
        f"• <b>Memory (RAM):</b> <code>{mem_mb:.1f} MB / 512 MB ({mem_pct:.1f}%)</code>\n\n"
        "<b>Image Engine Stats:</b>\n"
        f"• <b>Total Uploads:</b> <code>{stats['total_uploads']}</code>\n"
        f"• <b>Total Data Hosted:</b> <code>{total_mb_str}</code>\n"
        f"• <b>Telegram Gateway Ping:</b> <code>{ping_ms:.1f} ms</code>"
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Refresh Metrics", callback_data="usage:refresh", style="primary")]
    ])
    return text, markup

# ==============================================================================
# BOT COMMANDS & RESTART NOTIFICATION
# ==============================================================================

async def register_bot_commands(bot: Bot):
    public_commands = [
        BotCommand(command="start", description="Start bot & upload instructions"),
        BotCommand(command="help", description="How to use & format options"),
        BotCommand(command="myuploads", description="View your recent uploads"),
        BotCommand(command="ping", description="Check bot latency and uptime")
    ]
    owner_commands = public_commands + [
        BotCommand(command="stats", description="Service upload statistics (Owner)"),
        BotCommand(command="usage", description="Render hosting & account usage (Owner)")
    ]
    try:
        await bot.set_my_commands(public_commands, scope=BotCommandScopeDefault())
        if OWNER_ID:
            try:
                await bot.set_my_commands(owner_commands, scope=BotCommandScopeChat(chat_id=OWNER_ID))
            except Exception:
                pass
        print("[commands] Registered official Telegram bot commands.")
    except Exception as e:
        print(f"[commands] Failed to register commands: {e}")

def get_latest_changes() -> list[str]:
    reason_file = os.path.join(os.path.dirname(__file__), "restart_reason.txt")
    if os.path.exists(reason_file):
        try:
            with open(reason_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            os.remove(reason_file)
            if content:
                return [line.strip("-•* ").strip() for line in content.splitlines() if line.strip()]
        except Exception:
            pass

    try:
        import subprocess
        raw = subprocess.check_output(
            ["git", "log", "-1", "--pretty=%s%n%b"],
            text=True, stderr=subprocess.DEVNULL
        ).strip()
        lines = [l.strip("-•* ").strip() for l in raw.splitlines() if l.strip()]
        cleaned = []
        for line in lines:
            for prefix in ["feat:", "fix:", "chore:", "refactor:", "perf:", "docs:", "style:", "test:"]:
                if line.lower().startswith(prefix):
                    line = line[len(prefix):].strip()
                    break
            if line and len(line) > 3 and not line.startswith("Co-authored-by:") and not line.startswith("Signed-off-by:"):
                cleaned.append(line[0].upper() + line[1:])
            if len(cleaned) >= 3:
                break
        if cleaned:
            return cleaned
    except Exception:
        pass

    return ["Initial deployment of Kayi Image to Link Bot"]

async def send_restart_notification(bot: Bot):
    if not OWNER_ID:
        return

    changes = get_latest_changes()
    changes_formatted = "\n".join(f"• {html.escape(c)}" for c in changes)
    now_str = datetime.now(IST).strftime("%I:%M %p IST · %b %d")

    content = (
        f"<b>{BRAND_NAME} Online 🟢</b>\n\n"
        "<b>Changes in this Update:</b>\n"
        f"{changes_formatted}\n\n"
        f"<b>Time:</b> <code>{now_str}</code>"
    )

    targets = [OWNER_ID]
    if LOG_CHANNEL_ID:
        try:
            targets.append(int(LOG_CHANNEL_ID))
        except ValueError:
            pass

    for chat_id in set(targets):
        try:
            await bot.send_message(chat_id=chat_id, text=content, parse_mode=ParseMode.HTML)
            print(f"[restart] Sent clean restart notification to {chat_id}")
        except Exception as e:
            print(f"[restart] Could not notify {chat_id}: {e}")

# ==============================================================================
# CORE IMAGE UPLOAD HANDLER
# ==============================================================================

async def handle_media_upload(message: types.Message, bot: Bot, file_id: str, file_unique_id: str, filename: str, mime_type: str, file_size: int):
    # 1. Deduplication Cache Check
    cached = await db.find_existing_upload(file_unique_id)
    if cached:
        direct_url = cached["url"]
        provider_name = cached["provider"]
        size_str = format_file_size(cached["file_size"])
        upload_id = cached["id"]
        is_cached = True
    else:
        is_cached = False
        status_msg = await message.reply("⚡ <i>Uploading image to high-speed cloud...</i>", parse_mode=ParseMode.HTML)

        try:
            # Download file into memory
            file_info = await bot.get_file(file_id)
            if not file_info.file_path:
                await status_msg.edit_text("❌ <i>Failed to fetch image from Telegram servers.</i>", parse_mode=ParseMode.HTML)
                return

            file_bytes_io = await bot.download_file(file_info.file_path)
            file_bytes = file_bytes_io.read()

            # Upload to cloud
            direct_url, provider_name = await smart_upload(
                session=HTTP_SESSION,
                file_bytes=file_bytes,
                filename=filename,
                mime_type=mime_type,
                preferred=DEFAULT_PROVIDER,
                userhash=CATBOX_USERHASH
            )

            # Record in database
            upload_id = await db.record_upload(
                user_id=message.from_user.id,
                file_id=file_id,
                file_unique_id=file_unique_id,
                file_name=filename,
                file_size=len(file_bytes),
                provider=provider_name,
                url=direct_url
            )

            size_str = format_file_size(len(file_bytes))
            await status_msg.delete()

        except Exception as e:
            print(f"[upload] Error: {e}")
            await status_msg.edit_text(
                f"❌ <b>Upload failed:</b> <code>{html.escape(str(e))}</code>\n\n<i>Please try again in a few seconds.</i>",
                parse_mode=ParseMode.HTML
            )
            return

    # 2. Build Single Clean Response Card with pointer emoji and colorful buttons
    tag = " ⚡" if is_cached else ""
    card_text = (
        f"<b>Image Link Ready ✅{tag}</b>\n\n"
        f"<code>{direct_url}</code>\n\n"
        f"• <b>File:</b> <code>{html.escape(filename)}</code>\n"
        f"• <b>Size:</b> <code>{size_str}</code>\n"
        f"• <b>Host:</b> <code>{provider_name}</code> (Permanent)\n\n"
        "<i>Tap buttons below to open, copy, or view embeds:</i>"
    )

    markup = build_upload_markup(direct_url, upload_id)

    await message.reply(
        card_text,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
        link_preview_options=types.LinkPreviewOptions(is_disabled=True)
    )

def build_upload_markup(direct_url: str, upload_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Open Image 🌐", url=direct_url, style="primary"),
            InlineKeyboardButton(text="Copy Link 📋", copy_text=CopyTextButton(text=direct_url), style="success")
        ],
        [
            InlineKeyboardButton(text="Markdown 📝", callback_data=f"embed:md:{upload_id}"),
            InlineKeyboardButton(text="HTML Embed 💻", callback_data=f"embed:html:{upload_id}")
        ],
        [
            InlineKeyboardButton(text="BBCode 🏷️", callback_data=f"embed:bbcode:{upload_id}"),
            InlineKeyboardButton(text="Delete File 🗑️", callback_data=f"del:ask:{upload_id}", style="danger")
        ]
    ])

# ==============================================================================
# COMMAND HANDLERS
# ==============================================================================

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await db.touch_user(message.from_user.id)
    text = (
        f"<b>Welcome to {BRAND_NAME}!</b> 🚀\n\n"
        "Convert any image, photo, sticker, or GIF into a **permanent high-speed direct link** with 1-tap copy blocks.\n\n"
        "<b>Supported Formats:</b>\n"
        "• <b>Photos & Documents:</b> JPG, PNG, WebP, SVG, BMP\n"
        "• <b>Animated:</b> GIFs & MP4 Clips (Up to 200MB)\n"
        "• <b>Stickers:</b> Animated & Static Telegram stickers\n\n"
        "<b>Features:</b>\n"
        "• Permanent Cloud Hosting (Never expires)\n"
        "• Separate 1-tap copy blocks for Direct URL & Markdown\n"
        "• Ready-to-use HTML & BBCode embeds\n\n"
        "👉 <b>Send or forward any image below to get started!</b>"
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Recent Uploads 📂", callback_data="user:myuploads", style="primary"),
            InlineKeyboardButton(text="Channel 👑", url=COMMUNITY_URL, style="success")
        ]
    ])

    await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=markup)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        f"<b>{BRAND_NAME} — Help & Instructions</b> 📖\n\n"
        "<b>How to use:</b>\n"
        "1. Simply send any photo or image to the chat.\n"
        "2. To preserve 100% original uncompressed quality, send it as a <b>File/Document</b>.\n"
        "3. The bot uploads the image and gives you instant 1-tap copy links.\n\n"
        "<b>Commands:</b>\n"
        "• <code>/start</code> — Start bot & overview\n"
        "• <code>/myuploads</code> — View your recent 5 uploads\n"
        "• <code>/ping</code> — Check bot latency & uptime\n"
        "• <code>/help</code> — This help message"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)

@dp.message(Command("myuploads"))
async def cmd_myuploads(message: types.Message):
    uploads = await db.get_user_recent_uploads(message.from_user.id, limit=5)
    if not uploads:
        await message.reply("<i>You haven't uploaded any images yet. Send a photo to begin!</i>", parse_mode=ParseMode.HTML)
        return

    lines = ["<b>Your Recent Uploads 📂</b>\n"]
    for idx, up in enumerate(uploads, 1):
        size = format_file_size(up["file_size"])
        lines.append(f"<b>{idx}.</b> <code>{html.escape(up['file_name'])}</code> ({size})\n• <a href=\"{up['url']}\">Direct Link</a>")

    await message.reply("\n\n".join(lines), parse_mode=ParseMode.HTML, link_preview_options=types.LinkPreviewOptions(is_disabled=True))

@dp.message(Command("ping"))
async def cmd_ping(message: types.Message, bot: Bot):
    latency_ms = await measure_gateway_latency(bot)
    uptime_str = get_uptime_string()

    text = (
        "<b>Pong!</b> ⚡\n\n"
        f"<b>Latency:</b> <code>{latency_ms:.1f} ms</code>\n\n"
        f"<b>Uptime:</b> <code>{uptime_str}</code>"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.reply("Access restricted to bot owner.")
        return

    stats = await db.get_bot_statistics()
    uptime_str = get_uptime_string()
    total_data = format_file_size(stats["total_bytes"])

    text = (
        f"<b>{BRAND_NAME} — Service Statistics 📊</b>\n\n"
        "<b>User Base:</b>\n"
        f"• <b>Total Users:</b> <code>{stats['total_users']}</code>\n\n"
        "<b>Upload Activity:</b>\n"
        f"• <b>Total Images Hosted:</b> <code>{stats['total_uploads']}</code>\n"
        f"• <b>Total Data Uploaded:</b> <code>{total_data}</code>\n"
        f"• <b>Uploads Today:</b> <code>{stats['uploads_today']}</code>\n\n"
        f"<b>Uptime:</b> <code>{uptime_str}</code>"
    )
    await message.reply(text, parse_mode=ParseMode.HTML)

@dp.message(Command("usage"))
@dp.message(Command("render"))
@dp.message(Command("status"))
async def cmd_usage(message: types.Message, bot: Bot):
    if message.from_user.id != OWNER_ID:
        await message.reply("Access restricted to bot owner.")
        return

    text, markup = await build_usage_card_text(bot)
    await message.reply(text, parse_mode=ParseMode.HTML, reply_markup=markup)

# ==============================================================================
# MEDIA MESSAGE RECEIVERS
# ==============================================================================

@dp.message(F.photo)
async def on_photo_received(message: types.Message, bot: Bot):
    photo = message.photo[-1]
    filename = f"photo_{photo.file_unique_id}.jpg"
    await handle_media_upload(
        message=message,
        bot=bot,
        file_id=photo.file_id,
        file_unique_id=photo.file_unique_id,
        filename=filename,
        mime_type="image/jpeg",
        file_size=photo.file_size or 0
    )

@dp.message(F.document)
async def on_document_received(message: types.Message, bot: Bot):
    doc = message.document
    mime = doc.mime_type or ""
    if not (mime.startswith("image/") or mime.startswith("video/") or doc.file_name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".mp4"))):
        await message.reply("⚠️ <i>Please send an image or graphic file (PNG, JPG, WebP, GIF, SVG).</i>", parse_mode=ParseMode.HTML)
        return

    if doc.file_size and doc.file_size > 200 * 1024 * 1024:
        await message.reply("❌ <i>File exceeds the 200 MB maximum limit.</i>", parse_mode=ParseMode.HTML)
        return

    filename = doc.file_name or f"file_{doc.file_unique_id}.png"
    await handle_media_upload(
        message=message,
        bot=bot,
        file_id=doc.file_id,
        file_unique_id=doc.file_unique_id,
        filename=filename,
        mime_type=mime or "application/octet-stream",
        file_size=doc.file_size or 0
    )

@dp.message(F.animation)
async def on_animation_received(message: types.Message, bot: Bot):
    anim = message.animation
    filename = anim.file_name or f"animation_{anim.file_unique_id}.mp4"
    await handle_media_upload(
        message=message,
        bot=bot,
        file_id=anim.file_id,
        file_unique_id=anim.file_unique_id,
        filename=filename,
        mime_type=anim.mime_type or "video/mp4",
        file_size=anim.file_size or 0
    )

@dp.message(F.sticker)
async def on_sticker_received(message: types.Message, bot: Bot):
    sticker = message.sticker
    filename = f"sticker_{sticker.file_unique_id}.webp"
    await handle_media_upload(
        message=message,
        bot=bot,
        file_id=sticker.file_id,
        file_unique_id=sticker.file_unique_id,
        filename=filename,
        mime_type="image/webp",
        file_size=sticker.file_size or 0
    )

# ==============================================================================
# CALLBACK QUERY HANDLERS
# ==============================================================================

@dp.callback_query(F.data.startswith("embed:md:"))
async def cb_embed_md(callback: types.CallbackQuery):
    upload_id = int(callback.data.split(":")[2])
    async with db.aiosqlite.connect(db.DATABASE_PATH) as conn:
        conn.row_factory = db.aiosqlite.Row
        async with conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        await callback.answer("Upload record not found.", show_alert=True)
        return

    md_code = f"![image]({row['url']})"
    await callback.message.reply(
        f"<b>Markdown Embed Code 👇</b>\n\n<code>{html.escape(md_code)}</code>",
        parse_mode=ParseMode.HTML
    )
    await callback.answer("Markdown code generated!")

@dp.callback_query(F.data.startswith("embed:html:"))
async def cb_embed_html(callback: types.CallbackQuery):
    upload_id = int(callback.data.split(":")[2])
    async with db.aiosqlite.connect(db.DATABASE_PATH) as conn:
        conn.row_factory = db.aiosqlite.Row
        async with conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        await callback.answer("Upload record not found.", show_alert=True)
        return

    html_code = f'<img src="{row["url"]}" alt="{row["file_name"]}" />'
    await callback.message.reply(
        f"<b>HTML Embed Code 👇</b>\n\n<code>{html.escape(html_code)}</code>",
        parse_mode=ParseMode.HTML
    )
    await callback.answer("HTML embed code generated!")

@dp.callback_query(F.data.startswith("embed:bbcode:"))
async def cb_embed_bbcode(callback: types.CallbackQuery):
    upload_id = int(callback.data.split(":")[2])
    async with db.aiosqlite.connect(db.DATABASE_PATH) as conn:
        conn.row_factory = db.aiosqlite.Row
        async with conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)) as cur:
            row = await cur.fetchone()

    if not row:
        await callback.answer("Upload record not found.", show_alert=True)
        return

    bbcode = f"[img]{row['url']}[/img]"
    await callback.message.reply(
        f"<b>BBCode Embed (Forums/Reddit) 👇</b>\n\n<code>{html.escape(bbcode)}</code>",
        parse_mode=ParseMode.HTML
    )
    await callback.answer("BBCode generated!")

@dp.callback_query(F.data.startswith("del:ask:"))
async def cb_delete_ask(callback: types.CallbackQuery):
    upload_id = int(callback.data.split(":")[2])
    upload = await db.get_upload_by_id(upload_id)
    if not upload:
        await callback.answer("Upload record not found or already deleted.", show_alert=True)
        return

    # Check permission: only uploader or bot owner
    if callback.from_user.id != upload["user_id"] and callback.from_user.id != OWNER_ID:
        await callback.answer("⚠️ You can only delete your own uploads.", show_alert=True)
        return

    confirm_markup = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚠️ Confirm Delete 🗑️", callback_data=f"del:confirm:{upload_id}", style="danger"),
            InlineKeyboardButton(text="Cancel ↩️", callback_data=f"del:cancel:{upload_id}")
        ]
    ])

    try:
        await callback.message.edit_reply_markup(reply_markup=confirm_markup)
    except TelegramBadRequest:
        pass
    await callback.answer("Confirm deletion?")

@dp.callback_query(F.data.startswith("del:cancel:"))
async def cb_delete_cancel(callback: types.CallbackQuery):
    upload_id = int(callback.data.split(":")[2])
    upload = await db.get_upload_by_id(upload_id)
    if not upload:
        await callback.answer("Upload record not found.", show_alert=True)
        return

    original_markup = build_upload_markup(upload["url"], upload_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=original_markup)
    except TelegramBadRequest:
        pass
    await callback.answer("Cancelled.")

@dp.callback_query(F.data.startswith("del:confirm:"))
async def cb_delete_confirm(callback: types.CallbackQuery):
    upload_id = int(callback.data.split(":")[2])
    upload = await db.get_upload_by_id(upload_id)
    if not upload:
        await callback.answer("File was already deleted.", show_alert=True)
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        return

    # Check permission: only uploader or bot owner
    if callback.from_user.id != upload["user_id"] and callback.from_user.id != OWNER_ID:
        await callback.answer("⚠️ You can only delete your own uploads.", show_alert=True)
        return

    # 1. Delete from Catbox if userhash is set and provider was Catbox
    if CATBOX_USERHASH and "catbox" in upload.get("provider", "").lower():
        await delete_from_catbox(HTTP_SESSION, upload["url"], CATBOX_USERHASH)

    # 2. Delete from database & deduplication cache
    await db.delete_upload(upload_id)

    # 3. Delete message from chat
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass

    await callback.answer("🗑️ File deleted successfully!", show_alert=True)

@dp.callback_query(F.data == "user:myuploads")
async def cb_user_myuploads(callback: types.CallbackQuery):
    await callback.answer()
    await cmd_myuploads(callback.message)

@dp.callback_query(F.data == "usage:refresh")
async def cb_usage_refresh(callback: types.CallbackQuery, bot: Bot):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Access restricted to bot owner.", show_alert=True)
        return

    text, markup = await build_usage_card_text(bot)
    try:
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except TelegramBadRequest:
        pass
    await callback.answer("Metrics refreshed.")

# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================

async def main():
    global HTTP_SESSION
    if not BOT_TOKEN:
        print("\n[error] BOT_TOKEN is missing in .env!\n")
        sys.exit(1)

    print(f"Launching {BRAND_NAME}...")
    await db.init_db()

    connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300, ssl=False, enable_cleanup_closed=True)
    HTTP_SESSION = aiohttp.ClientSession(connector=connector)

    bot = Bot(token=BOT_TOKEN)

    runner = await start_web_server()
    await bot.delete_webhook(drop_pending_updates=True)

    await register_bot_commands(bot)
    await send_restart_notification(bot)

    print(f"{BRAND_NAME} is active. Ready to process images.")
    try:
        await dp.start_polling(bot)
    finally:
        if HTTP_SESSION and not HTTP_SESSION.closed:
            await HTTP_SESSION.close()
            await asyncio.sleep(0.25)
        await bot.session.close()
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print(f"\n{BRAND_NAME} shut down safely.")
