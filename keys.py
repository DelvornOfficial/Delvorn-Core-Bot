import time, secrets, string, html, logging, re, aiohttp, json
from typing import Dict, Any, Tuple, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from helpers import is_admin, is_owner, is_any_admin, can_generate_key, format_expiry, fmt, escape_md, HTML_EMOJI
import database as db
import key_pricing as kp



def _gen_code(prefix: str, duration_str: str) -> str:
    chars = string.ascii_uppercase + string.digits
    rand5 = "".join(secrets.choice(chars) for _ in range(5))
    return f"{prefix}-{duration_str}-{rand5}"


def parse_duration_str(s: str) -> tuple[int, str] | None:
    """Parses duration strings like '1h', '2h', '3d', '10d', or pure digits as days.
    Returns (valid_seconds, display_str) or None."""
    s = s.strip().lower()
    if not s:
        return None
    try:
        if s.endswith("h"):
            val = int(s[:-1])
            if val <= 0:
                return None
            return (val * 3600, f"{val}h")
        elif s.endswith("d"):
            val = int(s[:-1])
            if val <= 0:
                return None
            return (val * 86400, f"{val}d")
        elif s.endswith("m"):
            val = int(s[:-1])
            if val <= 0:
                return None
            return (val * 60, f"{val}m")
        elif s.isdigit():
            val = int(s)
            if val <= 0:
                return None
            return (val * 86400, f"{val}d")
    except ValueError:
        return None
    return None


async def _do_genkey(update, user_id: int, duration_sec: int, duration_label: str,
                     max_users: int = 1, attack_time: int = 240, cooldown: int = 30,
                     is_trial: bool = False) -> str | None:
    """Core logic: check credits, generate key, insert to DB. Returns code or None."""
    if not await can_generate_key(user_id):
        await update.message.reply_text(f"{HTML_EMOJI.CROSS} <b>No credits left!</b> Ask admin to add credits.", parse_mode="HTML")
        return None

    # Calculate dynamic cost (unit_price * max_users)
    if is_trial:
        unit_price, total_cost = 0, 0
    else:
        unit_price, total_cost = await kp.calculate_key_cost(duration_label, devices=max_users)

    is_owner_user = is_owner(user_id)
    adm = await db.get_admin(user_id) if not is_owner_user else None
    r   = await db.get_reseller(user_id) if (not is_owner_user and not adm) else None

    # Appointed Admin and Reseller both spend balance to generate keys; only Owner is unlimited (Trials are free)
    if not is_owner_user and not is_trial:
        if adm:
            if adm.get("expiry", 0) > 0 and int(time.time()) > adm["expiry"]:
                await update.message.reply_text(f"{HTML_EMOJI.CROSS} <b>Your Admin access has expired!</b> Contact owner.", parse_mode="HTML")
                return None
            if adm.get("balance", 0) < total_cost:
                await update.message.reply_text(
                    f"{HTML_EMOJI.CROSS} <b>Insufficient credits!</b>\n"
                    f"Generating this key requires <code>{total_cost}</code> credits (<code>{unit_price}</code> pts/dev × <code>{max_users}</code> devs).\n"
                    f"Your balance: <code>{adm.get('balance', 0)}</code> credits.",
                    parse_mode="HTML"
                )
                return None
        elif r:
            if r.get("expiry", 0) > 0 and int(time.time()) > r["expiry"]:
                await update.message.reply_text(f"{HTML_EMOJI.CROSS} <b>Your Reseller subscription has expired!</b> Contact admin.", parse_mode="HTML")
                return None
            if r.get("credits", 0) < total_cost:
                await update.message.reply_text(
                    f"{HTML_EMOJI.CROSS} <b>Insufficient credits!</b>\n"
                    f"Generating this key requires <code>{total_cost}</code> credits (<code>{unit_price}</code> pts/dev × <code>{max_users}</code> devs).\n"
                    f"Your balance: <code>{r.get('credits', 0) if r else 0}</code> credits.",
                    parse_mode="HTML"
                )
                return None
        else:
            await update.message.reply_text(f"{HTML_EMOJI.CROSS} <b>Unauthorized!</b> Only Owner, Admin, or Reseller can generate keys.", parse_mode="HTML")
            return None

    from branding import KEY_PREFIX as BRAND_PREFIX
    uname = update.effective_user.username if update.effective_user else None
    if is_owner_user:
        prefix = BRAND_PREFIX
    elif adm:
        clean_name = uname.lstrip("@") if uname else f"Admin_{user_id}"
        prefix = f"Admin_{clean_name.capitalize()}"
    else:
        clean_name = uname.lstrip("@") if uname else f"Reseller_{user_id}"
        prefix = clean_name.capitalize() if clean_name else f"Reseller_{user_id}"

    # Ensure the first letter of prefix is always uppercase
    if prefix:
        prefix = prefix[0].upper() + prefix[1:]

    code = _gen_code(prefix, duration_label)
    await db.insert_key(code, attack_time, cooldown, duration_sec, int(time.time()), is_trial, user_id, max_users=max_users)

    # Deduct credits (Owner is free/unlimited; Admin & Reseller pay from their balance for non-trials)
    if not is_owner_user and not is_trial:
        if adm:
            await db.update_admin_balance(user_id, -total_cost)
        elif r:
            await db.update_reseller_credits(user_id, -total_cost)
            await db.increment_reseller_keys(user_id)

    return code


