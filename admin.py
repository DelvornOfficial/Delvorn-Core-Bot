"""admin.py — All admin utility commands."""
import time, sys, platform, io, asyncio, html
from telegram import Update
from telegram.ext import ContextTypes
from helpers import is_admin, is_owner, is_any_admin, format_expiry, fmt, HTML_EMOJI
from keys import parse_duration_str
import database as db
import attack as atk

BOT_START_TIME = time.time()


# ── USER MANAGEMENT ───────────────────────────────────────

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args: return await update.message.reply_text(f"{HTML_EMOJI.WARN} <code>/ban &lt;user_id&gt; [reason]</code>", parse_mode="HTML")
    try: uid = int(context.args[0])
    except ValueError: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid ID.", parse_mode="HTML")
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    # Try to get username
    uname = ""
    try:
        chat = await context.bot.get_chat(uid)
        uname = chat.username or ""
    except Exception:
        u = await db.get_user(uid)
        if u: uname = u.get("username", "")
    await db.ban_user(uid, uname, reason)
    await update.message.reply_text(
        f"{HTML_EMOJI.BAN} <b>User Banned!</b>\n"
        f"{HTML_EMOJI.PROFILE} ID: <code>{uid}</code>\n"
        f"{HTML_EMOJI.CROSS} Reason: <code>{html.escape(reason) if reason else 'No reason given'}</code>",
        parse_mode="HTML"
    )


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args: return await update.message.reply_text(f"{HTML_EMOJI.WARN} <code>/unban &lt;user_id&gt;</code>", parse_mode="HTML")
    try: uid = int(context.args[0])
    except ValueError: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid ID.", parse_mode="HTML")
    await db.unban_user(uid)
    await update.message.reply_text(f"{HTML_EMOJI.CHECKMARK} User <code>{uid}</code> unbanned.", parse_mode="HTML")


async def banned_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    blist = await db.get_all_banned()
    if not blist: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} No banned users.", parse_mode="HTML")
    lines = [f"{HTML_EMOJI.BAN} <b>Banned Users ({len(blist)})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"]
    for b in blist:
        uid = b["user_id"]
        uname = f"@{html.escape(b['username'])}" if b.get("username") else "N/A"
        reason = html.escape(b.get("reason") or "No reason")
        lines.append(f"• <code>{uid}</code> {uname} — <i>{reason}</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /block <chatid> [reason] — block user from bot & groups ──
async def block_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Block a user: they cannot interact with bot or groups."""
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/block &lt;chatid&gt; [reason]</code>", parse_mode="HTML")
    try: uid = int(context.args[0])
    except ValueError: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid ID.", parse_mode="HTML")
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Blocked by owner"
    uname = ""
    try:
        chat = await context.bot.get_chat(uid)
        uname = chat.username or ""
    except Exception:
        u = await db.get_user(uid)
        if u: uname = u.get("username", "")
    # Block = ban in our system
    await db.ban_user(uid, uname, reason)
    await update.message.reply_text(
        f"{HTML_EMOJI.LOCK} <b>User Blocked!</b>\n"
        f"{HTML_EMOJI.PROFILE} Chat ID: <code>{uid}</code>\n"
        f"{HTML_EMOJI.COOL} Username: @{html.escape(uname) if uname else 'N/A'}\n"
        f"{HTML_EMOJI.CROSS} Reason: <code>{html.escape(reason)}</code>\n"
        f"{HTML_EMOJI.LIGHTNING} This user cannot interact with the bot or send attacks in groups.",
        parse_mode="HTML"
    )


async def unblock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unblock a previously blocked user."""
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/unblock &lt;chatid&gt;</code>", parse_mode="HTML")
    try: uid = int(context.args[0])
    except ValueError: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid ID.", parse_mode="HTML")
    await db.unban_user(uid)
    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>User Unblocked!</b>\n"
        f"{HTML_EMOJI.PROFILE} Chat ID: <code>{uid}</code> is now free to use the bot.",
        parse_mode="HTML"
    )


