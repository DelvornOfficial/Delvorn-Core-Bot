"""
branding.py — ONE FILE REBRANDING SYSTEM
=========================================
Change ONLY this file to fully rebrand the bot.

To rebrand from "Delvorn" to e.g. "Titan":
1. Change BOT_NAME = "Titan"
2. Change BOT_TAGLINE = "Titan Stresser"
3. Change KEY_PREFIX = "Titancheat"
4. Update OWNER_CHANNEL, SUPPORT_GROUP links
5. Done! All bot messages, key names, and links update automatically.
"""

# Bot Identity
BOT_NAME     = "Delvorn"              # Main bot name
BOT_TAGLINE  = "Delvorn Stresser"     # Full tagline used in messages
KEY_PREFIX   = "Delvorncheat"         # Admin key prefix (Delvorncheat-30d-XXXXX)
BOT_VERSION  = "2.0"

# Channels & Groups — FALLBACK defaults only (admin overrides via /setup /canary commands)
DEFAULT_SETUP_LINK   = ""
DEFAULT_CANARY_LINK  = ""
OWNER_CHANNEL        = "https://t.me/"
SUPPORT_GROUP        = "https://t.me/"

# Attack Server API — default backend attack API endpoint
DEFAULT_API_URL = "https://automation.delvorn.in/attack"

# Key Pricing
DEFAULT_KEY_COST = 4   # Base credits cost per key for resellers