# ── /genkey <duration> [limit] [attack_time] [cooldown] ─────────
async def genkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin_user = await is_any_admin(user_id)
    if not (is_admin_user or await db.get_reseller(user_id)):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Reseller or Admin only.</b>", parse_mode="HTML")

    args = context.args
    if len(args) < 1:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/genkey &lt;duration&gt; [limit] [attack_time] [cooldown]</code>\n\n"
            f"{HTML_EMOJI.PIN} <b>Examples:</b>\n"
            "• <code>/genkey 1h</code> — 1 Hour key (1 user)\n"
            "• <code>/genkey 3d 5</code> — 3 Days key (redeemable by 5 users)\n"
            "• <code>/genkey 30d 1 240 30</code> — 30 Days key (1 user, 240s attack, 30s cd)",
            parse_mode="HTML"
        )

    parsed = parse_duration_str(args[0])
    if not parsed:
        return await update.message.reply_text(
            f"{HTML_EMOJI.CROSS} <b>Invalid duration format!</b>\nUse e.g. <code>1h</code>, <code>2h</code>, <code>3d</code>, <code>10d</code>, <code>30d</code>.",
            parse_mode="HTML"
        )

    duration_sec, duration_label = parsed
    max_users   = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    def_time    = int(await db.get_setting("max_time")) if await db.get_setting("max_time") else 240
    def_cd      = int(await db.get_setting("cooldown")) if await db.get_setting("cooldown") else 30
    attack_time = int(args[2]) if len(args) > 2 and args[2].isdigit() else def_time
    cooldown    = int(args[3]) if len(args) > 3 and args[3].isdigit() else def_cd

    code = await _do_genkey(update, user_id, duration_sec, duration_label, max_users, attack_time, cooldown, is_trial=False)
    if not code:
        return

    bot_username = context.bot.username or ""
    bot_mention = f"@{bot_username}" if bot_username else "@DelvornStresserBot"
    redeem_link = f"https://t.me/{bot_username}?start=redeem_{code}" if bot_username else ""

    setup_link = await db.get_setting("setup_link") or ""
    canary_link = await db.get_setting("canary_link") or ""

    # Balance info if non-owner generated it
    credit_line = ""
    if not is_owner(user_id):
        adm_cur = await db.get_admin(user_id)
        r_cur   = await db.get_reseller(user_id) if not adm_cur else None
        _, total_deducted = await kp.calculate_key_cost(duration_label, devices=max_users)
        if adm_cur:
            rem_creds = adm_cur.get("balance", 0)
            credit_line = f"\n{HTML_EMOJI.CREDIT} <b>Your Remaining Balance:</b> <code>{rem_creds}</code> credits (-{total_deducted} deducted)"
        elif r_cur:
            rem_creds = r_cur.get("credits", 0)
            credit_line = f"\n{HTML_EMOJI.CREDIT} <b>Your Remaining Balance:</b> <code>{rem_creds}</code> credits (-{total_deducted} deducted)"

    guide_lines = ""
    if setup_link and setup_link != "https://t.me/":
        guide_lines += f"{HTML_EMOJI.TV} <b>Setup Guide:</b> {setup_link}\n"
    if canary_link and canary_link != "https://t.me/":
        guide_lines += f"{HTML_EMOJI.CANARY} <b>Canary Link:</b> {canary_link}\n"

    msg = (
        "╔═════════════════════════════╗\n"
        f"   {HTML_EMOJI.CROWN} <b>DELVORN VIP ACCESS KEY</b> {HTML_EMOJI.CROWN}\n"
        "╚═════════════════════════════╝\n\n"
        f"{HTML_EMOJI.SPARKLES} <b>Key Details:</b>\n"
        f"┌ {HTML_EMOJI.KEY} <b>Key:</b> <i>(tap code to copy)</i>\n"
        f"└ <code>{code}</code>\n\n"
        f"┌ {HTML_EMOJI.HOURGLASS} <b>Plan Validity:</b> <code>{duration_label}</code>\n"
        f"├ {HTML_EMOJI.PROFILE} <b>Max Users:</b> <code>{max_users} user(s)</code>\n"
        f"├ {HTML_EMOJI.TIMER} <b>Attack Duration:</b> <code>{attack_time}s</code>\n"
        f"└ {HTML_EMOJI.COOLDOWN} <b>Attack Cooldown:</b> <code>{cooldown}s</code>\n"
        f"{credit_line}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.DOWNLOAD} <b>How To Redeem:</b>\n"
        f"{HTML_EMOJI.NUM_1} Open Bot: {bot_mention}\n"
        f"{HTML_EMOJI.NUM_2} Send command <i>(tap to copy)</i>:\n"
        f"<code>/redeem {code}</code>\n\n"
        f"{guide_lines}"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    buttons = []
    if bot_username:
        buttons.append([InlineKeyboardButton("🚀 One-Tap Redeem", url=f"https://t.me/{bot_username}")])
    guide_row = []
    if setup_link and setup_link != "https://t.me/":
        guide_row.append(InlineKeyboardButton("🎬 Watch Setup Video", url=setup_link))
    if canary_link and canary_link != "https://t.me/":
        guide_row.append(InlineKeyboardButton("📥 Download Canary", url=canary_link))
    if guide_row:
        buttons.append(guide_row)

    markup = InlineKeyboardMarkup(buttons)

    try:
        await update.message.reply_text(
            msg,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception:
        # Fallback to plain text if HTML parsing has issues
        await update.message.reply_text(
            msg.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "").replace("<i>", "").replace("</i>", ""),
            reply_markup=markup,
            disable_web_page_preview=True
        )



# ── /delkey <key> (Admin: any key; Reseller: only their own keys) ─
async def delete_code_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin_user = await is_any_admin(user_id)
    is_res = await db.get_reseller(user_id) if not is_admin_user else None

    if not is_admin_user and not is_res:
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin or Reseller only.</b>", parse_mode="HTML")

    if not context.args:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/delkey &lt;key&gt;</code>", parse_mode="HTML")

    code = context.args[0].strip()
    key  = await db.get_key(code)
    if not key:
        key = await db.get_key(code.upper())
    if not key:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Key code not found in database.", parse_mode="HTML")

    # Non-owners can only delete keys they created
    if not is_owner(user_id):
        if key.get("created_by") != user_id:
            return await update.message.reply_text(
                f"{HTML_EMOJI.BAN} <b>Access Denied!</b>\nYou can only delete keys that you generated.",
                parse_mode="HTML"
            )

    await db.delete_key(key["code"])
    await update.message.reply_text(
        f"{HTML_EMOJI.TRASH} <b>Key Deleted &amp; Access Revoked!</b>\n\n"
        f"{HTML_EMOJI.KEY} Key: <code>{html.escape(key['code'])}</code>\n"
        f"{HTML_EMOJI.LIGHTNING} All users who redeemed this key have been instantly terminated.",
        parse_mode="HTML"
    )


# ── /gentrial <time> [count | device] ─────────────────
async def gentrial_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_any_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only. Free trial keys can only be generated by Admin.</b>", parse_mode="HTML")

    args = context.args
    if not args:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/gentrial &lt;time&gt; [count | device]</code>\n\n"
            f"{HTML_EMOJI.PIN} <b>Examples:</b>\n"
            "• <code>/gentrial 24h</code> — 1 Trial key (1 device, 24h)\n"
            "• <code>/gentrial 2h 5</code> — 5 Trial keys (1 device each, 2h)\n"
            "• <code>/gentrial 1d 3dev</code> (or <code>3device</code> / <code>dev3</code>) — 1 Trial key redeemable by 3 devices\n\n"
            f"{HTML_EMOJI.WARN} <i>Note: If key count is set, device cannot be set; if device is set, only 1 key is generated.</i>",
            parse_mode="HTML"
        )

    parsed = parse_duration_str(args[0])
    if not parsed:
        return await update.message.reply_text(
            f"{HTML_EMOJI.CROSS} <b>Invalid time format!</b>\nUse e.g. <code>1h</code>, <code>2h</code>, <code>24h</code>, <code>1d</code>, <code>3d</code>.",
            parse_mode="HTML"
        )

    duration_sec, duration_label = parsed

    count = 1
    max_devices = 1

    if len(args) >= 2:
        second_arg = args[1].lower().strip()
        # Check if user specified device (e.g. "3dev", "3device", "dev3", "device3")
        is_dev_spec = False
        dev_val = 0
        if "dev" in second_arg:
            clean = second_arg.replace("device", "").replace("dev", "").strip()
            if clean.isdigit():
                dev_val = int(clean)
                is_dev_spec = True
        
        if is_dev_spec:
            if dev_val <= 0:
                return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Device count must be greater than 0.", parse_mode="HTML")
            max_devices = dev_val
            count = 1  # When device is set, key count is strictly 1
        elif second_arg.isdigit():
            # Key count is set, device remains 1
            count = int(second_arg)
            if count <= 0 or count > 50:
                return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Key count must be between 1 and 50.", parse_mode="HTML")
            max_devices = 1
        else:
            return await update.message.reply_text(
                f"{HTML_EMOJI.CROSS} <b>Invalid argument!</b>\nPass either key count (e.g. <code>5</code>) OR device count (e.g. <code>3dev</code>). Both cannot be set together.",
                parse_mode="HTML"
            )

    codes = []
    for _ in range(count):
        code = await _do_genkey(
            update, user_id, duration_sec, duration_label,
            max_users=max_devices, attack_time=120, cooldown=60, is_trial=True
        )
        if code:
            codes.append(code)

    if not codes:
        return

    # Fetch configured setup and canary links
    setup_link = await db.get_setting("setup_link") or ""
    canary_link = await db.get_setting("canary_link") or ""

    code_list = "\n".join(f"<code>{c}</code>" for c in codes)
    dev_info = f"{HTML_EMOJI.PROFILE} <b>Device Limit:</b> <code>{max_devices} device(s)</code>" if max_devices > 1 else f"{HTML_EMOJI.PROFILE} <b>Device Limit:</b> <code>1 device</code>"
    count_info = f"{HTML_EMOJI.BOX} <b>{len(codes)} Trial Key(s) Generated</b>" if len(codes) > 1 else f"{HTML_EMOJI.BOX} <b>Trial Key Generated</b>"

    guide_lines = ""
    if setup_link and setup_link != "https://t.me/":
        guide_lines += f"{HTML_EMOJI.TV} <b>Setup Guide:</b> {setup_link}\n"
    if canary_link and canary_link != "https://t.me/":
        guide_lines += f"{HTML_EMOJI.CANARY} <b>Canary Link:</b> {canary_link}\n"

    first_code = codes[0] if codes else "CODE"
    html_msg = (
        f"{count_info} (<b>{duration_label}</b>)\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.KEY} <b>Key(s):</b> <i>(tap code to copy)</i>\n"
        f"{code_list}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.HOURGLASS} <b>Validity:</b> <code>{duration_label}</code>\n"
        f"{dev_info}\n"
        f"{guide_lines}"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.ARROW_RIGHT} <b>Redeem:</b> <code>/redeem {first_code}</code>"
    )

    await update.message.reply_text(
        html_msg,
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# ── /gentrialfor <id> <hours> [count] ───────────────
async def gentrialfor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_any_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    args = context.args
    if len(args) < 2:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/gentrialfor &lt;user_id&gt; &lt;hours&gt; [count]</code>\n\n"
            f"{HTML_EMOJI.PIN} <b>Examples:</b>\n"
            "• <code>/gentrialfor 123456789 2</code> — 1 Trial key (2 hours)\n"
            "• <code>/gentrialfor 123456789 24 5</code> — 5 Trial keys (24 hours each)",
            parse_mode="HTML"
        )
    try:
        target_id = int(args[0])
        hours     = int(args[1])
        count     = int(args[2]) if len(args) >= 3 else 1
    except ValueError:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Must be numeric values.", parse_mode="HTML")

    if hours <= 0:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Hours must be greater than 0.", parse_mode="HTML")
    if count <= 0 or count > 50:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Key count must be between 1 and 50.", parse_mode="HTML")

    from branding import KEY_PREFIX as BRAND_PREFIX
    is_owner_user = is_owner(user_id)
    adm = await db.get_admin(user_id) if not is_owner_user else None
    uname = update.effective_user.username if update.effective_user else None
    if is_owner_user:
        prefix = BRAND_PREFIX
    elif adm:
        clean_name = uname.lstrip("@") if uname else f"Admin_{user_id}"
        prefix = f"Admin_{clean_name.capitalize()}"
    else:
        prefix = BRAND_PREFIX

    if prefix:
        prefix = prefix[0].upper() + prefix[1:]

    codes = []
    duration_sec = hours * 3600
    for _ in range(count):
        code = _gen_code(prefix, f"{hours}h")
        await db.insert_key(code, 120, 60, duration_sec, int(time.time()), True, user_id, max_users=1)
        codes.append(code)

    code_list = "\n".join(f"<code>{c}</code>" for c in codes)
    count_label = f"{count} Trial Keys Assigned to User" if count > 1 else "Trial Key Assigned to User"
    await update.message.reply_text(
        f"{HTML_EMOJI.BOX} <b>{count_label}</b> <code>{target_id}</code>\n"
        f"{HTML_EMOJI.HOURGLASS} <b>Duration:</b> <code>{hours}h</code> each\n\n"
        f"{code_list}",
        parse_mode="HTML"
    )
    try:
        msg = (
            f"{HTML_EMOJI.BOX} <b>You received {count} trial key(s)!</b> (<code>{hours}h</code>)\n\n"
            f"{code_list}\n\n"
            f"{HTML_EMOJI.ARROW_RIGHT} Redeem with: <code>/redeem {codes[0]}</code>"
        )
        await context.bot.send_message(chat_id=target_id, text=msg, parse_mode="HTML")
    except Exception:
        pass