async def blocklist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all blocked users."""
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    blist = await db.get_all_banned()
    if not blist:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} No blocked users found.", parse_mode="HTML")
    lines = [f"{HTML_EMOJI.LOCK} <b>BLOCKED USERS ({len(blist)})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"]
    for b in blist:
        uid = b["user_id"]
        uname = f"@{html.escape(b['username'])}" if b.get("username") else "N/A"
        reason = html.escape(b.get("reason") or "No reason")
        lines.append(f"• <code>{uid}</code> {uname}\n  └ <i>{reason}</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    all_u = await db.get_all_users()
    if not all_u: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} No registered users.", parse_mode="HTML")
    now = int(time.time())
    lines = [f"{HTML_EMOJI.USERS} <b>Users ({len(all_u)})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"]
    for u in all_u[:30]:
        expired = u["expiry"] > 0 and now > u["expiry"]
        status  = f"{HTML_EMOJI.CROSS} Expired" if expired else f"{HTML_EMOJI.CHECKMARK} Active"
        uname = html.escape(u['username'] or 'N/A')
        lines.append(f"• <code>{u['user_id']}</code> @{uname} — {status} | exp: {format_expiry(u['expiry'])}")
    if len(all_u) > 30: lines.append(f"\n<i>...{len(all_u)-30} more</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def userlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all bot users — owner only."""
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    all_u = await db.get_all_users()
    if not all_u:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} No registered users.", parse_mode="HTML")
    now = int(time.time())
    lines = [f"{HTML_EMOJI.USERS} <b>ALL BOT USERS ({len(all_u)})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"]
    for idx, u in enumerate(all_u, 1):
        expired = u["expiry"] > 0 and now > u["expiry"]
        banned = await db.is_banned(u["user_id"])
        if banned:
            status = f"{HTML_EMOJI.BAN} Blocked"
        elif expired:
            status = f"{HTML_EMOJI.CROSS} Expired"
        else:
            status = f"{HTML_EMOJI.CHECKMARK} Active"
        uname = html.escape(u['username'] or 'N/A')
        lines.append(f"{idx}. <code>{u['user_id']}</code> @{uname} — {status}")
        if idx % 25 == 0 and idx < len(all_u):
            # Send in chunks to avoid message length limit
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
            lines = []
    if lines:
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args: return await update.message.reply_text(f"{HTML_EMOJI.WARN} <code>/remove &lt;user_id&gt;</code>", parse_mode="HTML")
    try: uid = int(context.args[0])
    except ValueError: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid ID.", parse_mode="HTML")
    await db.delete_user(uid)
    await update.message.reply_text(f"{HTML_EMOJI.TRASH} User <code>{uid}</code> removed.", parse_mode="HTML")


async def remove_expired_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    count = await db.delete_expired_users()
    await update.message.reply_text(f"{HTML_EMOJI.TRASH} Removed <code>{count}</code> expired user(s).", parse_mode="HTML")


async def reset_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args: return await update.message.reply_text(f"{HTML_EMOJI.WARN} <code>/reset_user &lt;user_id&gt;</code>", parse_mode="HTML")
    try: uid = int(context.args[0])
    except ValueError: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid ID.", parse_mode="HTML")
    await db.reset_user(uid)
    await update.message.reply_text(f"{HTML_EMOJI.REFRESH} User <code>{uid}</code> reset (attacks count + feedback cleared).", parse_mode="HTML")


async def get_user_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args: return await update.message.reply_text(f"{HTML_EMOJI.WARN} <code>/get_user_info &lt;user_id&gt;</code>", parse_mode="HTML")
    try: uid = int(context.args[0])
    except ValueError: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid ID.", parse_mode="HTML")
    u = await db.get_user(uid)
    if not u: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} User not found.", parse_mode="HTML")
    now     = int(time.time())
    expired = u["expiry"] > 0 and now > u["expiry"]
    banned  = await db.is_banned(uid)
    st = f"{HTML_EMOJI.CROSS} Expired" if expired else f"{HTML_EMOJI.BAN} Banned" if banned else f"{HTML_EMOJI.CHECKMARK} Active"
    uname = html.escape(u['username'] or 'N/A')
    key_used = html.escape(str(u['key_used'] or 'N/A'))
    await update.message.reply_text(
        f"{HTML_EMOJI.PROFILE} <b>User Info</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.PROFILE} <b>ID:</b> <code>{u['user_id']}</code>\n"
        f"{HTML_EMOJI.COOL} <b>Username:</b> @{uname}\n"
        f"{HTML_EMOJI.CALENDAR} <b>Joined:</b> <code>{fmt(u['join_date'])}</code>\n"
        f"{HTML_EMOJI.HOURGLASS} <b>Expiry:</b> <code>{format_expiry(u['expiry'])}</code>\n"
        f"{HTML_EMOJI.TIMER} <b>Max Time:</b> <code>{u['max_time']}s</code>\n"
        f"{HTML_EMOJI.COOLDOWN} <b>Cooldown:</b> <code>{u['cooldown']}s</code>\n"
        f"{HTML_EMOJI.EXPLOSION} <b>Attacks:</b> <code>{u['attacks_count']}</code>\n"
        f"{HTML_EMOJI.KEY} <b>Key Used:</b> <code>{key_used}</code>\n"
        f"{HTML_EMOJI.CAMERA} <b>Feedback Pending:</b> <code>{u['feedback_pending']}</code>\n"
        f"{HTML_EMOJI.BAN} <b>Banned:</b> <code>{banned}</code>\n"
        f"{HTML_EMOJI.STATS} <b>Status:</b> {st}",
        parse_mode="HTML"
    )


