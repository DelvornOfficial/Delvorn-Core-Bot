"""group.py — Group approval management commands."""
import time, html
from telegram import Update
from telegram.ext import ContextTypes
from helpers import is_admin, is_owner, format_expiry, HTML_EMOJI
import database as db
from keys import parse_duration_str


# ── /approvegroup <groupid> <duration> [conc] [max_time] [cooldown] ──
async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")

    args = context.args
    chat = update.effective_chat
    default_conc = int(await db.get_setting("concurrent"))
    default_max_time = int(await db.get_setting("max_time"))
    default_cooldown = int(await db.get_setting("cooldown"))

    if len(args) == 1 and chat.type in ("group", "supergroup"):
        chat_id = chat.id
        dur_str = args[0]
        conc, max_time, cooldown = default_conc, default_max_time, default_cooldown
    elif len(args) >= 2:
        try:
            chat_id = int(args[0])
        except ValueError:
            return await update.message.reply_text(f"{HTML_EMOJI.CROSS} <code>groupid</code> must be an integer (e.g. <code>-100123456789</code>).", parse_mode="HTML")
        dur_str = args[1]
        conc = int(args[2]) if len(args) > 2 and args[2].isdigit() else default_conc
        max_time = int(args[3]) if len(args) > 3 and args[3].isdigit() else default_max_time
        cooldown = int(args[4]) if len(args) > 4 and args[4].isdigit() else default_cooldown
    else:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/approvegroup &lt;groupid&gt; &lt;duration&gt; [conc] [max_time] [cooldown]</code>\n\n"
            f"{HTML_EMOJI.PIN} <b>Examples:</b>\n"
            f"• <code>/approvegroup -100123456789 30d</code>\n"
            f"• <code>/approvegroup -100123456789 7d 5 300 10</code>\n"
            f"• <code>/approvegroup 24h</code> <i>(if executed directly inside the group)</i>",
            parse_mode="HTML"
        )

    parsed = parse_duration_str(dur_str)
    if not parsed:
        return await update.message.reply_text(
            f"{HTML_EMOJI.CROSS} Invalid duration! Use formats like <code>24h</code>, <code>7d</code>, <code>30d</code>.",
            parse_mode="HTML"
        )

    duration_sec, duration_label = parsed
    expiry_ts = int(time.time()) + duration_sec

    chat_name = str(chat_id)
    try:
        tg_chat = await context.bot.get_chat(chat_id)
        chat_name = tg_chat.title or chat_name
    except Exception:
        pass

    await db.approve_group(chat_id, chat_name, conc, max_time, cooldown, user_id, expiry_ts)
    exp_str = format_expiry(expiry_ts)

    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Group Authorized Successfully!</b> {HTML_EMOJI.COOL}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.DESKTOP} <b>Group Name:</b> {html.escape(chat_name)}\n"
        f"{HTML_EMOJI.PROFILE} <b>Group ID:</b> <code>{chat_id}</code>\n"
        f"{HTML_EMOJI.HOURGLASS} <b>Authorized For:</b> <code>{duration_label}</code> ({exp_str})\n"
        f"{HTML_EMOJI.REFRESH} <b>Concurrency Limit:</b> <code>{conc}</code>\n"
        f"{HTML_EMOJI.FIRE} <b>Max Attack Duration:</b> <code>{max_time}s</code>\n"
        f"{HTML_EMOJI.COOLDOWN} <b>Cooldown Interval:</b> <code>{cooldown}s</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.STAR} All group members can now use bot commands without buying individual keys!",
        parse_mode="HTML"
    )

    setup_link = await db.get_setting("setup_link")
    setup_line = f"\n{HTML_EMOJI.TV} <b>Setup Tutorial:</b> {setup_link}" if setup_link and setup_link != "https://t.me/" else ""

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"{HTML_EMOJI.PARTY} <b>This group has been authorized by Admin!</b> {HTML_EMOJI.DELVORN}\n\n"
                f"{HTML_EMOJI.HOURGLASS} <b>Access Validity:</b> <code>{duration_label}</code> ({exp_str})\n"
                f"{HTML_EMOJI.FIRE} All members can now use <code>/attack &lt;ip&gt; &lt;port&gt; &lt;time&gt;</code> directly!{setup_line}"
            ),
            parse_mode="HTML"
        )
    except Exception:
        pass



# ── /delgroup <groupid> ─────────────────────────────────────
async def disapprove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")

    chat = update.effective_chat
    if not context.args and chat.type in ("group", "supergroup"):
        chat_id = chat.id
    elif context.args:
        try:
            chat_id = int(context.args[0])
        except ValueError:
            return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid group ID.", parse_mode="HTML")
    else:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/delgroup &lt;groupid&gt;</code>", parse_mode="HTML")

    grp = await db.get_group(chat_id)
    if not grp:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Group not found in authorized directory.", parse_mode="HTML")

    await db.disapprove_group(chat_id)
    cname = html.escape(str(grp.get('chat_name', chat_id)))
    await update.message.reply_text(
        f"{HTML_EMOJI.TRASH} <b>Group Access Revoked!</b>\n"
        f"Group <code>{cname}</code> (<code>{chat_id}</code>) is now unauthorized.",
        parse_mode="HTML"
    )


# ── /grouplist ──────────────────────────────────────────────
async def approved_groups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")

    groups = await db.get_all_groups()
    if not groups:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} No authorized groups found.", parse_mode="HTML")

    lines = [f"{HTML_EMOJI.USERS} <b>AUTHORIZED GROUPS DIRECTORY ({len(groups)})</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
    now = int(time.time())

    for idx, g in enumerate(groups, 1):
        cid = g["chat_id"]
        cname = html.escape(str(g.get("chat_name") or cid))
        exp_val = g.get("expiry", 0)
        exp_status = format_expiry(exp_val) if exp_val > 0 else "Permanent"
        if exp_val > 0 and now > exp_val:
            exp_status = f"{HTML_EMOJI.CROSS} Expired"

        lines.append(
            f"<b>{idx}.</b> {cname}\n"
            f"   {HTML_EMOJI.PROFILE} <code>GroupID:</code> <code>{cid}</code>\n"
            f"   {HTML_EMOJI.HOURGLASS} <code>Expiry:</code> <code>{exp_status}</code>\n"
            f"   {HTML_EMOJI.REFRESH} <code>Conc:</code> <code>{g['concurrency']}</code> | {HTML_EMOJI.TIMER} <code>Max:</code> <code>{g['max_time']}s</code> | {HTML_EMOJI.COOLDOWN} <code>CD:</code> <code>{g['cooldown']}s</code>\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