# ── /deletetrials ──────────────────────────────────
async def deletetrials_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_any_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    count = await db.delete_trial_keys()
    await update.message.reply_text(f"{HTML_EMOJI.TRASH} Deleted <code>{count}</code> unused trial key(s).", parse_mode="HTML")


# ── /list_codes ───────────────────────────────────
async def list_codes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin_user = await is_any_admin(user_id)
    is_res = await db.get_reseller(user_id) if not is_admin_user else None

    if not is_admin_user and not is_res:
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin or Reseller only.</b>", parse_mode="HTML")

    if is_owner(user_id):
        all_keys = await db.get_all_keys()
    else:
        all_keys = [k for k in await db.get_all_keys() if k.get("created_by") == user_id]
    if not all_keys:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} No keys found in database.", parse_mode="HTML")

    lines = [f"{HTML_EMOJI.KEY} <b>All Keys ({len(all_keys)})</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
    for k in all_keys[:30]:
        used_count = k.get("used_count", 0)
        max_users = k.get("max_users", 1)
        status = f"{HTML_EMOJI.BAN} Blocked" if k["is_blocked"] else (f"{HTML_EMOJI.CHECKMARK} Used ({used_count}/{max_users})" if used_count >= max_users else f"{HTML_EMOJI.CHECKMARK} Available ({used_count}/{max_users})")
        trial  = " [TRIAL]" if k["is_trial"] else ""
        lines.append(f"• <code>{html.escape(k['code'])}</code>{trial} — {status}")

    if len(all_keys) > 30:
        lines.append(f"\n<i>...and {len(all_keys)-30} more keys</i>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /block_code <code> ────────────────────────────
async def block_code_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_any_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    if not context.args:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/block_code &lt;code&gt;</code>", parse_mode="HTML")

    code = context.args[0].strip()
    key  = await db.get_key(code)
    if not key:
        key = await db.get_key(code.upper())
    if not key:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Key code not found in database.", parse_mode="HTML")

    # Non-owners cannot block Owner keys
    if not is_owner(user_id) and is_owner(key.get("created_by", 0)):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Access Denied!</b> You cannot block Owner keys.", parse_mode="HTML")

    if key.get("is_blocked"):
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} Key <code>{html.escape(key['code'])}</code> is already blocked.", parse_mode="HTML")

    await db.block_key(key["code"])
    await update.message.reply_text(
        f"{HTML_EMOJI.BAN} <b>Key Blocked!</b>\n\n"
        f"{HTML_EMOJI.KEY} Code: <code>{html.escape(key['code'])}</code>\n"
        f"{HTML_EMOJI.WARN} Status: Blocked from being redeemed.",
        parse_mode="HTML"
    )


# ── /unblock_code <code> ──────────────────────────
async def unblock_code_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_any_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    if not context.args:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/unblock_code &lt;code&gt;</code>", parse_mode="HTML")

    code = context.args[0].strip()
    key  = await db.get_key(code)
    if not key:
        key = await db.get_key(code.upper())
    if not key:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Key code not found in database.", parse_mode="HTML")

    if not is_owner(user_id) and is_owner(key.get("created_by", 0)):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Access Denied!</b> You cannot manage Owner keys.", parse_mode="HTML")

    if not key.get("is_blocked"):
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} Key <code>{html.escape(key['code'])}</code> is not blocked.", parse_mode="HTML")

    await db.unblock_key(key["code"])
    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Key Unblocked!</b>\n\n"
        f"{HTML_EMOJI.KEY} Code: <code>{html.escape(key['code'])}</code>\n"
        f"{HTML_EMOJI.CHECKMARK} Status: Active and redeemable.",
        parse_mode="HTML"
    )