async def set_user_limit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if len(context.args) < 3:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <code>/set_user_limit &lt;id&gt; &lt;max_time&gt; &lt;cooldown&gt;</code>", parse_mode="HTML")
    try: uid, max_t, cd = int(context.args[0]), int(context.args[1]), int(context.args[2])
    except ValueError: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid values.", parse_mode="HTML")
    await db.set_user_limits(uid, max_t, cd)
    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} User <code>{uid}</code> limits updated.\n{HTML_EMOJI.TIMER} Max: <code>{max_t}s</code> | {HTML_EMOJI.COOLDOWN} Cooldown: <code>{cd}s</code>",
        parse_mode="HTML"
    )


# ── SETTINGS ──────────────────────────────────────────────

async def settime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/settime &lt;seconds&gt;</code> (Maximum: 300s)", parse_mode="HTML")
    try:
        val = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Duration must be an integer in seconds.", parse_mode="HTML")
    if val <= 0:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Duration must be greater than 0.", parse_mode="HTML")
    if val > 300:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Maximum attack time cannot exceed <code>300s</code>.", parse_mode="HTML")

    await db.set_setting("max_time", str(val))
    # Sync all existing users and groups so max_time matches bot max_time
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE groups SET max_time = $1", val)
        await conn.execute("UPDATE users SET max_time = $1", val)
    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Global Max Duration Updated!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.TIMER} Max attack duration set to: <code>{val}s</code>\n"
        f"{HTML_EMOJI.LIGHTNING} Updated globally, for all users, and for all authorized groups.",
        parse_mode="HTML"
    )


async def setcooldown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/setcooldown &lt;seconds&gt;</code>\nExample: <code>/setcooldown 30</code>",
            parse_mode="HTML"
        )
    try:
        val = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Cooldown duration must be an integer in seconds.", parse_mode="HTML")
    if val < 0:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Cooldown duration cannot be negative.", parse_mode="HTML")

    await db.set_setting("cooldown", str(val))
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE groups SET cooldown = $1", val)
        await conn.execute("UPDATE users SET cooldown = $1", val)
    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Global Cooldown Updated!</b>\n\n"
        f"{HTML_EMOJI.TIMER} Cooldown interval set to: <code>{val}s</code>\n"
        f"{HTML_EMOJI.LIGHTNING} Updated globally, for all users, and for all authorized groups.",
        parse_mode="HTML"
    )


async def delcooldown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")

    await db.set_setting("cooldown", "0")
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE groups SET cooldown = 0")
        await conn.execute("UPDATE users SET cooldown = 0")
    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Attack Cooldown Disabled!</b>\n\n"
        "Global attack cooldown has been removed (<code>0s</code>) for users and all authorized groups.",
        parse_mode="HTML"
    )


async def setconcurrent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args: return await update.message.reply_text(f"{HTML_EMOJI.WARN} <code>/setconcurrent &lt;number&gt;</code> (e.g. <code>/setconcurrent 10</code>)", parse_mode="HTML")
    try: val = int(context.args[0])
    except ValueError: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Must be an integer number.", parse_mode="HTML")
    if val < 1: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Concurrent attacks must be at least 1.", parse_mode="HTML")
    await db.set_setting("concurrent", str(val))
    # Sync all existing groups so group concurrency matches bot concurrency
    async with db.pool.acquire() as conn:
        await conn.execute("UPDATE groups SET concurrency = $1", val)
    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Global Concurrency Updated!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.LIGHTNING} Max concurrent attacks set to: <code>{val}</code>\n"
        f"Users and all authorized groups can now launch up to <code>{val}</code> concurrent attacks.",
        parse_mode="HTML"
    )


async def setapi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/setapi &lt;url&gt; [key]</code>", parse_mode="HTML")
    url = context.args[0].strip()
    key = context.args[1].strip() if len(context.args) > 1 else ""
    await db.set_setting("api_url", url)
    await db.set_setting("api_key", key)
    key_str = f"<code>{html.escape(key)}</code>" if key else "<i>None (Embedded in URL)</i>"
    await update.message.reply_text(f"{HTML_EMOJI.CHECKMARK} <b>API Updated Successfully!</b>\n{HTML_EMOJI.LINK} URL: <code>{html.escape(url)}</code>\n{HTML_EMOJI.KEY} Key: {key_str}", parse_mode="HTML")


async def changeapikey_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/changeapikey &lt;apikey&gt;</code>\n"
            f"{HTML_EMOJI.PIN} <b>Example:</b> <code>/changeapikey Delvorn_Core_NEWKEY123</code>",
            parse_mode="HTML"
        )
    new_key = context.args[0].strip()
    await db.set_setting("api_key", new_key)
    current_url = await db.get_setting("api_url")
    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>API Key Updated Successfully!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.KEY} <b>New API Key:</b> <code>{html.escape(new_key)}</code>\n"
        f"{HTML_EMOJI.GLOBE} <b>API Endpoint:</b> <code>{html.escape(str(current_url))}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.LIGHTNING} <b>Status:</b> Active and saved to server database.",
        parse_mode="HTML"
    )


