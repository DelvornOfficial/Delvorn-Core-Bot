"""
remote_keys.py — Remote Key Verification & Sync Engine
======================================================
Connects Bot Modified to the APK / Payment server at https://bot.delvorn.in/server.
Enables cross-bot key redemption so keys bought from the payment bot can be redeemed
directly in this stresser bot, while this bot's own local keys continue to work seamlessly.
"""

import logging
import re
import aiohttp
import json
from typing import Dict, Any, Tuple, Optional
import database as db

logger = logging.getLogger(__name__)

DEFAULT_AUTH_SERVER = "https://bot.delvorn.in/server"


def mask_sensitive_urls(text: str) -> str:
    """Masks backend sensitive URLs like bot.delvorn.in and jonathan.delvorn.in from user messages."""
    if not text:
        return ""
    # Mask full URLs containing sensitive domains
    text = re.sub(r'https?://(?:bot|jonathan)\.delvorn\.in[^\s<>"\']*', '[PROTECTED_SERVER]', text, flags=re.IGNORECASE)
    # Mask bare domain references
    text = re.sub(r'(?:bot|jonathan)\.delvorn\.in', '[PROTECTED_SERVER]', text, flags=re.IGNORECASE)
    return text


async def get_remote_server_urls() -> list[str]:
    """Fetch configured remote auth server endpoint(s) from DB settings or fallback."""
    try:
        raw = await db.get_setting("remote_auth_server")
        if raw and raw.strip():
            urls = []
            for item in re.split(r'[,;\s]+', raw.strip()):
                item = item.strip()
                if not item:
                    continue
                if not item.startswith("http://") and not item.startswith("https://"):
                    item = f"https://{item}"
                clean = item.rstrip("/")
                if not clean.endswith("/server"):
                    clean = f"{clean}/server"
                if clean not in urls:
                    urls.append(clean)
            if urls:
                return urls
    except Exception:
        pass
    return [DEFAULT_AUTH_SERVER]


async def get_remote_server_url() -> str:
    """Returns comma-separated remote server URLs for display / compatibility."""
    urls = await get_remote_server_urls()
    return ", ".join(urls)


async def set_remote_server_url(new_url: str) -> str:
    """Admin updates the remote auth server endpoint(s). Supports comma-separated list."""
    urls = []
    for item in re.split(r'[,;\s]+', new_url.strip()):
        item = item.strip()
        if not item:
            continue
        if not item.startswith("http://") and not item.startswith("https://"):
            item = f"https://{item}"
        clean = item.rstrip("/")
        if not clean.endswith("/server"):
            clean = f"{clean}/server"
        if clean not in urls:
            urls.append(clean)
    
    clean_str = ", ".join(urls) if urls else DEFAULT_AUTH_SERVER
    await db.set_setting("remote_auth_server", clean_str)
    return clean_str


async def ping_remote_server() -> Tuple[bool, str]:
    """Test connectivity to remote auth server(s)."""
    urls = await get_remote_server_urls()
    headers = {"User-Agent": "DelvornStresserBot/2.0"}
    online_count = 0
    errors = []

    for base_url in urls:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{base_url}?game=pubg&key=ping_test", headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    raw_text = await resp.text()
                    try:
                        data = json.loads(raw_text)
                    except Exception:
                        data = None

                    if resp.status in (200, 400, 404) and isinstance(data, dict):
                        online_count += 1
                    else:
                        errors.append(f"HTTP {resp.status}")
        except Exception as e:
            logger.error(f"[Ping] Error contacting {base_url}: {e}")
            errors.append("Unreachable")

    if online_count > 0:
        return True, f"Online ({online_count}/{len(urls)} active)"
    return False, ", ".join(errors) or "Connection Failed"


