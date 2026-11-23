"""feedback.py — Feedback screenshot handler with custom destination routing."""
import html
from telegram import Update
from telegram.ext import ContextTypes, filters, MessageHandler
from helpers import is_admin, HTML_EMOJI
import database as db


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """When user sends a photo (in DM or in Group), forward it to all configured destinations."""
    user    = update.effective_user
    user_id = user.id if user else 0
    chat    = update.effective_chat
    is_group = chat.type in ("group", "supergroup")

    # Check if feedback mode is ON globally
    feedback_on = (await db.get_setting("feedback")).lower() == "true"
    if not feedback_on:
        return  # Feedback off — ignore photos

    # Check if this user has feedback pending (or if sent in group, check if user was pending)
    u = await db.get_user(user_id)
    if not u or not u.get("feedback_pending", False):
        return  # Not pending — ignore photo

    # Get configured destination(s)
    dest_str = await db.get_setting("feedback_destination") or ""
    targets = [t.strip() for t in dest_str.split(",") if t.strip() and t.strip().lower() != "admin"]

    # Clean caption for Channel/Group (Public Proof)
    u_first = html.escape(user.first_name or "User")
    u_name = html.escape(user.username or "N/A")
    channel_caption = (
        f"{HTML_EMOJI.CAMERA} <b>Attack Proof / Feedback Screenshot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.PROFILE} <b>User</b>     : <a href=\"tg://user?id={user_id}\">{u_first}</a> (<code>{user_id}</code>)\n"
        f"{HTML_EMOJI.COOL} <b>Username</b> : @{u_name}"
    )

    photo_id = update.message.photo[-1].file_id

    for target in targets:
        # Channel ID or Group ID
        target_chat_id = int(target) if (target.startswith("-") or target.isdigit()) else target
        forwarded = False
        # 1. Try native forward_message (shows 'Forwarded from <User>')
        try:
            await context.bot.forward_message(
                chat_id      = target_chat_id,
                from_chat_id = update.effective_chat.id,
                message_id   = update.message.message_id
            )
            forwarded = True
        except Exception:
            forwarded = False

        # 2. Fallback: if forwarding fails, send photo with caption
        if not forwarded:
            try:
                await context.bot.send_photo(
                    chat_id    = target_chat_id,
                    photo      = photo_id,
                    caption    = channel_caption,
                    parse_mode = "HTML"
                )
            except Exception:
                pass

    # Clear pending
    await db.set_feedback_pending(user_id, False)

    # Reply to user
    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Screenshot received! Thank you.</b>\n"
        "You can now launch your next attack.",
        parse_mode="HTML"
    )


async def feebackforwad_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a forwarding destination chat ID (channel or group)."""
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    args = context.args
    if not args:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b>\n"
            "• <code>/feebackforwad &lt;chatid&gt;</code> (e.g. <code>/feebackforwad -1001234567890</code>)\n"
            f"{HTML_EMOJI.TIP} Use <code>/forwadlist</code> to view all active forwarding destinations.",
            parse_mode="HTML"
        )

    new_target = args[0].strip()
    if new_target.lower() == "admin":
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Admin DMs cannot be added as forwarding target. Provide a channel/group chat ID (e.g. <code>-100...</code>).", parse_mode="HTML")

    curr = await db.get_setting("feedback_destination") or ""
    current_targets = [t.strip() for t in curr.split(",") if t.strip() and t.strip().lower() != "admin"]

    # Avoid duplicate
    if new_target in current_targets:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <code>{html.escape(new_target)}</code> is already in the forwarding list!",
            parse_mode="HTML"
        )

    current_targets.append(new_target)
    updated_str = ", ".join(current_targets)
    await db.set_setting("feedback_destination", updated_str)

    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Destination Added Successfully!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.TARGET} <b>New Target:</b> <code>{html.escape(new_target)}</code>\n"
        f"{HTML_EMOJI.STATS} <b>Total Active Targets:</b> <code>{len(current_targets)}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.TIP} Use <code>/forwadlist</code> to check all targets.",
        parse_mode="HTML"
    )


async def delforward_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a forwarding destination by chatid."""
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    args = context.args
    if not args:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/delforward &lt;chatid&gt;</code>\n"
            f"{HTML_EMOJI.PIN} <b>Example:</b> <code>/delforward -1001234567890</code>\n"
            f"{HTML_EMOJI.TIP} Use <code>/forwadlist</code> to check active IDs.",
            parse_mode="HTML"
        )

    target_to_remove = args[0].strip()
    curr = await db.get_setting("feedback_destination") or ""
    current_targets = [t.strip() for t in curr.split(",") if t.strip() and t.strip().lower() != "admin"]

    if target_to_remove not in current_targets:
        return await update.message.reply_text(
            f"{HTML_EMOJI.CROSS} <code>{html.escape(target_to_remove)}</code> was not found in the forwarding list.\n"
            "Use <code>/forwadlist</code> to see active destinations.",
            parse_mode="HTML"
        )

    current_targets.remove(target_to_remove)
    updated_str = ", ".join(current_targets)
    await db.set_setting("feedback_destination", updated_str)

    await update.message.reply_text(
        f"{HTML_EMOJI.TRASH} <b>Destination Removed!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.CROSS} Removed: <code>{html.escape(target_to_remove)}</code>\n"
        f"{HTML_EMOJI.STATS} Remaining Targets: <code>{len(current_targets)}</code>",
        parse_mode="HTML"
    )