async def port_protection_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args: return await update.message.reply_text(f"{HTML_EMOJI.WARN} <code>/port_protection on</code> or <code>off</code>", parse_mode="HTML")
    state = context.args[0].lower()
    if state not in ("on", "off"): return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Use <code>on</code> or <code>off</code>.", parse_mode="HTML")
    await db.set_setting("port_protection", "true" if state == "on" else "false")
    emoji = HTML_EMOJI.SHIELD if state == "on" else HTML_EMOJI.LOCK
    await update.message.reply_text(f"{emoji} Port Protection turned <b>{state.upper()}</b>.", parse_mode="HTML")


async def block_port_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if len(context.args) < 2:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <code>/block_port &lt;ip&gt; &lt;port&gt;</code>", parse_mode="HTML")
    try:
        ip   = context.args[0]
        port = int(context.args[1])
    except ValueError: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid port.", parse_mode="HTML")
    await db.add_blocked_port(ip, port)
    await update.message.reply_text(f"{HTML_EMOJI.BAN} <code>{html.escape(ip)}:{port}</code> added to blocked list.", parse_mode="HTML")


# ── UTILITIES ─────────────────────────────────────────────

async def state_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    all_u    = await db.get_all_users()
    all_k    = await db.get_all_keys()
    all_r    = await db.get_all_resellers()
    banned   = await db.get_all_banned()
    now      = int(time.time())
    active   = sum(1 for u in all_u if u["expiry"] == 0 or now < u["expiry"])
    used_k   = sum(1 for k in all_k if k["used_by"] is not None)
    uptime_s = int(time.time() - BOT_START_TIME)
    h, rem   = divmod(uptime_s, 3600)
    m, s     = divmod(rem, 60)
    curr_api = await db.get_setting("api_url")
    curr_conc = await db.get_setting("concurrent")
    curr_time = await db.get_setting("max_time")
    curr_cd = await db.get_setting("cooldown")

    await update.message.reply_text(
        f"{HTML_EMOJI.STATS} <b>Bot Statistics &amp; Configuration</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.USERS} Total Users: <code>{len(all_u)}</code> (Active: <code>{active}</code>)\n"
        f"{HTML_EMOJI.BAN} Banned: <code>{len(banned)}</code>\n"
        f"{HTML_EMOJI.KEY} Keys: <code>{len(all_k)}</code> (Used: <code>{used_k}</code>)\n"
        f"{HTML_EMOJI.STORE} Resellers: <code>{len(all_r)}</code>\n"
        f"{HTML_EMOJI.SWORDS} Active Attacks: <code>{len(atk.active_attacks)}</code>\n"
        f"{HTML_EMOJI.TIMER} Uptime: <code>{h}h {m}m {s}s</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.GLOBE} <b>API Endpoint:</b> <code>{html.escape(str(curr_api))}</code>\n"
        f"{HTML_EMOJI.LIGHTNING} <b>Max Concurrent:</b> <code>{curr_conc}</code>\n"
        f"{HTML_EMOJI.TIMER} <b>Max Duration:</b> <code>{curr_time}s</code>\n"
        f"{HTML_EMOJI.HOURGLASS} <b>Cooldown:</b> <code>{curr_cd}s</code>",
        parse_mode="HTML"
    )