# ── REMOTE AUTH SERVER INTEGRATION (https://bot.delvorn.in/server) ──

DEFAULT_AUTH_SERVER = "https://bot.delvorn.in/server"
logger = logging.getLogger(__name__)


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


def parse_seconds_from_key(key_code: str, fallback_label: str = "") -> Tuple[int, str]:
    """Extracts duration in seconds from DelvornCheat-<duration>-<random> format."""
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

    m = re.search(r'(\d+)\s*(?:hour|hr|h\b)', fallback_label.lower())
    if m:
        h = int(m.group(1))
        return h * 3600, f"{h} Hour(s)"

    m_d = re.search(r'(\d+)\s*(?:day|d\b)', fallback_label.lower())
    if m_d:
        d = int(m_d.group(1))
        return d * 86400, f"{d} Day(s)"

    return 86400, "1 Day(s)"


async def verify_remote_key(key_code: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    """Calls configured remote auth server(s) to verify and activate a license key."""
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
                        # If key is simply not found on this server, try the next fallback server
                        msg_low = message.lower()
                        if "not registered" in msg_low or "not found" in msg_low or "invalid key" in msg_low or data.get("code") == 404:
                            continue
                        else:
                            # Specific error like already bound/expired on this server
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


# ── UNIVERSAL KEY REDEMPTION ENGINE ───────────────
async def redeem_key_internal(user, code: str) -> tuple[bool, str]:
    """
    Core universal key redemption engine:
    1. Checks local PostgreSQL database (keys table).
       - Supports keys generated by Admin, Owner, Resellers, and Trials.
       - Enforces max_users, blocked status, expiration.
    2. If not found locally, queries remote auth server.
       - If verified and active, binds user, calculates expiration, and inserts
         into local database so it cannot be double-redeemed.
    3. Activates user access in users table.
    
    Returns (success, message_html).
    """
    user_id = user.id
    code = code.strip()

    # ── Check if user already has an active key ────────────
    # A user cannot redeem another key while their current key is still active
    if not is_owner(user_id):
        u = await db.get_user(user_id)
        now = int(time.time())
        if u and u.get("key_used"):
            curr_expiry = u.get("expiry", 0)
            # Active if lifetime (expiry == 0) or future timestamp (expiry > now)
            if curr_expiry == 0 or curr_expiry > now:
                exp_display = "Lifetime (Permanent)" if curr_expiry == 0 else format_expiry(curr_expiry)
                return False, (
                    f"{HTML_EMOJI.WARN} <b>Key Already Active!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "You already have an active access key on your account!\n\n"
                    f"{HTML_EMOJI.KEY} <b>Active Key:</b> <code>{html.escape(u.get('key_used', 'Active'))}</code>\n"
                    f"{HTML_EMOJI.CALENDAR} <b>Expiry Date:</b> <code>{exp_display}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "⚠️ <b>Note:</b> You cannot redeem a new key until your current key expires."
                )

    # Step 1: Check Local Database
    key = await db.get_key(code)
    if not key:
        key = await db.get_key(code.upper())
    if not key:
        key = await db.get_key(code.lower())

    if key:
        # Local key found!
        if key.get("is_blocked"):
            return False, f"{HTML_EMOJI.BAN} This key has been blocked by Admin."

        max_users = key.get("max_users", 1)
        used_count = key.get("used_count", 0)
        if used_count >= max_users:
            return False, f"{HTML_EMOJI.CROSS} This key has already reached its maximum redeem limit."

        # Shared key logic: first user sets the expiry for ALL users
        if key["valid_days"] > 0:
            is_shared = key.get("max_users", 1) > 1
            shared_expiry = key.get("shared_expiry_ts", 0)

            if is_shared and shared_expiry > 0:
                if int(time.time()) > shared_expiry:
                    return False, f"{HTML_EMOJI.CROSS} This key has expired."
                user_expiry = shared_expiry
            else:
                key_expiry = key["created_at"] + key["valid_days"]
                if int(time.time()) > key_expiry:
                    return False, f"{HTML_EMOJI.CROSS} This key has expired."
                user_expiry = int(time.time()) + key["valid_days"]
                if is_shared:
                    await db.set_key_shared_expiry(code, user_expiry)
        else:
            user_expiry = 0

        await db.use_key(code, user_id)
        await db.upsert_user(
            user_id      = user_id,
            username     = user.username or user.first_name or "",
            join_date    = int(time.time()),
            expiry       = user_expiry,
            max_time     = key["duration"],
            cooldown     = key["cooldown"],
            key_used     = code,
        )

        exp_str = format_expiry(user_expiry) if user_expiry > 0 else "Lifetime"
        setup_link = await db.get_setting("setup_link") or ""
        setup_line = f"\n{HTML_EMOJI.TV} <b>Setup Tutorial:</b> {setup_link}" if setup_link and setup_link != "https://t.me/" and "DelvornSetup" not in setup_link else ""
        msg = (
            f"{HTML_EMOJI.CHECKMARK} <b>Key Redeemed Successfully!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.KEY} <b>Code:</b> <code>{html.escape(code)}</code>\n"
            f"{HTML_EMOJI.SHIELD} <b>Key Type:</b> <code>Local VIP Key</code>\n"
            f"{HTML_EMOJI.TIMER} <b>Max Attack Duration:</b> <code>{key['duration']}s</code>\n"
            f"{HTML_EMOJI.COOLDOWN} <b>Attack Cooldown:</b> <code>{key['cooldown']}s</code>\n"
            f"{HTML_EMOJI.CALENDAR} <b>Access Validity:</b> <code>{exp_str}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.LIGHTNING} You can now launch attacks using:\n<code>/attack &lt;ip&gt; &lt;port&gt; &lt;time&gt;</code>\n\n"
            f"{HTML_EMOJI.BOOK} <b>View all commands:</b> Send <code>/help</code>"
            f"{setup_line}"
        )
        return True, msg

    # Step 2: Not in local DB -> Verify with remote server
    remote_res = await verify_remote_key(code, user_id=user_id)
    if remote_res.get("success") is True:
        # Remote key successfully verified & activated!
        rem_sec = int(remote_res.get("remaining_seconds", 86400))
        dur_label = remote_res.get("duration_label", "1d")

        def_time = int(await db.get_setting("max_time")) if await db.get_setting("max_time") else 240
        def_cd   = int(await db.get_setting("cooldown")) if await db.get_setting("cooldown") else 30

        user_expiry = int(time.time()) + rem_sec

        # Insert key into local database so it is registered, bound, and cannot be reused
        await db.insert_key(code, def_time, def_cd, rem_sec, int(time.time()), False, 0, max_users=1)
        await db.use_key(code, user_id)
        await db.upsert_user(
            user_id   = user_id,
            username  = user.username or user.first_name or "",
            join_date = int(time.time()),
            expiry    = user_expiry,
            max_time  = def_time,
            cooldown  = def_cd,
            key_used  = code,
        )

        exp_str = format_expiry(user_expiry)
        setup_link = await db.get_setting("setup_link") or ""
        setup_line = f"\n{HTML_EMOJI.TV} <b>Setup Tutorial:</b> {setup_link}" if setup_link and setup_link != "https://t.me/" and "DelvornSetup" not in setup_link else ""
        msg = (
            f"{HTML_EMOJI.CHECKMARK} <b>VIP Key Verified &amp; Redeemed!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.KEY} <b>Code:</b> <code>{html.escape(code)}</code>\n"
            f"{HTML_EMOJI.TIMER} <b>Max Attack Duration:</b> <code>{def_time}s</code>\n"
            f"{HTML_EMOJI.COOLDOWN} <b>Attack Cooldown:</b> <code>{def_cd}s</code>\n"
            f"{HTML_EMOJI.CALENDAR} <b>Plan Validity:</b> <code>{html.escape(dur_label)} ({exp_str})</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.LIGHTNING} You can now launch attacks using:\n<code>/attack &lt;ip&gt; &lt;port&gt; &lt;time&gt;</code>\n\n"
            f"{HTML_EMOJI.BOOK} <b>View all commands:</b> Send <code>/help</code>"
            f"{setup_line}"
        )
        return True, msg

    # Step 3: Failed both local and remote verification
    remote_err = mask_sensitive_urls(remote_res.get("message") or "Invalid key code.")
    return False, f"{HTML_EMOJI.CROSS} <b>Redemption Failed:</b> {html.escape(remote_err)}"


# ── /redeem <code> ────────────────────────────────
async def redeem_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not context.args:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/redeem &lt;code&gt;</code>\n"
            f"Example: <code>/redeem DelvornCheat-30d-XYZ12</code>",
            parse_mode="HTML"
        )

    code = context.args[0].strip()
    success, reply_text = await redeem_key_internal(user, code)
    await update.message.reply_text(
        reply_text,
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# ── /checkkey <code> ──────────────────────────────
async def checkkey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /checkkey <code> — Diagnostic command to inspect any key (Local DB + Remote Auth Server)
    """
    user_id = update.effective_user.id
    if not await is_any_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    if not context.args:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/checkkey &lt;code&gt;</code>\n"
            f"Example: <code>/checkkey DelvornCheat-1d-8gkAuN9Z</code>",
            parse_mode="HTML"
        )

    code = context.args[0].strip()
    status_lines = [
        f"{HTML_EMOJI.KEY} <b>KEY DIAGNOSTIC REPORT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Key Code: <code>{html.escape(code)}</code>\n"
    ]

    # 1. Check local DB
    local_key = await db.get_key(code)
    if not local_key:
        local_key = await db.get_key(code.upper())

    if local_key:
        used_c = local_key.get("used_count", 0)
        max_u = local_key.get("max_users", 1)
        blk = "Blocked (Yes)" if local_key.get("is_blocked") else "Active (No)"
        status_lines.append(
            f"📁 <b>Local Database:</b> {HTML_EMOJI.CHECKMARK} Found\n"
            f"• Max Users: <code>{used_c}/{max_u}</code>\n"
            f"• Attack Time: <code>{local_key.get('duration')}s</code>\n"
            f"• Cooldown: <code>{local_key.get('cooldown')}s</code>\n"
            f"• Blocked: <code>{blk}</code>\n"
            f"• Created: <code>{fmt(local_key.get('created_at', 0))}</code>\n"
        )
    else:
        status_lines.append(f"📁 <b>Local Database:</b> {HTML_EMOJI.CROSS} Not present in local DB\n")

    # 2. Check remote server
    remote_res = await verify_remote_key(code)
    if remote_res.get("success") is True:
        status_lines.append(
            f"🌐 <b>Remote Server:</b> {HTML_EMOJI.CHECKMARK} Valid &amp; Authorized\n"
            f"• Duration: <code>{remote_res.get('duration_label')}</code>\n"
            f"• Remaining: <code>{remote_res.get('remaining_seconds')}s</code>\n"
            f"• Game: <code>{remote_res.get('game', 'pubg')}</code>\n"
        )
    else:
        err = mask_sensitive_urls(remote_res.get("message") or "Invalid")
        status_lines.append(
            f"🌐 <b>Remote Server:</b> {HTML_EMOJI.CROSS} {html.escape(err)}\n"
        )

    status_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    await update.message.reply_text("\n".join(status_lines), parse_mode="HTML")


# ── /keypanel ─────────────────────────────────────
async def keypanel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /keypanel — Unified Key Control Panel with remote server status & stats
    """
    user_id = update.effective_user.id
    if not await is_any_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    auth_url = await get_remote_server_url()
    online, ping_msg = await ping_remote_server()
    server_badge = "🟢 ONLINE" if online else "🔴 OFFLINE"

    all_keys = await db.get_all_keys()
    tot_keys = len(all_keys)
    avail_keys = sum(1 for k in all_keys if k.get("used_count", 0) < k.get("max_users", 1) and not k.get("is_blocked"))
    blocked_keys = sum(1 for k in all_keys if k.get("is_blocked"))

    url_display = f"• URL: <code>{auth_url}</code>\n" if is_owner(user_id) else "• URL: <code>[PROTECTED]</code>\n"

    text = (
        f"{HTML_EMOJI.CROWN} <b>KEY MANAGEMENT &amp; AUTH CONTROL PANEL</b> {HTML_EMOJI.CROWN}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>Remote Auth Server:</b>\n"
        f"{url_display}"
        f"• Status: <b>{server_badge}</b> (<i>{ping_msg}</i>)\n\n"
        f"{HTML_EMOJI.KEY} <b>Local Keys Database:</b>\n"
        f"• Total Keys: <b>{tot_keys}</b>\n"
        f"• Available to Redeem: <b>{avail_keys}</b>\n"
        f"• Blocked: <b>{blocked_keys}</b>\n\n"
        f"{HTML_EMOJI.SPARKLES} <b>Key Commands:</b>\n"
        "• <code>/genkey &lt;dur&gt; [devices]</code> — Generate VIP key\n"
        "• <code>/gentrial &lt;time&gt; [count]</code> — Generate trial key\n"
        "• <code>/checkkey &lt;code&gt;</code> — Inspect any key (Local + Remote)\n"
        "• <code>/delkey &lt;code&gt;</code> — Delete key and revoke users\n"
        "• <code>/block_code &lt;code&gt;</code> — Temporarily block key\n"
        "• <code>/unblock_code &lt;code&gt;</code> — Unblock key\n"
        "• <code>/list_codes</code> — List all keys in database\n"
        "• <code>/setauthserver &lt;url&gt;</code> — Change remote server endpoint\n"
        "• <code>/authserver</code> — Test remote server connection\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    keyboard = [
        [InlineKeyboardButton("📋 All Keys List", callback_data="h_keys"),
         InlineKeyboardButton("🌐 Ping Server",   callback_data="kp_ping")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="start_back")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


# ── /authserver ───────────────────────────────────
async def authserver_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_any_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    auth_url = await get_remote_server_url()
    online, ping_msg = await ping_remote_server()
    badge = "🟢 ONLINE" if online else "🔴 OFFLINE"

    await update.message.reply_text(
        f"🌐 <b>Remote Key Auth Server:</b>\n\n"
        f"• <b>Endpoint:</b> <code>{auth_url}</code>\n"
        f"• <b>Status:</b> <b>{badge}</b>\n"
        f"• <b>Diagnostic:</b> <i>{ping_msg}</i>\n\n"
        f"To change the endpoint, use: <code>/setauthserver &lt;new_url&gt;</code>",
        parse_mode="HTML"
    )


# ── /setauthserver <url> ──────────────────────────
async def setauthserver_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")

    if not context.args:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/setauthserver &lt;url&gt;</code>\n"
            f"Example: <code>/setauthserver https://your-server.com/server</code>",
            parse_mode="HTML"
        )

    new_url = context.args[0].strip()
    saved = await set_remote_server_url(new_url)
    online, ping_msg = await ping_remote_server()
    badge = "🟢 ONLINE" if online else "🔴 OFFLINE"

    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Auth Server Updated!</b>\n\n"
        f"• <b>New Endpoint:</b> <code>{saved}</code>\n"
        f"• <b>Status:</b> <b>{badge}</b> ({ping_msg})",
        parse_mode="HTML"
    )

