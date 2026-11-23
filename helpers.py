"""helpers.py — shared utility functions used across all modules."""
import time
from config import ADMIN_IDS
import database as db


def is_owner(user_id: int) -> bool:
    """Check if user is a hardcoded owner (highest privilege)."""
    return user_id in ADMIN_IDS


async def is_db_admin(user_id: int) -> bool:
    """Check if user is an owner-appointed admin (stored in DB)."""
    if is_owner(user_id):
        return True
    return await db.is_db_admin(user_id)


def is_admin(user_id: int) -> bool:
    """Sync check — only for owner (hardcoded). Use is_any_admin() for full check."""
    return user_id in ADMIN_IDS


async def is_any_admin(user_id: int) -> bool:
    """Async check — owner OR DB admin."""
    if is_owner(user_id):
        return True
    return await db.is_db_admin(user_id)


async def is_reseller(user_id: int) -> bool:
    """True if user is owner, DB admin, or reseller."""
    if is_owner(user_id):
        return True
    if await db.is_db_admin(user_id):
        return True
    return await db.get_reseller(user_id) is not None


async def can_generate_key(user_id: int) -> bool:
    """Owner & Admin can generate keys; Reseller needs credits >= 4."""
    if is_owner(user_id):
        return True
    if await db.is_db_admin(user_id):
        return True
    r = await db.get_reseller(user_id)
    if not r:
        return False
    if r.get("expiry", 0) > 0 and int(time.time()) > r["expiry"]:
        return False
    return r.get("credits", 0) >= 4


def make_progress_bar(elapsed: int, total: int, width: int = 18) -> str:
    ratio  = max(0.0, min(1.0, elapsed / total)) if total else 1.0
    filled = int(ratio * width)
    bar    = "█" * filled + "░" * (width - filled)
    pct    = int(ratio * 100)
    return f"[{bar}] {pct}%"


def format_expiry(ts: int) -> str:
    if ts <= 0:
        return "Never"
    remaining = ts - int(time.time())
    if remaining <= 0:
        return "Expired"
    days    = remaining // 86400
    hours   = (remaining % 86400) // 3600
    minutes = (remaining % 3600) // 60
    parts   = []
    if days:    parts.append(f"{days}d")
    if hours:   parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    return " ".join(parts) or "<1m"


def fmt(ts: int) -> str:
    """Timestamp → human readable date."""
    if ts <= 0:
        return "—"
    import datetime
    return datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M UTC")


def escape_md(text: str) -> str:
    """Escape special characters for Telegram Markdown V1."""
    if not text:
        return ""
    for ch in ['_', '*', '`', '[']:
        text = text.replace(ch, f"\\{ch}")
    return text


# Re-export premium emoji helpers
from emojis import get_custom_emoji_html, get_custom_emoji_md, EMOJI_CATALOG, HTML_EMOJI, animate_text


def install_emoji_interceptor(application=None):
    """Intercepts all outgoing bot messages and edits directly via Bot._send_message
    to automatically replace every standard emoji with custom animated emojis,
    and strips any HTML/markdown tags from inline keyboard buttons so buttons display clean emojis."""
    import re
    from telegram import Bot

    orig_send_message = Bot._send_message

    def clean_button_text(btn_text: str) -> str:
        if not btn_text or not isinstance(btn_text, str):
            return btn_text
        # Strip <tg-emoji ...>fallback</tg-emoji> -> fallback
        cleaned = re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', btn_text)
        # Strip ![fallback](tg://emoji?id=...) -> fallback
        cleaned = re.sub(r'!\[(.*?)\]\(tg://emoji\?id=\d+\)', r'\1', cleaned)
        return cleaned

    async def animated_send_message_internal(self, *args, **kwargs):
        # args usually: (endpoint, data, ...)
        if len(args) >= 2 and isinstance(args[1], dict):
            data = args[1]
            pm = data.get("parse_mode") or kwargs.get("parse_mode") or "HTML"
            mode = "html" if str(pm).lower() == "html" else "md"
            if "text" in data and isinstance(data["text"], str):
                data["text"] = animate_text(data["text"], mode=mode)
            if "caption" in data and isinstance(data["caption"], str):
                data["caption"] = animate_text(data["caption"], mode=mode)

            # Sanitize reply_markup in kwargs or data
            rm = kwargs.get("reply_markup") or data.get("reply_markup")
            if rm and hasattr(rm, "inline_keyboard"):
                for row in rm.inline_keyboard:
                    for btn in row:
                        if hasattr(btn, "text") and btn.text:
                            btn.text = clean_button_text(btn.text)
            elif isinstance(rm, dict) and "inline_keyboard" in rm:
                for row in rm["inline_keyboard"]:
                    for btn in row:
                        if isinstance(btn, dict) and "text" in btn:
                            btn["text"] = clean_button_text(btn["text"])

        return await orig_send_message(self, *args, **kwargs)

    Bot._send_message = animated_send_message_internal




