"""reseller.py — Reseller system management."""
import time, html
from telegram import Update
from telegram.ext import ContextTypes
from helpers import is_admin, is_owner, is_any_admin, format_expiry, escape_md, HTML_EMOJI
import database as db
from keys import parse_duration_str


# ── /addreseller <chatid> <balance> <duration> ──────────────
async def add_reseller_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    is_admin_user = await is_any_admin(user_id)
    if not is_admin_user:
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin or Owner only.</b>", parse_mode="HTML")

    args = context.args
    if len(args) < 3:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/addreseller &lt;chatid&gt; &lt;balance&gt; &lt;duration&gt;</code>\n\n"
            f"{HTML_EMOJI.PIN} <b>Examples:</b>\n"
            f"• <code>/addreseller 123456789 100 30d</code>\n"
            f"• <code>/addreseller 123456789 50 7d</code>\n"
            f"• <code>/addreseller 123456789 20 12h</code>",
            parse_mode="HTML"
        )

    try:
        target_id = int(args[0])
        balance   = int(args[1])
    except ValueError:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} <code>chatid</code> and <code>balance</code> must be numbers.", parse_mode="HTML")

    if balance <= 0:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Balance must be greater than 0.", parse_mode="HTML")

    parsed = parse_duration_str(args[2])
    if not parsed:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid duration format! Use e.g. <code>12h</code>, <code>7d</code>, <code>30d</code>.", parse_mode="HTML")

    duration_sec, duration_label = parsed
    expiry_ts = int(time.time()) + duration_sec

    # Check admin balance if not owner
    if not is_owner(user_id):
        adm = await db.get_admin(user_id)
        if not adm:
            return await update.message.reply_text(f"{HTML_EMOJI.CROSS} <b>Access Denied!</b> Admin account not found or expired.", parse_mode="HTML")
        if adm.get("expiry", 0) > 0 and int(time.time()) > adm["expiry"]:
            return await update.message.reply_text(f"{HTML_EMOJI.CROSS} <b>Your Admin access has expired!</b>", parse_mode="HTML")
        if adm.get("balance", 0) < balance:
            return await update.message.reply_text(
                f"{HTML_EMOJI.CROSS} <b>Insufficient Balance!</b> You have <code>{adm.get('balance', 0)}</code> credits, cannot assign <code>{balance}</code> credits.",
                parse_mode="HTML"
            )

    # Try to fetch Telegram user profile info
    name = ""
    username = ""
    try:
        chat = await context.bot.get_chat(target_id)
        name = chat.first_name or ""
        if chat.last_name:
            name += f" {chat.last_name}"
        username = chat.username or ""
    except Exception:
        # Fallback to local users table if already known
        u = await db.get_user(target_id)
        if u:
            username = u.get("username", "")

    await db.add_reseller(
        user_id=target_id,
        credits=balance,
        added_by=user_id,
        name=name,
        username=username,
        expiry=expiry_ts
    )

    # Deduct balance from admin (if they are an appointed admin, not owner)
    balance_note = ""
    if not is_owner(user_id):
        adm = await db.get_admin(user_id)
        if adm:
            new_adm_bal = await db.update_admin_balance(user_id, -balance)
            balance_note = f"\n{HTML_EMOJI.CREDIT} <b>Your Remaining Balance:</b> <code>{new_adm_bal}</code> credits (-{balance} deducted)"

    exp_str = format_expiry(expiry_ts)
    uname_str = f"@{html.escape(username)}" if username else "No Username"
    name_str = html.escape(name) if name else "N/A"
    creator_tag = "👑 Owner" if is_owner(user_id) else "🛡️ You (Admin)"

    await update.message.reply_text(
        f"{HTML_EMOJI.CHECKMARK} <b>Reseller Added Successfully!</b> {HTML_EMOJI.COOL}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.PROFILE} <b>Chat ID:</b> <code>{target_id}</code>\n"
        f"{HTML_EMOJI.COOL} <b>Name:</b> {name_str}\n"
        f"{HTML_EMOJI.COOL} <b>Username:</b> {uname_str}\n"
        f"{HTML_EMOJI.CREDIT} <b>Balance:</b> <code>{balance}</code> credits\n"
        f"{HTML_EMOJI.HOURGLASS} <b>Plan Validity:</b> <code>{duration_label}</code> ({exp_str})\n"
        f"🔗 <b>Linked Creator:</b> <code>{creator_tag}</code>{balance_note}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode="HTML"
    )

    # Try notifying the reseller in DM
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"{HTML_EMOJI.PARTY} <b>Congratulations! You are now a Reseller.</b> {HTML_EMOJI.DELVORN}\n\n"
                f"{HTML_EMOJI.CREDIT} <b>Credits:</b> <code>{balance}</code>\n"
                f"{HTML_EMOJI.HOURGLASS} <b>Valid for:</b> <code>{duration_label}</code>\n\n"
                f"Use <code>/genkey &lt;duration&gt;</code> to generate customer keys!"
            ),
            parse_mode="HTML"
        )
    except Exception:
        pass