async def verify_remote_key(key_code: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Calls configured remote auth server(s) to verify and activate a license key.
    
    Parameters:
      key_code: The license key entered by user (e.g. DelvornCheat-1d-xxxx)
      user_id: Telegram user ID for device binding (tg_<user_id>)

    Returns:
      Dict with status, success, message, remaining_seconds, duration_label, etc.
    """
    key = (key_code or "").strip()
    if not key:
        return {"success": False, "status": False, "message": "Key cannot be empty."}

    urls = await get_remote_server_urls()
    device_id = f"tg_{user_id}" if user_id else "tg_bot_redeem"

    params = {
        "game": "pubg",
        "key": key,
        "serial": device_id,
        "device_id": device_id
    }
    headers = {
        "User-Agent": "DelvornStresserBot/2.0",
        "Accept": "application/json"
    }

    last_res = None
    for base_url in urls:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(base_url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    raw_text = await resp.text()
                    try:
                        data = json.loads(raw_text)
                    except Exception:
                        data = None

                    if not isinstance(data, dict):
                        logger.warning(f"[RemoteKey] Non-JSON response (HTTP {resp.status}) from {base_url}: {raw_text[:200]}")
                        last_res = {
                            "success": False,
                            "status": False,
                            "message": f"Auth server returned HTTP {resp.status} (invalid format)."
                        }
                        continue

                    status_ok = data.get("status") is True or data.get("success") is True or data.get("code") == 200
                    raw_message = data.get("message") or data.get("reason") or "Unknown response"
                    message = mask_sensitive_urls(str(raw_message))

                    if status_ok:
                        rem_sec = int(data.get("remaining_seconds") or 0)
                        dur_label = data.get("duration") or ""

                        # If remaining_seconds is 0, parse from key code or duration label
                        if rem_sec <= 0:
                            rem_sec, parsed_label = parse_seconds_from_key(key, dur_label)
                            if not dur_label:
                                dur_label = parsed_label

                        return {
                            "success": True,
                            "status": True,
                            "code": 200,
                            "message": message,
                            "key": key,
                            "duration_label": dur_label or "1d",
                            "remaining_seconds": rem_sec if rem_sec > 0 else 86400,
                            "game": data.get("game", "pubg"),
                            "raw": data
                        }
                    else:
                        last_res = {
                            "success": False,
                            "status": False,
                            "code": data.get("code", 400),
                            "message": message,
                            "key": key,
                            "raw": data
                        }
                        # If key is simply not registered on this server, try the next fallback server
                        msg_low = message.lower()
                        if "not registered" in msg_low or "not found" in msg_low or "invalid key" in msg_low or data.get("code") == 404:
                            continue
                        else:
                            return last_res
        except Exception as e:
            logger.error(f"[RemoteKey] Error contacting remote auth server ({base_url}): {e}")
            last_res = {
                "success": False,
                "status": False,
                "message": "Auth server unreachable. Please try again later."
            }
            continue

    return last_res or {
        "success": False,
        "status": False,
        "message": "Auth server unreachable. Please try again later."
    }


def parse_seconds_from_key(key_code: str, fallback_label: str = "") -> Tuple[int, str]:
    """
    Extracts duration in seconds from DelvornCheat-<duration>-<random> or similar format.
    Examples:
      DelvornCheat-1h-K9p3X2y8 -> (3600, "1h")
      DelvornCheat-2h-xxxx -> (7200, "2h")
      DelvornCheat-1d-xxxx -> (86400, "1d")
      DelvornCheat-7d-xxxx -> (604800, "7d")
      DelvornCheat-30d-xxxx -> (2592000, "30d")
    """
    parts = key_code.strip().split("-")
    tag = ""
    if len(parts) >= 2:
        tag = parts[1].lower().strip()
    elif fallback_label:
        tag = fallback_label.lower().strip()

    if tag.endswith("h"):
        try:
            val = int(tag[:-1])
            return val * 3600, f"{val} Hour(s)"
        except ValueError:
            pass
    elif tag.endswith("d"):
        try:
            val = int(tag[:-1])
            return val * 86400, f"{val} Day(s)"
        except ValueError:
            pass
    elif tag.endswith("m"):
        try:
            val = int(tag[:-1])
            return val * 60, f"{val} Minute(s)"
        except ValueError:
            pass
    elif tag.isdigit():
        val = int(tag)
        return val * 86400, f"{val} Day(s)"

    # Check fallback regex
    m = re.search(r'(\d+)\s*(?:hour|hr|h\b)', fallback_label.lower())
    if m:
        h = int(m.group(1))
        return h * 3600, f"{h} Hour(s)"

    m_d = re.search(r'(\d+)\s*(?:day|d\b)', fallback_label.lower())
    if m_d:
        d = int(m_d.group(1))
        return d * 86400, f"{d} Day(s)"

    # Default 1 day
    return 86400, "1 Day(s)"
