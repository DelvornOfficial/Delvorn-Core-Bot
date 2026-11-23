import os
from dotenv import load_dotenv

load_dotenv()

# ==========================================
#          BOT CONFIGURATION
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Admin IDs â€” have unlimited credits, bypass all limits
ADMIN_IDS = [8816802036]

DATA_DIR = "data"