async def server_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    py_ver = sys.version.split()[0]
    os_ver = platform.platform()
    uptime_s = int(time.time() - BOT_START_TIME)
    h, rem   = divmod(uptime_s, 3600)
    m, s     = divmod(rem, 60)
    try:
        import psutil
        mem = psutil.virtual_memory()
        mem_info = f"<code>{mem.used // 1024 // 1024}MB / {mem.total // 1024 // 1024}MB ({mem.percent}%)</code>"
        cpu_info = f"<code>{psutil.cpu_percent(interval=1)}%</code>"
    except ImportError:
        mem_info = "<i>psutil not installed</i>"
        cpu_info = "<i>psutil not installed</i>"
    await update.message.reply_text(
        f"{HTML_EMOJI.DESKTOP} <b>Server Stats</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.PYTHON} Python: <code>{py_ver}</code>\n"
        f"{HTML_EMOJI.DESKTOP} OS: <code>{os_ver}</code>\n"
        f"{HTML_EMOJI.ARCHIVE} RAM: {mem_info}\n"
        f"{HTML_EMOJI.SETTINGS} CPU: {cpu_info}\n"
        f"{HTML_EMOJI.TIMER} Uptime: <code>{h}h {m}m {s}s</code>",
        parse_mode="HTML"
    )


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: broadcast to admin/reseller/user/all with targeted modes.
    Usage:
      /broadcast <msg>                  → all users + groups
      /broadcast users <msg>            → registered users only  
      /broadcast resellers <msg>        → resellers only
      /broadcast admins <msg>           → DB admins only
      /broadcast groups <msg>           → authorized groups only
    """
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    is_owner_user = True

    if not context.args:
        return await update.message.reply_text(
            f"{HTML_EMOJI.ANNOUNCEMENT} <b>Broadcast Command</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <code>/broadcast &lt;message&gt;</code> — All users &amp; groups\n"
            "• <code>/broadcast users &lt;message&gt;</code> — Registered users only\n"
            "• <code>/broadcast resellers &lt;message&gt;</code> — Your resellers only\n"
            "• <code>/broadcast groups &lt;message&gt;</code> — Authorized groups only\n"
            + ("• <code>/broadcast admins &lt;message&gt;</code> — DB Admins only\n" if is_owner_user else ""),
            parse_mode="HTML"
        )

    first_arg = context.args[0].lower()
    if first_arg in ("all", "users", "groups", "resellers", "admins"):
        target = first_arg
        custom_content = " ".join(context.args[1:]).strip()
    else:
        target = "all"
        custom_content = " ".join(context.args).strip()

    # Only owner can broadcast to 'admins' target
    if target == "admins" and not is_owner_user:
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} Only the <b>Owner</b> can broadcast to admins.", parse_mode="HTML")

    if not custom_content:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Broadcast message cannot be empty.", parse_mode="HTML")

    from branding import BOT_TAGLINE
    curr_date = time.strftime("%d %b %Y, %I:%M %p")
    premium_message = (
        f"{HTML_EMOJI.CROWN} <b>OFFICIAL BROADCAST</b> {HTML_EMOJI.CROWN}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.BELL} <b>Announcement</b>\n\n"
        f"{custom_content}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.CALENDAR} <i>{curr_date}</i> • <b>{BOT_TAGLINE}</b>"
    )

    ids = []
    if target in ("all", "users"):
        all_u = await db.get_all_users()
        ids.extend(u["user_id"] for u in all_u)
    if target in ("all", "groups"):
        all_g = await db.get_all_groups()
        ids.extend(g["chat_id"] for g in all_g)
    if target == "resellers":
        # Non-owner admins only see their own resellers
        if is_owner_user:
            all_r = await db.get_all_resellers()
        else:
            all_r = await db.get_resellers_by_admin(user_id)
        ids.extend(r["user_id"] for r in all_r)
    if target == "admins":
        all_admins = await db.get_all_admins()
        ids.extend(a["user_id"] for a in all_admins)
    ids = list(set(ids))

    status_msg = await update.message.reply_text(
        f"{HTML_EMOJI.ROCKET} <b>Broadcasting to {len(ids)} destinations...</b>",
        parse_mode="HTML"
    )

    ok, fail = 0, 0
    for cid in ids:
        try:
            await context.bot.send_message(
                chat_id=cid,
                text=premium_message,
                parse_mode="HTML"
            )
            ok += 1
            await asyncio.sleep(0.04)
        except Exception:
            fail += 1

    await status_msg.edit_text(
        f"{HTML_EMOJI.SPARKLES} <b>Broadcast Completed!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.TARGET} <b>Target:</b> <code>{target.upper()}</code>\n"
        f"{HTML_EMOJI.CHECKMARK} <b>Delivered:</b> <code>{ok}</code>\n"
        f"{HTML_EMOJI.CROSS} <b>Failed:</b> <code>{fail}</code>",
        parse_mode="HTML"
    )


async def backup_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    import json
    all_u = await db.get_all_users()
    data  = json.dumps(all_u, indent=2, default=str)
    bio   = io.BytesIO(data.encode())
    bio.name = "users_backup.json"
    await update.message.reply_document(document=bio, filename="users_backup.json", caption=f"{HTML_EMOJI.BOX} Users backup", parse_mode="HTML")


async def restore_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Owner only: /restore_users or /load_users
    Load/restore users from a JSON backup file.
    - If sent directly: prompts the user to send/forward the json backup file.
    - If replied to a file or sent with a file: processes immediately.
    """
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")

    msg = update.effective_message
    doc = None

    if msg.reply_to_message and msg.reply_to_message.document:
        doc = msg.reply_to_message.document
    elif msg.document:
        doc = msg.document

    if not doc:
        context.user_data["awaiting_restore_json"] = True
        return await update.message.reply_text(
            f"{HTML_EMOJI.WAIT} <b>Restore Users Mode Activated!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Ab apna <code>users_backup.json</code> file yahan send ya forward karein.\n\n"
            "<i>(Cancel karne ke liye /cancel likhein)</i>",
            parse_mode="HTML"
        )

    await process_user_backup_document(update, context, doc)


