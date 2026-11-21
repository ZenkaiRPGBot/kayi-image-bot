import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# Indian Standard Time (UTC + 05:30)
IST = timezone(timedelta(hours=5, minutes=30))

# Startup timestamp for uptime calculation
BOT_START_TIME = datetime.now(IST)

# Telegram Bot Token from @BotFather
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Owner & Log Channel IDs
OWNER_ID = int(os.getenv("OWNER_ID", "986380678"))
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "-1004495645007").strip()

# Brand Identity
BRAND_NAME = "Kayi Image to Link"
BOT_USERNAME = os.getenv("BOT_USERNAME", "@KayiImgBot")
COMMUNITY_URL = os.getenv("COMMUNITY_URL", "https://t.me/kayitv")

# Cloud Hosting Metadata
RENDER_EMAIL = os.getenv("RENDER_EMAIL", "2305101270081@paruluniversity.ac.in")
RENDER_SERVICE_NAME = os.getenv("RENDER_SERVICE_NAME", "kayi-image-bot")
RENDER_REPO = "ZenkaiRPGBot/kayi-image-bot"
RENDER_USED_HOURS = float(os.getenv("RENDER_USED_HOURS", "0.0"))

# Web Server Port
PORT = int(os.getenv("PORT", "8080"))
PING_URL = os.getenv("PING_URL") or os.getenv("RENDER_EXTERNAL_URL")
if not PING_URL and os.getenv("RENDER"):
    PING_URL = "https://kayi-image-bot.onrender.com"

# Storage Settings
DATABASE_PATH = os.path.join(os.path.dirname(__file__), "image_bot.db")
DEFAULT_PROVIDER = "catbox"  # 'catbox' (up to 200MB, permanent) or 'imgdb'