async def forwadlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all active forwarding destinations for feedback screenshots."""
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    feedback_on = (await db.get_setting("feedback")).lower() == "true"
    curr = await db.get_setting("feedback_destination") or ""
    targets = [t.strip() for t in curr.split(",") if t.strip() and t.strip().lower() != "admin"]

    lines = [
        f"{HTML_EMOJI.STATS} <b>Active Feedback Forwarding List</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"{HTML_EMOJI.LIGHTNING} <b>Feedback Mode:</b> <code>{'ON (Active)' if feedback_on else 'OFF (Inactive)'}</code>",
        f"{HTML_EMOJI.TARGET} <b>Total Targets:</b> <code>{len(targets)}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if not targets:
        lines.append(f"{HTML_EMOJI.CROSS} <i>No forwarding destinations set. Use <code>/feebackforwad &lt;id&gt;</code> to add one.</i>")
    else:
        for i, t in enumerate(targets, 1):
            if t.startswith("-100"):
                lines.append(f"<b>{i}.</b> {HTML_EMOJI.ANNOUNCEMENT} <b>Channel / Supergroup:</b> <code>{html.escape(t)}</code>")
            elif t.startswith("-"):
                lines.append(f"<b>{i}.</b> {HTML_EMOJI.USERS} <b>Group:</b> <code>{html.escape(t)}</code>")
            else:
                lines.append(f"<b>{i}.</b> {HTML_EMOJI.PIN} <b>Destination:</b> <code>{html.escape(t)}</code>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"{HTML_EMOJI.PLUS} Add: <code>/feebackforwad &lt;chatid&gt;</code>")
    lines.append(f"{HTML_EMOJI.TRASH} Remove: <code>/delforward &lt;chatid&gt;</code>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def feedbackon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")
    await db.set_setting("feedback", "true")
    curr = await db.get_setting("feedback_destination") or ""
    dest_display = curr if curr else "None (Use /feebackforwad <id> to add)"
    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Feedback Mode ON</b>\n"
        "Users must send a screenshot after each attack (in DM or Group) before they can launch the next one.\n"
        f"{HTML_EMOJI.TARGET} <b>Forwarding Targets:</b> <code>{html.escape(dest_display)}</code>\n"
        f"{HTML_EMOJI.TIP} Use <code>/forwadlist</code> to view destinations.",
        parse_mode="HTML"
    )


async def feedbackoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")
    await db.set_setting("feedback", "false")
    await update.message.reply_text(
        f"{HTML_EMOJI.CROSS} <b>Feedback Mode OFF</b>\n"
        "Users can attack freely without sending screenshots.",
        parse_mode="HTML"
    )


def get_feedback_handler():
    """Returns the MessageHandler for photos (handles both DM and Groups)."""
    return MessageHandler(filters.PHOTO & ~filters.COMMAND, photo_handler)