# ── /delreseller <chatid> ──────────────────────────────────
async def remove_reseller_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_any_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    if not context.args:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/delreseller &lt;chatid&gt;</code>", parse_mode="HTML")

    try:
        target_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid chat ID.", parse_mode="HTML")

    r = await db.get_reseller(target_id)
    if not r:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Reseller not found.", parse_mode="HTML")

    creator_id = r.get("added_by", 0)
    if not is_owner(user_id):
        if creator_id != user_id:
            creator_tag = "Owner" if is_owner(creator_id) else f"another Admin (ID: {creator_id})"
            return await update.message.reply_text(
                f"{HTML_EMOJI.CROSS} <b>Permission Denied!</b>\n"
                f"This reseller was created by <b>{creator_tag}</b>.\n"
                f"You can only manage or delete resellers created by you.",
                parse_mode="HTML"
            )

    await db.remove_reseller(target_id)
    await update.message.reply_text(f"{HTML_EMOJI.TRASH} Reseller <code>{target_id}</code> removed successfully.", parse_mode="HTML")


# ── /resellerlist ───────────────────────────────────────────
async def resellers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_any_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    is_owner_user = is_owner(user_id)
    # Non-owner admins ONLY see resellers they created
    if is_owner_user:
        all_r = await db.get_all_resellers()
        header = f"{HTML_EMOJI.STORE} <b>ALL BOT RESELLERS ({len(all_r)})</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    else:
        all_r = await db.get_resellers_by_admin(user_id)
        header = f"{HTML_EMOJI.STORE} <b>YOUR APPOINTED RESELLERS ({len(all_r)})</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

    if not all_r:
        msg = "No resellers found in the database." if is_owner_user else "You have not added any resellers yet. Use <code>/addreseller</code> to add one."
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} {msg}", parse_mode="HTML")

    lines = [header]
    now = int(time.time())

    for idx, r in enumerate(all_r, 1):
        uid = r["user_id"]
        name = r.get("name") or ""
        username = r.get("username") or ""
        if not name and not username:
            try:
                chat = await context.bot.get_chat(uid)
                name = chat.first_name or ""
                username = chat.username or ""
            except Exception:
                pass

        uname_disp = f"@{html.escape(username)}" if username else "N/A"
        name_disp  = html.escape(name) if name else "Unknown"
        exp_val    = r.get("expiry", 0)
        exp_status = format_expiry(exp_val) if exp_val > 0 else "Permanent"
        if exp_val > 0 and now > exp_val:
            exp_status = f"{HTML_EMOJI.CROSS} Expired"

        added_by_id = r.get("added_by", 0)
        if is_owner(added_by_id):
            creator_disp = "👑 Owner"
        elif added_by_id == user_id:
            creator_disp = "🛡️ You"
        else:
            adm_obj = await db.get_admin(added_by_id)
            if adm_obj:
                adm_u = f"@{adm_obj.get('username')}" if adm_obj.get('username') else f"ID: {added_by_id}"
                creator_disp = f"🛡️ Admin {adm_u}"
            else:
                creator_disp = f"ID: {added_by_id}"

        lines.append(
            f"<b>{idx}.</b> {name_disp} ({uname_disp})\n"
            f"   {HTML_EMOJI.PROFILE} <code>ChatID:</code> <code>{uid}</code>\n"
            f"   {HTML_EMOJI.CREDIT} <code>Credits:</code> <code>{r['credits']}</code> (keys made: <code>{r.get('keys_generated', 0)}</code>)\n"
            f"   {HTML_EMOJI.HOURGLASS} <code>Expiry:</code> <code>{exp_status}</code>\n"
            f"   🔗 <code>Created By:</code> <b>{creator_disp}</b>\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /addcredit <user_id> <amount> ──────────────────────────
async def addcredit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_any_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    args = context.args
    if len(args) < 2:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/addcredit &lt;chatid&gt; &lt;amount&gt;</code>", parse_mode="HTML")

    try:
        target_id = int(args[0])
        amount    = int(args[1])
    except ValueError:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid numbers.", parse_mode="HTML")

    if amount <= 0:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Amount must be greater than 0.", parse_mode="HTML")

    r = await db.get_reseller(target_id)
    if not r:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Reseller not found. Use <code>/addreseller</code> first.", parse_mode="HTML")

    creator_id = r.get("added_by", 0)
    if not is_owner(user_id):
        if creator_id != user_id:
            creator_tag = "Owner" if is_owner(creator_id) else f"another Admin (ID: {creator_id})"
            return await update.message.reply_text(
                f"{HTML_EMOJI.CROSS} <b>Permission Denied!</b>\n"
                f"This reseller was created by <b>{creator_tag}</b>.\n"
                f"You can only add balance to resellers created by you.",
                parse_mode="HTML"
            )
        adm = await db.get_admin(user_id)
        if not adm or adm.get("balance", 0) < amount:
            cur_bal = adm.get("balance", 0) if adm else 0
            return await update.message.reply_text(
                f"{HTML_EMOJI.CROSS} <b>Insufficient Balance!</b> Your current balance is <code>{cur_bal}</code> credits, cannot add <code>{amount}</code>.",
                parse_mode="HTML"
            )

    new_bal = await db.update_reseller_credits(target_id, amount)
    adm_note = ""
    if not is_owner(user_id):
        adm = await db.get_admin(user_id)
        if adm:
            new_adm_bal = await db.update_admin_balance(user_id, -amount)
            adm_note = f"\n{HTML_EMOJI.CREDIT} Your Balance: <code>{new_adm_bal}</code>"

    await update.message.reply_text(
        f"{HTML_EMOJI.PLUS} <b>Credits Added!</b>\n"
        f"{HTML_EMOJI.PROFILE} ChatID: <code>{target_id}</code>\n"
        f"{HTML_EMOJI.PLUS} Added: <code>{amount}</code>\n"
        f"{HTML_EMOJI.CREDIT} New Balance: <code>{new_bal}</code> credits{adm_note}",
        parse_mode="HTML"
    )


# ── /removecredit <user_id> <amount> ───────────────────────
async def removecredit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await is_any_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    args = context.args
    if len(args) < 2:
        return await update.message.reply_text(f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/removecredit &lt;chatid&gt; &lt;amount&gt;</code>", parse_mode="HTML")

    try:
        target_id = int(args[0])
        amount    = int(args[1])
    except ValueError:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid numbers.", parse_mode="HTML")

    if amount <= 0:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Amount must be greater than 0.", parse_mode="HTML")

    r = await db.get_reseller(target_id)
    if not r:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Reseller not found.", parse_mode="HTML")

    creator_id = r.get("added_by", 0)
    if not is_owner(user_id):
        if creator_id != user_id:
            creator_tag = "Owner" if is_owner(creator_id) else f"another Admin (ID: {creator_id})"
            return await update.message.reply_text(
                f"{HTML_EMOJI.CROSS} <b>Permission Denied!</b>\n"
                f"This reseller was created by <b>{creator_tag}</b>.\n"
                f"You can only remove balance from resellers created by you.",
                parse_mode="HTML"
            )
        # Reclaim credits back to admin
        await db.update_admin_balance(user_id, amount)

    new_bal = await db.update_reseller_credits(target_id, -amount)
    await update.message.reply_text(
        f"{HTML_EMOJI.CROSS} <b>Credits Removed!</b>\n"
        f"{HTML_EMOJI.PROFILE} ChatID: <code>{target_id}</code>\n"
        f"{HTML_EMOJI.CROSS} Removed: <code>{amount}</code>\n"
        f"{HTML_EMOJI.CREDIT} New Balance: <code>{new_bal}</code> credits",
        parse_mode="HTML"
    )


# ── /reseller_info [user_id] ───────────────────────────────
async def reseller_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    target_id = user_id

    if context.args:
        if not await is_any_admin(user_id):
            return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")
        try:
            target_id = int(context.args[0])
        except ValueError:
            return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Invalid Chat ID.", parse_mode="HTML")

    r = await db.get_reseller(target_id)
    if not r:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Reseller account not found.", parse_mode="HTML")

    creator_id = r.get("added_by", 0)
    if not is_owner(user_id) and target_id != user_id and creator_id != user_id:
        creator_tag = "Owner" if is_owner(creator_id) else f"another Admin (ID: {creator_id})"
        return await update.message.reply_text(
            f"{HTML_EMOJI.CROSS} <b>Permission Denied!</b>\n"
            f"This reseller was created by <b>{creator_tag}</b>.\n"
            f"You can only view details of resellers created by you.",
            parse_mode="HTML"
        )

    exp_val = r.get("expiry", 0)
    exp_status = format_expiry(exp_val) if exp_val > 0 else "Permanent"
    if exp_val > 0 and int(time.time()) > exp_val:
        exp_status = f"{HTML_EMOJI.CROSS} Expired"

    uname = f"@{html.escape(r.get('username'))}" if r.get('username') else "N/A"
    name = html.escape(r.get("name")) if r.get("name") else "N/A"

    if is_owner(creator_id):
        creator_disp = "👑 Owner"
    elif creator_id == user_id:
        creator_disp = "🛡️ You"
    else:
        adm_obj = await db.get_admin(creator_id)
        if adm_obj:
            adm_u = f"@{adm_obj.get('username')}" if adm_obj.get('username') else f"ID: {creator_id}"
            creator_disp = f"🛡️ Admin {adm_u}"
        else:
            creator_disp = f"ID: {creator_id}"

    await update.message.reply_text(
        f"{HTML_EMOJI.STORE} <b>Reseller Profile</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.PROFILE} <b>Chat ID:</b> <code>{r['user_id']}</code>\n"
        f"{HTML_EMOJI.COOL} <b>Name:</b> {name}\n"
        f"{HTML_EMOJI.COOL} <b>Username:</b> {uname}\n"
        f"{HTML_EMOJI.CREDIT} <b>Available Credits:</b> <code>{r['credits']}</code>\n"
        f"{HTML_EMOJI.KEY} <b>Keys Generated:</b> <code>{r.get('keys_generated', 0)}</code>\n"
        f"{HTML_EMOJI.HOURGLASS} <b>Valid Till:</b> <code>{exp_status}</code>\n"
        f"🔗 <b>Linked Creator:</b> <code>{creator_disp}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode="HTML"
    )