async def process_user_backup_document(update: Update, context: ContextTypes.DEFAULT_TYPE, doc):
    """Helper to download, parse and restore backup json file."""
    file_name = doc.file_name or ""
    if not file_name.lower().endswith(".json"):
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} <b>File must be a .json file.</b>", parse_mode="HTML")

    status_msg = await update.message.reply_text(f"{HTML_EMOJI.WAIT} <i>Downloading and parsing backup file...</i>", parse_mode="HTML")

    try:
        import json
        file_obj = await context.bot.get_file(doc.file_id)
        file_bytes = await file_obj.download_as_bytearray()
        data = json.loads(file_bytes.decode("utf-8"))

        if not isinstance(data, list):
            return await status_msg.edit_text(
                f"{HTML_EMOJI.CROSS} <b>Invalid JSON format.</b> Expected a list of users.",
                parse_mode="HTML"
            )

        restored, errors = await db.restore_users(data)
        await status_msg.edit_text(
            f"{HTML_EMOJI.CHECKMARK} <b>Users Backup Loaded Successfully!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.USERS} <b>Total in File:</b> <code>{len(data)}</code>\n"
            f"{HTML_EMOJI.CHECKMARK} <b>Restored / Updated:</b> <code>{restored}</code>\n"
            f"{HTML_EMOJI.CROSS} <b>Errors / Skipped:</b> <code>{errors}</code>",
            parse_mode="HTML"
        )
    except json.JSONDecodeError:
        await status_msg.edit_text(f"{HTML_EMOJI.CROSS} <b>Invalid JSON content!</b> Could not decode file.", parse_mode="HTML")
    except Exception as e:
        await status_msg.edit_text(f"{HTML_EMOJI.CROSS} <b>Error restoring backup:</b> <code>{e}</code>", parse_mode="HTML")


async def restore_users_document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the incoming json document when awaiting_restore_json is active."""
    if not is_owner(update.effective_user.id):
        return

    if not context.user_data.get("awaiting_restore_json"):
        return

    doc = update.effective_message.document
    if not doc:
        return

    context.user_data["awaiting_restore_json"] = False
    await process_user_backup_document(update, context, doc)


async def cancel_restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_restore_json"):
        context.user_data["awaiting_restore_json"] = False
        return await update.message.reply_text(f"{HTML_EMOJI.CHECKMARK} Restore cancelled.", parse_mode="HTML")


async def extend_all_users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id): return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args: return await update.message.reply_text(f"{HTML_EMOJI.WARN} <code>/extend_all_users &lt;seconds&gt;</code>", parse_mode="HTML")
    try: secs = int(context.args[0])
    except ValueError: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Must be integer.", parse_mode="HTML")
    await db.extend_all_users(secs)
    await update.message.reply_text(f"{HTML_EMOJI.CHECKMARK} Extended all active users by <code>{secs}s</code>.", parse_mode="HTML")


# ── ADMIN MANAGEMENT (Owner Only) ───────────────────────────────────

async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /addadmin <chatid> <balance> <duration>"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if len(context.args) < 3:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/addadmin &lt;chatid&gt; &lt;balance&gt; &lt;duration&gt;</code>\n\n"
            f"{HTML_EMOJI.PIN} <b>Examples:</b>\n"
            f"• <code>/addadmin 123456789 200 30d</code>\n"
            f"• <code>/addadmin 123456789 100 7d</code>\n"
            f"• <code>/addadmin 123456789 50 24h</code>",
            parse_mode="HTML"
        )
    try:
        target_id = int(context.args[0])
        balance   = int(context.args[1])
    except ValueError:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} <code>chatid</code> and <code>balance</code> must be numbers.", parse_mode="HTML")

    parsed = parse_duration_str(context.args[2])
    if not parsed:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid duration format! Use e.g. <code>24h</code>, <code>7d</code>, <code>30d</code>.", parse_mode="HTML")

    duration_sec, duration_label = parsed
    expiry_ts = int(time.time()) + duration_sec

    name, username = "", ""
    try:
        chat = await context.bot.get_chat(target_id)
        name = chat.first_name or ""
        if chat.last_name: name += f" {chat.last_name}"
        username = chat.username or ""
    except Exception:
        u = await db.get_user(target_id)
        if u: username = u.get("username", "")

    await db.add_admin(target_id, name, username, balance, user_id, expiry_ts)
    exp_str = format_expiry(expiry_ts)
    uname_str = f"@{html.escape(username)}" if username else "N/A"
    await update.message.reply_text(
        f"{HTML_EMOJI.CROWN} <b>Admin Added Successfully!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.PROFILE} <b>Chat ID:</b> <code>{target_id}</code>\n"
        f"{HTML_EMOJI.COOL} <b>Username:</b> {uname_str}\n"
        f"{HTML_EMOJI.CREDIT} <b>Balance:</b> <code>{balance}</code> credits\n"
        f"{HTML_EMOJI.HOURGLASS} <b>Valid For:</b> <code>{duration_label}</code> ({exp_str})",
        parse_mode="HTML"
    )
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"{HTML_EMOJI.CROWN} <b>You have been promoted to Admin!</b>\n\n"
                f"{HTML_EMOJI.CREDIT} <b>Balance:</b> <code>{balance}</code> credits\n"
                f"{HTML_EMOJI.HOURGLASS} <b>Valid For:</b> <code>{duration_label}</code> ({exp_str})\n"
                f"Use <code>/help</code> or <code>/myplan</code> to view your admin commands and plan."
            ),
            parse_mode="HTML"
        )
    except Exception:
        pass


async def deladmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: /deladmin <chatid>"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/deladmin &lt;chatid&gt;</code>", parse_mode="HTML")
    try: target_id = int(context.args[0])
    except ValueError: return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid ID.", parse_mode="HTML")
    adm = await db.get_admin(target_id)
    if not adm:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Admin not found.", parse_mode="HTML")
    await db.remove_admin(target_id)
    await update.message.reply_text(f"{HTML_EMOJI.TRASH} Admin <code>{target_id}</code> removed.", parse_mode="HTML")


async def adminlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: list all DB admins."""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    all_admins = await db.get_all_admins()
    if not all_admins:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} No admins added yet.", parse_mode="HTML")
    lines = [f"{HTML_EMOJI.CROWN} <b>ADMIN LIST ({len(all_admins)})</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"]
    now = int(time.time())
    for idx, a in enumerate(all_admins, 1):
        uname = f"@{html.escape(a['username'])}" if a.get("username") else "N/A"
        name = html.escape(a.get("name") or "Unknown")
        exp_val = a.get("expiry", 0)
        exp_status = format_expiry(exp_val) if exp_val > 0 else "Permanent"
        if exp_val > 0 and now > exp_val:
            exp_status = f"{HTML_EMOJI.CROSS} Expired"
        lines.append(
            f"{idx}. {name} ({uname})\n"
            f"   {HTML_EMOJI.PROFILE} <code>{a['user_id']}</code> | {HTML_EMOJI.CREDIT} <code>{a['balance']}</code> credits | {HTML_EMOJI.HOURGLASS} <code>{exp_status}</code>"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def setimageforward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the channel to auto-forward group images to. /setimageforward <channel_id>"""
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
    if not context.args:
        current = await db.get_setting("image_forward_channel") or "Not set"
        return await update.message.reply_text(
            f"{HTML_EMOJI.CAMERA} <b>Image Forward Channel</b>\n"
            f"Current: <code>{html.escape(current)}</code>\n\n"
            f"Usage: <code>/setimageforward &lt;channel_id&gt;</code>\n"
            f"Example: <code>/setimageforward -1001234567890</code>",
            parse_mode="HTML"
        )
    channel = context.args[0].strip()
    await db.set_setting("image_forward_channel", channel)
    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Image Forward Channel Set!</b>\n"
        f"{HTML_EMOJI.CAMERA} Channel: <code>{html.escape(channel)}</code>\n"
        f"{HTML_EMOJI.LIGHTNING} All images sent in authorized groups will now be auto-forwarded to this channel.",
        parse_mode="HTML"
    )


# ── MAINTENANCE MODE ───────────────────────────────────────

async def maintenanceon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable maintenance mode and broadcast alert to all users and groups."""
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")

    await db.set_setting("maintenance", "true")

    notice = (
        f"{HTML_EMOJI.SIREN} <b>SYSTEM MAINTENANCE ALERT</b> {HTML_EMOJI.SIREN}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.SETTINGS} <b>Status:</b> Bot is now under Maintenance.\n"
        f"{HTML_EMOJI.WARN} Attack commands and bot services are temporarily paused.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.TIMER} We are updating and optimizing servers. You will be notified as soon as maintenance is completed!"
    )

    status_msg = await update.message.reply_text(f"{HTML_EMOJI.REFRESH} <b>Enabling Maintenance Mode &amp; Broadcasting alert...</b>", parse_mode="HTML")

    # Gather all users and groups
    all_u = await db.get_all_users()
    all_g = await db.get_all_groups()
    targets = set([u["user_id"] for u in all_u] + [g["chat_id"] for g in all_g])

    ok, fail = 0, 0
    for cid in targets:
        try:
            await context.bot.send_message(chat_id=cid, text=notice, parse_mode="HTML")
            ok += 1
            await asyncio.sleep(0.05)  # slight delay to prevent flood
        except Exception:
            fail += 1

    await status_msg.edit_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Maintenance Mode is now ON!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.ANNOUNCEMENT} Broadcast Sent: <code>{ok}</code>\n"
        f"{HTML_EMOJI.CROSS} Broadcast Failed: <code>{fail}</code>\n"
        f"{HTML_EMOJI.LOCK} All attack services are locked for non-admins.",
        parse_mode="HTML"
    )


async def maintenanceoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable maintenance mode and broadcast resume alert to all users and groups."""
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")

    await db.set_setting("maintenance", "false")

    notice = (
        f"{HTML_EMOJI.CHECKMARK} <b>MAINTENANCE COMPLETED</b> {HTML_EMOJI.CHECKMARK}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.ROCKET} <b>Status:</b> Bot services are back ONLINE!\n"
        f"{HTML_EMOJI.LIGHTNING} Attack commands and all features are now fully operational.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.TARGET} Thank you for your patience. Happy attacking!"
    )

    status_msg = await update.message.reply_text(f"{HTML_EMOJI.REFRESH} <b>Disabling Maintenance Mode &amp; Broadcasting alert...</b>", parse_mode="HTML")

    # Gather all users and groups
    all_u = await db.get_all_users()
    all_g = await db.get_all_groups()
    targets = set([u["user_id"] for u in all_u] + [g["chat_id"] for g in all_g])

    ok, fail = 0, 0
    for cid in targets:
        try:
            await context.bot.send_message(chat_id=cid, text=notice, parse_mode="HTML")
            ok += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1

    await status_msg.edit_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Maintenance Mode is now OFF!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.ANNOUNCEMENT} Broadcast Sent: <code>{ok}</code>\n"
        f"{HTML_EMOJI.CROSS} Broadcast Failed: <code>{fail}</code>\n"
        f"{HTML_EMOJI.LIGHTNING} Bot is now fully accessible to everyone.",
        parse_mode="HTML"
    )


# ── OWNER ↔ OWNER MESSAGING ───────────────────────────────────────────────
# /omsg <message>  — send a private message to all other owners via the bot
# /oreply <msg>    — alias for /omsg (same thing, just feels more like a reply)

async def omsg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-to-owner private chat via bot.
    Usage: /omsg <message>
    The bot delivers your message to every other hardcoded owner instantly.
    """
    from config import ADMIN_IDS

    user    = update.effective_user
    user_id = user.id if user else 0

    if not is_owner(user_id):
        return await update.message.reply_text(
            f"{HTML_EMOJI.BAN} <b>Owner only.</b>",
            parse_mode="HTML"
        )

    other_owners = [oid for oid in ADMIN_IDS if oid != user_id]

    if not other_owners:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>No other owners configured.</b>\n"
            f"Add more owner IDs to <code>ADMIN_IDS</code> in <code>config.py</code> to enable owner-to-owner chat.",
            parse_mode="HTML"
        )

    if not context.args:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/omsg &lt;your message&gt;</code>\n\n"
            f"{HTML_EMOJI.PIN} <b>Example:</b>\n"
            f"<code>/omsg Hey, check the server — something's off.</code>\n\n"
            f"{HTML_EMOJI.USERS} <b>Other owners:</b> <code>{len(other_owners)}</code>",
            parse_mode="HTML"
        )

    message_text = " ".join(context.args)

    sender_name  = html.escape(user.first_name or "Owner")
    sender_uname = f"@{html.escape(user.username)}" if user.username else "No username"
    curr_time    = time.strftime("%d %b %Y, %I:%M %p")

    card = (
        f"{HTML_EMOJI.CROWN} <b>OWNER MESSAGE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.PROFILE} <b>From:</b> {sender_name} ({sender_uname})\n"
        f"{HTML_EMOJI.PROFILE} <b>ID:</b> <code>{user_id}</code>\n"
        f"{HTML_EMOJI.CALENDAR} <b>Time:</b> {curr_time}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.BELL} <b>Message:</b>\n"
        f"{html.escape(message_text)}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Reply with: <code>/omsg &lt;your reply&gt;</code></i>"
    )

    delivered = 0
    failed    = 0
    for oid in other_owners:
        try:
            await context.bot.send_message(chat_id=oid, text=card, parse_mode="HTML")
            delivered += 1
        except Exception:
            failed += 1

    # Confirm to sender
    status_icon = HTML_EMOJI.CHECKMARK if delivered > 0 else HTML_EMOJI.CROSS
    await update.message.reply_text(
        f"{status_icon} <b>Message Delivered!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.USERS} <b>Delivered to:</b> <code>{delivered}</code> owner(s)\n"
        + (f"{HTML_EMOJI.CROSS} <b>Failed:</b> <code>{failed}</code>\n" if failed else "")
        + f"\n<b>Your message:</b>\n<i>{html.escape(message_text)}</i>",
        parse_mode="HTML"
    )
