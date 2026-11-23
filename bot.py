"""bot.py — Main entry point. Registers all handlers and starts polling."""
import asyncio, logging, time
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

from config import BOT_TOKEN
from helpers import is_admin, is_owner, is_any_admin, format_expiry, escape_md, install_emoji_interceptor, HTML_EMOJI
import html


import database as db
import attack as atk
import keys as ky
import reseller as res
import group as grp
import admin as adm
import feedback as fb

logging.basicConfig(level=logging.INFO)


async def group_image_forward_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-forward any image sent in an approved group to the configured forward channel."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        return
    msg = update.message
    if not msg:
        return
    # Only forward from approved groups
    grp_data = await db.get_group(chat.id)
    if not grp_data:
        return
    # Check if a forward channel is configured
    channel = await db.get_setting("image_forward_channel")
    if not channel or channel.strip() in ("", "Not set"):
        return
    try:
        chan_id = int(channel.strip())
    except ValueError:
        chan_id = channel.strip()  # @username format

    # 1st attempt: native forward_message
    forwarded = False
    try:
        await context.bot.forward_message(
            chat_id=chan_id,
            from_chat_id=chat.id,
            message_id=msg.message_id
        )
        forwarded = True
    except Exception as e:
        logging.warning(f"[ImageForward] forward_message failed: {e}")

    # 2nd attempt (fallback): send_photo / send_document directly
    if not forwarded:
        try:
            if msg.photo:
                # Compressed photo — use largest size
                file_id = msg.photo[-1].file_id
                await context.bot.send_photo(
                    chat_id=chan_id,
                    photo=file_id,
                    caption=msg.caption or None,
                    parse_mode="HTML" if msg.caption else None
                )
            elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
                # Uncompressed image sent as document
                await context.bot.send_document(
                    chat_id=chan_id,
                    document=msg.document.file_id,
                    caption=msg.caption or None,
                    parse_mode="HTML" if msg.caption else None
                )
        except Exception as e:
            logging.error(f"[ImageForward] Fallback send also failed: {e}")




async def post_init(application: Application):
    install_emoji_interceptor(application)
    await db.init_db(application)
    commands = [
        BotCommand("start",        "Start the bot & check profile"),
        BotCommand("help",         "Show help menu"),
        BotCommand("rules",        "Bot usage rules"),
        BotCommand("myinfo",       "Check your account status"),
        BotCommand("myplan",       "Check your account plan"),
        BotCommand("setup",        "Video tutorial & setup guide"),
        BotCommand("attack",       "Launch attack /attack <ip> <port> <time>"),
        BotCommand("canattack",    "Check if target IP can be attacked"),

        BotCommand("status",       "Check attack running status"),
        BotCommand("checkport",    "Scan IP port availability"),
        BotCommand("redeem",       "Redeem an access key"),
        BotCommand("checkkey",     "Inspect key validity (local & remote)"),
        BotCommand("keypanel",     "Key management & verification panel"),
        BotCommand("feedback",     "Submit attack feedback/proof screenshot"),
    ]
    await application.bot.set_my_commands(commands)
    print("[+] Bot commands set successfully.")

    # Start Midnight Key Cleanup Background Task
    asyncio.create_task(midnight_cleanup_worker(application))


async def midnight_cleanup_worker(application: Application):
    """Runs continuously in the background and triggers daily at 12:00 AM midnight (local time)."""
    from config import ADMIN_IDS
    import datetime

    while True:
        try:
            now = datetime.datetime.now()
            # Calculate seconds until next midnight (00:00:00)
            next_midnight = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            wait_seconds = (next_midnight - now).total_seconds()

            logging.info(f"[Scheduler] Next midnight cleanup in {wait_seconds:.1f} seconds (~{wait_seconds/3600:.1f}h).")
            await asyncio.sleep(wait_seconds)

            # Perform cleanup
            deleted, remaining_active = await db.cleanup_unused_and_expired_keys()

            report = (
                f"{HTML_EMOJI.BROOM} <b>DAILY MIDNIGHT KEY CLEANUP REPORT</b> {HTML_EMOJI.BROOM}\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{HTML_EMOJI.TRASH} <b>Deleted (Unused &amp; Expired):</b> <code>{deleted}</code> keys\n"
                f"{HTML_EMOJI.KEY} <b>Active Keys Remaining:</b> <code>{remaining_active}</code> keys\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{HTML_EMOJI.CALENDAR} <i>Timestamp:</i> <code>{datetime.datetime.now().strftime('%d %b %Y, %I:%M %p')}</code>\n"
                f"{HTML_EMOJI.LIGHTNING} Database optimized and cleaned automatically."
            )

            # Notify all admins
            for admin_id in ADMIN_IDS:
                try:
                    await application.bot.send_message(
                        chat_id=admin_id,
                        text=report,
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

            # Sleep 60 seconds to avoid multiple executions in same minute
            await asyncio.sleep(60)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"[Scheduler Error] in midnight_cleanup_worker: {e}")
            await asyncio.sleep(60)


# ─────────────────────────────────────────
#  HANDLERS
# ─────────────────────────────────────────

def get_start_markup():
    from branding import OWNER_CHANNEL
    keyboard = [
        [
            InlineKeyboardButton("🔥 ATTACK PANEL", callback_data="h_atk"),
            InlineKeyboardButton("🔑 REDEEM KEY", callback_data="h_key"),
        ],
        [
            InlineKeyboardButton("🔐 KEY PANEL", callback_data="kp_menu"),
            InlineKeyboardButton("📖 COMMANDS HELP", callback_data="help"),
        ],
        [
            InlineKeyboardButton("👤 MY PROFILE", callback_data="myinfo"),
            InlineKeyboardButton("📊 BOT STATUS", callback_data="status"),
        ],
        [
            InlineKeyboardButton("🎬 SETUP TUTORIAL", callback_data="h_setup"),
            InlineKeyboardButton("📢 OFFICIAL CHANNEL", url=OWNER_CHANNEL),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user      = update.effective_user
    user_id   = user.id if user else 0
    user_name = user.first_name if user else "Friend"
    chat      = update.effective_chat
    is_group  = chat.type in ("group", "supergroup") if chat else False

    # Block check
    if await db.is_banned(user_id):
        return  # silently ignore blocked users

    # Track user in DB
    uname = user.username or "" if user else ""
    await db.upsert_user(user_id=user_id, username=uname, join_date=int(time.time()))

    is_owner_user = is_owner(user_id)
    is_db_adm = await is_any_admin(user_id)
    reseller_acc = await db.get_reseller(user_id)
    u = await db.get_user(user_id) if not is_db_adm else None

    # In groups, do not reveal admin status publicly
    if is_db_adm and not is_group:
        role = f"{HTML_EMOJI.CROWN} {'OWNER' if is_owner_user else 'ADMIN'}"
    elif reseller_acc and not is_group:
        role = f"{HTML_EMOJI.STORE} RESELLER"
    elif u and (u["expiry"] == 0 or u["expiry"] > int(time.time())):
        role = f"{HTML_EMOJI.LIGHTNING} VIP USER"
    elif is_db_adm and is_group:
        role = f"{HTML_EMOJI.LIGHTNING} VIP USER"
    else:
        role = f"{HTML_EMOJI.PROFILE} FREE USER"

    # Group View: Welcome user
    if is_group:
        from branding import BOT_TAGLINE
        group_title = chat.title if chat and chat.title else "Group"
        vd = await db.get_valid_durations()
        vd_str = ", ".join(str(d) for d in vd)
        group_text = (
            f"{HTML_EMOJI.WAVE} <b>Welcome {html.escape(user_name)} to {html.escape(group_title)} from {BOT_TAGLINE}</b> {HTML_EMOJI.ROCKET}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.LIGHTNING} <b>Free Attacks Unlocked in this Group!</b>\n"
            "You can launch high-speed stress tests directly without buying a personal key.\n\n"
            f"{HTML_EMOJI.TARGET} <b>How To Attack:</b>\n"
            "Send: <code>/attack &lt;ip&gt; &lt;port&gt; &lt;time&gt;</code>\n"
            f"{HTML_EMOJI.PIN} <b>Example:</b> <code>/attack 1.1.1.1 80 120</code>\n"
            f"{HTML_EMOJI.TIMER} <b>Valid Durations:</b> <code>{vd_str}</code> seconds\n\n"
            f"{HTML_EMOJI.BOOK} <b>Setup Tutorial:</b> <code>/setup</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        return await update.message.reply_text(
            group_text,
            parse_mode="HTML"
        )

    # In DM: Admin gets control panel markup
    if is_db_adm and not is_group:
        from branding import BOT_TAGLINE
        admin_text = (
            f"{HTML_EMOJI.CROWN} <b>{'Owner' if is_owner_user else 'Admin'} Control Panel — {BOT_TAGLINE}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.PROFILE} <b>Name:</b> <code>{html.escape(user_name)}</code>\n"
            f"{HTML_EMOJI.PROFILE} <b>ID:</b> <code>{user_id}</code>\n"
            f"{HTML_EMOJI.MEDAL} <b>Role:</b> <code>{role}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.LIGHTNING} Use the panel below or commands to manage the bot:"
        )
        return await update.message.reply_text(
            admin_text,
            reply_markup=get_start_markup(),
            parse_mode="HTML"
        )

    # In DM for Normal Users / Resellers:
    user_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔑 REDEEM KEY", callback_data="h_key"),
            InlineKeyboardButton("🔥 ATTACK PANEL", callback_data="h_atk"),
        ],
        [
            InlineKeyboardButton("👤 MY PROFILE", callback_data="myinfo"),
            InlineKeyboardButton("📖 COMMANDS HELP", callback_data="help"),
        ],
        [
            InlineKeyboardButton("📊 BOT STATUS", callback_data="status"),
            InlineKeyboardButton("🎬 SETUP TUTORIAL", callback_data="h_setup"),
        ]
    ])

    welcome_text = (
        f"{HTML_EMOJI.WAVE} <b>Welcome to Delvorn Stresser, {html.escape(user_name)}!</b> {HTML_EMOJI.FIRE}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.PROFILE} <b>Account:</b> <code>{html.escape(user_name)}</code>\n"
        f"{HTML_EMOJI.PROFILE} <b>User ID:</b> <code>{user_id}</code>\n"
        f"{HTML_EMOJI.MEDAL} <b>Status:</b> <code>{role}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{HTML_EMOJI.LIGHTNING} <b>Basic Commands:</b>\n"
        f"• <code>/redeem &lt;key&gt;</code> ➔ Activate access key\n"
        f"• <code>/attack &lt;ip&gt; &lt;port&gt; &lt;time&gt;</code> ➔ Launch stress test\n"
        f"• <code>/myinfo</code> ➔ Check plan, expiry &amp; stats\n"
        f"• <code>/status</code> ➔ Check bot status\n"
        f"• <code>/help</code> ➔ View all commands\n\n"
        f"{HTML_EMOJI.PIN} <b>Key Activation:</b>\n"
        f"Use <code>/redeem &lt;key&gt;</code> to activate your VIP access!\n\n"
        f"{HTML_EMOJI.TV} <b>Setup Video Tutorial:</b> <code>/setup</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    await update.message.reply_text(
        welcome_text,
        reply_markup=user_markup,
        parse_mode="HTML"
    )



async def rules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules = (
        f"{HTML_EMOJI.RULES} <b>DELVORN BOT RULES &amp; POLICIES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.NUM_1} <b>Educational &amp; Stress-Testing Only</b>\n"
        "   Do not attack critical, government, or educational infrastructure.\n\n"
        f"{HTML_EMOJI.NUM_2} <b>Strict Cooldown Policy</b>\n"
        "   Wait for your cooldown period to finish before starting another attack.\n\n"
        f"{HTML_EMOJI.NUM_3} <b>No Spamming Commands</b>\n"
        "   Flooding commands will result in an automatic and permanent ban.\n\n"
        f"{HTML_EMOJI.NUM_4} <b>One Account Per User</b>\n"
        "   Sharing keys or multi-accounting across devices is strictly prohibited.\n\n"
        f"{HTML_EMOJI.NUM_5} <b>Submit Feedback When Prompted</b>\n"
        "   If feedback mode is active, send proof screenshot using <code>/feedback</code>.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(rules, parse_mode="HTML")


async def setup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Owner can change setup link: /setup <link>
    if context.args:
        if not is_owner(update.effective_user.id):
            return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
        new_link = context.args[0].strip()
        await db.set_setting("setup_link", new_link)
        return await update.message.reply_text(
            f"{HTML_EMOJI.CHECKMARK} <b>Setup Guide Link Updated!</b>\n{HTML_EMOJI.LINK} <code>{html.escape(new_link)}</code>",
            parse_mode="HTML"
        )

    setup_link = await db.get_setting("setup_link")
    if not setup_link or setup_link == "https://t.me/":
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>No Setup Guide link configured yet.</b>\n"
            f"The bot owner can set the tutorial video using <code>/setup &lt;link&gt;</code>.",
            parse_mode="HTML"
        )
    text = (
        f"{HTML_EMOJI.TV} <b>OFFICIAL SETUP &amp; CONFIGURATION GUIDE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Watch our official step-by-step video tutorial to configure and use the bot properly:\n\n"
        f"{HTML_EMOJI.LINK} <b>Video Link:</b> {html.escape(setup_link)}\n\n"
        f"{HTML_EMOJI.PIN} <b>Key Steps:</b>\n"
        f"{HTML_EMOJI.NUM_1} Buy key from Admin or Reseller.\n"
        f"{HTML_EMOJI.NUM_2} Redeem using <code>/redeem &lt;key&gt;</code>.\n"
        f"{HTML_EMOJI.NUM_3} Launch attack using <code>/attack &lt;ip&gt; &lt;port&gt; &lt;time&gt;</code>.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Watch Setup Tutorial", url=setup_link)]
    ])
    await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


async def canary_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner sets canary link with /canary <link>. Users in DM or Group get the canary download link."""
    user_id = update.effective_user.id

    # If owner provides an argument, update the canary link
    if context.args:
        if not is_owner(user_id):
            return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Owner only.</b>", parse_mode="HTML")
        new_link = context.args[0].strip()
        await db.set_setting("canary_link", new_link)
        return await update.message.reply_text(
            f"{HTML_EMOJI.CHECKMARK} <b>Canary Link Updated Successfully!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.CANARY} <b>New Canary Link:</b> <code>{html.escape(new_link)}</code>\n"
            f"{HTML_EMOJI.LIGHTNING} Now whenever any user sends <code>/canary</code> in DM or group, this link will be sent.",
            parse_mode="HTML"
        )

    canary_link = await db.get_setting("canary_link")
    if not canary_link or canary_link == "https://t.me/":
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>No Canary download link configured yet.</b>\n"
            f"The bot owner can set the download link using <code>/canary &lt;link&gt;</code>.",
            parse_mode="HTML"
        )
    text = (
        f"{HTML_EMOJI.CANARY} <b>OFFICIAL HTTP CANARY DOWNLOAD</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Download the official and tested HTTP Canary / Target capture tool below:\n\n"
        f"{HTML_EMOJI.DOWNLOAD} <b>Download Link:</b> {html.escape(canary_link)}\n\n"
        f"{HTML_EMOJI.PIN} <b>Instructions:</b>\n"
        f"{HTML_EMOJI.NUM_1} Download and install HttpCanary on your device.\n"
        f"{HTML_EMOJI.NUM_2} Configure SSL certificates as shown in <code>/setup</code>.\n"
        f"{HTML_EMOJI.NUM_3} Capture target packet and extract IP &amp; Port.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Download HttpCanary", url=canary_link)]
    ])
    await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


# ============================================================================
# ROLE-BASED HELP TEXT GENERATORS
# ============================================================================

def get_attack_help_text(vd_str: str) -> str:
    return (
        f"{HTML_EMOJI.FIRE} <b>ATTACK COMMANDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "• <code>/attack &lt;ip&gt; &lt;port&gt; &lt;duration&gt;</code>\n"
        "  Launch stress test on target server.\n"
        f"  └ {HTML_EMOJI.TIMER} <b>Valid durations:</b> <code>{vd_str}</code> seconds\n"
        "  <i>Example:</i> <code>/attack 1.1.1.1 80 120</code>\n\n"
        "• <code>/status</code>\n"
        "  Check live countdown timer and attack progress.\n\n"
        f"{HTML_EMOJI.TIP} <b>Tip:</b> All members can use attack commands inside authorized groups without keys!"
    )


def get_user_help_text() -> str:
    return (
        f"{HTML_EMOJI.SHIELD} <b>USER &amp; ACCOUNT COMMANDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "• <code>/start</code> — Welcome screen &amp; account status.\n"
        "• <code>/redeem &lt;code&gt;</code> — Activate key to unlock VIP access.\n"
        "• <code>/myplan</code> / <code>/myinfo</code> — Check plan validity &amp; days left.\n"
        "• <code>/status</code> — Live attack countdown &amp; server load.\n"
        "• <code>/setup</code> — Video tutorial on setup &amp; usage.\n"
        "• <code>/canary</code> — Download official HttpCanary packet capture.\n"
        "• <code>/ping</code> — Test server connection speed.\n"
        "• <code>/getid</code> — View your User ID and Chat ID.\n"
        "• <code>/help</code> — Open complete command menu."
    )


def get_reseller_help_text() -> str:
    return (
        f"{HTML_EMOJI.STORE} <b>🏪 RESELLER COMMAND DIRECTORY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.KEY} <b>Key Generation &amp; Management:</b>\n"
        "• <code>/genkey &lt;duration&gt; [devices]</code>\n"
        "  Generate customer VIP key (costs <b>4 credits</b>).\n"
        "  <i>Single Device:</i> <code>/genkey 1h</code> | <code>/genkey 7d</code> | <code>/genkey 30d</code>\n"
        "  <i>Multi-Device:</i> <code>/genkey 5h 100</code> (100 devices share one key)\n"
        "• <code>/delkey &lt;key&gt;</code>\n"
        "  Delete an unredeemed key <i>you</i> generated.\n"
        "  <i>(Note: You can only delete keys generated by your own account)</i>\n"
        "• <code>/list_codes</code>\n"
        "  View all unredeemed keys generated by you.\n\n"
        f"{HTML_EMOJI.STATS} <b>Reseller Account:</b>\n"
        "• <code>/reseller_info</code> — View your credits, keys generated &amp; expiry.\n"
        "• <code>/myinfo</code> or <code>/myplan</code> — View your profile &amp; privileges.\n\n"
        f"{HTML_EMOJI.FIRE} <b>Direct Attack Access:</b>\n"
        "• <code>/attack &lt;ip&gt; &lt;port&gt; &lt;time&gt;</code>\n"
        "  Launch attacks directly — <b>No key needed for resellers!</b>\n"
        "• <code>/status</code> — View live attack progress &amp; countdown.\n\n"
        f"{HTML_EMOJI.TV} <b>Guides &amp; Utilities:</b>\n"
        "• <code>/setup</code> — Official setup tutorial video.\n"
        "• <code>/canary</code> — Official HttpCanary packet capture download.\n"
        "• <code>/ping</code> — Test server latency.\n"
        "• <code>/getid</code> — View your User ID and Chat ID.\n\n"
        f"{HTML_EMOJI.WARN} <b>Key Rules &amp; Multi-Device System:</b>\n"
        "• 4 credits deducted from your balance for every key generated.\n"
        "• Multi-device keys: First user redemption sets the expiry for all devices simultaneously!"
    )


def get_keys_help_text() -> str:
    return (
        f"{HTML_EMOJI.KEY} <b>🔑 KEY &amp; RESELLER MANAGEMENT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.STORE} <b>Reseller Control:</b>\n"
        "• <code>/addreseller &lt;chatid&gt; &lt;balance&gt; &lt;duration&gt;</code>\n"
        "  <i>e.g.</i> <code>/addreseller 123456 100 30d</code>\n"
        "• <code>/delreseller &lt;chatid&gt;</code> — Remove a reseller.\n"
        "• <code>/resellerlist</code> — List your resellers.\n"
        "• <code>/addcredit &lt;chatid&gt; &lt;amt&gt;</code> — Add credits (deducted from balance).\n"
        "• <code>/removecredit &lt;chatid&gt; &lt;amt&gt;</code> — Remove credits.\n\n"
        f"{HTML_EMOJI.KEY} <b>Key Control:</b>\n"
        "• <code>/genkey &lt;duration&gt; [devices]</code> — Generate key (unlimited for owner, balance for admin).\n"
        "  <i>e.g.</i> <code>/genkey 30d</code> | <code>/genkey 5h 100</code> (100-device shared key)\n"
        "• <code>/gentrial &lt;time&gt; [count | device]</code> — Generate free trial key(s).\n"
        "  <i>e.g.</i> <code>/gentrial 2h</code> | <code>/gentrial 24h 5</code> | <code>/gentrial 1d 3dev</code>\n"
        "• <code>/gentrialfor &lt;id&gt; &lt;hours&gt; [count]</code> — Directly assign trial key(s) to user.\n"
        "• <code>/deletetrials</code> — Purge all unused trial keys.\n"
        "• <code>/block_code &lt;code&gt;</code> — Disable a key without deleting.\n"
        "• <code>/unblock_code &lt;code&gt;</code> — Re-enable a blocked key.\n"
        "• <code>/delkey &lt;key&gt;</code> — Instantly revoke key &amp; all its users.\n"
        "• <code>/list_codes</code> — View all active keys."
    )


def get_group_user_help_text() -> str:
    return (
        f"{HTML_EMOJI.USERS} <b>👥 GROUPS &amp; USER MANAGEMENT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.LOCK} <b>Group Control:</b>\n"
        "• <code>/approvegroup &lt;groupid&gt; &lt;dur&gt;</code> — Authorize group.\n"
        "  <i>e.g.</i> <code>/approvegroup -100123456 30d</code>\n"
        "• <code>/delgroup &lt;groupid&gt;</code> — Revoke group access.\n"
        "• <code>/grouplist</code> — List all authorized groups.\n\n"
        f"{HTML_EMOJI.PROFILE} <b>User Control:</b>\n"
        "• <code>/users</code> — List active registered users.\n"
        "• <code>/get_user_info &lt;id&gt;</code> — Full user profile &amp; history.\n"
        "• <code>/set_user_limit &lt;id&gt; &lt;time&gt; &lt;cd&gt;</code> — Set user attack limits.\n"
        "• <code>/reset_user &lt;id&gt;</code> — Reset user feedback &amp; attack count.\n"
        "• <code>/remove &lt;id&gt;</code> — Delete user from bot.\n"
        "• <code>/remove_expired</code> — Purge all expired users.\n\n"
        f"{HTML_EMOJI.BAN} <b>Block / Ban:</b>\n"
        "• <code>/block &lt;chatid&gt; [reason]</code> — Block user from bot &amp; groups.\n"
        "• <code>/unblock &lt;chatid&gt;</code> — Unblock user.\n"
        "• <code>/blocklist</code> — View all blocked users.\n"
        "• <code>/ban &lt;id&gt; [reason]</code> | <code>/unban &lt;id&gt;</code> | <code>/banned_list</code>\n\n"
        f"{HTML_EMOJI.ANNOUNCEMENT} <b>Broadcast:</b>\n"
        "• <code>/broadcast &lt;msg&gt;</code> — All users &amp; groups.\n"
        "• <code>/broadcast users &lt;msg&gt;</code> — Registered users only.\n"
        "• <code>/broadcast resellers &lt;msg&gt;</code> — Your resellers only.\n"
        "• <code>/broadcast groups &lt;msg&gt;</code> — Authorized groups only."
    )


def get_system_help_text() -> str:
    return (
        f"{HTML_EMOJI.SETTINGS} <b>⚙️ SETTINGS &amp; SYSTEM</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.TIMER} <b>Attack Limits:</b>\n"
        "• <code>/settime &lt;sec&gt;</code> — Global max attack duration (e.g. <code>/settime 300</code>).\n"
        "• <code>/setcooldown &lt;sec&gt;</code> — Cooldown between attacks.\n"
        "• <code>/delcooldown</code> — Remove cooldown entirely.\n"
        "• <code>/setconcurrent &lt;num&gt;</code> — Max simultaneous attacks.\n\n"
        f"{HTML_EMOJI.LIGHTNING} <b>API &amp; Server:</b>\n"
        "• <code>/setapi &lt;url&gt;</code> — Set attack server API URL.\n"
        "• <code>/changeapikey &lt;key&gt;</code> — Update API auth key.\n"
        "• <code>/state</code> — View all current settings.\n"
        "• <code>/server_stats</code> — CPU, RAM, Disk &amp; DB stats.\n\n"
        f"{HTML_EMOJI.SHIELD} <b>Security:</b>\n"
        "• <code>/port_protection &lt;on|off&gt;</code> — Enable IP:Port blocking.\n"
        "• <code>/block_port &lt;ip&gt; &lt;port&gt;</code> — Block specific IP:Port.\n\n"
        f"{HTML_EMOJI.CAMERA} <b>Feedback &amp; Forwarding:</b>\n"
        "• <code>/feedbackon</code> | <code>/feedbackoff</code> — Toggle feedback requirement.\n"
        "• <code>/forward &lt;channel_id&gt;</code> | <code>/delforward</code> | <code>/forwardlist</code>\n\n"
        f"{HTML_EMOJI.WARN} <b>Maintenance:</b>\n"
        "• <code>/maintenanceon</code> — Enable maintenance (disables attacks).\n"
        "• <code>/maintenanceoff</code> — Restore normal operation.\n\n"
        f"{HTML_EMOJI.TV} <b>Links:</b>\n"
        "• <code>/setup &lt;link&gt;</code> — Update setup tutorial link.\n"
        "• <code>/canary &lt;link&gt;</code> — Update canary download link.\n"
        "• <code>/setvideo &lt;link&gt;</code> — Update attack visual animation link."
    )


def get_owner_help_text() -> str:
    return (
        f"{HTML_EMOJI.CROWN} <b>👑 OWNER-ONLY COMMANDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.CROWN} <b>Admin Management:</b>\n"
        "• <code>/addadmin &lt;chatid&gt; &lt;balance&gt; &lt;duration&gt;</code> — Promote user to Admin with credits &amp; duration.\n"
        "• <code>/deladmin &lt;chatid&gt;</code> — Remove an admin.\n"
        "• <code>/adminlist</code> — List all appointed admins &amp; their balance &amp; expiry.\n\n"
        f"{HTML_EMOJI.USERS} <b>User Overview:</b>\n"
        "• <code>/userlist</code> — Full list of ALL bot users with status.\n"
        "• <code>/backup_users</code> — Export all users as JSON file.\n"
        "• <code>/restore_users</code> — Restore / load users from backup JSON.\n"
        "• <code>/extend_all_users &lt;sec&gt;</code> — Extend all active users' expiry.\n\n"
        f"{HTML_EMOJI.SETTINGS} <b>System Settings (Owner Only):</b>\n"
        "• <code>/settime</code> | <code>/setcooldown</code> | <code>/delcooldown</code>\n"
        "• <code>/setconcurrent</code> | <code>/setapi</code> | <code>/changeapikey</code>\n"
        "• <code>/port_protection</code> | <code>/block_port</code>\n"
        "• <code>/feedbackon</code> | <code>/feedbackoff</code> | <code>/forward &lt;channel&gt;</code>\n"
        "• <code>/maintenanceon</code> | <code>/maintenanceoff</code>\n"
        "• <code>/state</code> | <code>/server_stats</code>\n\n"
        f"{HTML_EMOJI.ANNOUNCEMENT} <b>Broadcast (Owner-exclusive targets):</b>\n"
        "• <code>/broadcast admins &lt;msg&gt;</code> — Send message to all your admins.\n"
        "• <code>/broadcast resellers &lt;msg&gt;</code> — Send to ALL resellers (owner sees all).\n"
        "• <code>/broadcast users &lt;msg&gt;</code> — All registered users.\n"
        "• <code>/broadcast groups &lt;msg&gt;</code> — All authorized groups.\n"
        "• <code>/broadcast &lt;msg&gt;</code> — Everyone (users + groups).\n\n"
        f"{HTML_EMOJI.CAMERA} <b>Media &amp; Auto-Forwarding:</b>\n"
        "• <code>/setimageforward &lt;channel_id&gt;</code> — Auto-forward group images to channel.\n"
        "• <code>/setup &lt;link&gt;</code> — Update setup tutorial link.\n"
        "• <code>/canary &lt;link&gt;</code> — Update canary download link.\n"
        "• <code>/setvideo &lt;link&gt;</code> — Update attack visual video/animation link.\n\n"
        f"✉️ <b>Owner Chat:</b>\n"
        "• <code>/omsg &lt;message&gt;</code> — Direct private message to all other bot owners.\n"
        "• <code>/oreply &lt;message&gt;</code> — Reply/message other bot owners.\n\n"
        f"{HTML_EMOJI.TIP} <i>Note: As Owner, you also have full access to Admin, Reseller &amp; User panels!</i>"
    )


def get_admin_help_text() -> str:
    return (
        f"🛡️ <b>ADMIN / SENIOR RESELLER COMMANDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.STORE} <b>Reseller Control (Your Resellers):</b>\n"
        "• <code>/addreseller &lt;chatid&gt; &lt;bal&gt; &lt;dur&gt;</code> — Add reseller under you (deducts your balance).\n"
        "• <code>/delreseller &lt;chatid&gt;</code> — Remove your reseller.\n"
        "• <code>/resellerlist</code> — View all resellers added by you.\n"
        "• <code>/addcredit &lt;chatid&gt; &lt;amt&gt;</code> — Transfer balance to your reseller.\n"
        "• <code>/removecredit &lt;chatid&gt; &lt;amt&gt;</code> — Reclaim balance from your reseller.\n"
        "• <code>/reseller_info &lt;chatid&gt;</code> — View your reseller's profile &amp; usage.\n\n"
        f"{HTML_EMOJI.KEY} <b>Customer VIP &amp; Trial Keys:</b>\n"
        "• <code>/genkey &lt;dur&gt; [devices]</code> — Generate customer VIP key (costs credits).\n"
        "• <code>/gentrial &lt;time&gt; [count | device]</code> — Generate free trial key(s).\n"
        "• <code>/gentrialfor &lt;id&gt; &lt;hours&gt; [count]</code> — Directly assign trial key(s) to user.\n"
        "• <code>/deletetrials</code> — Purge all unused trial keys.\n"
        "• <code>/block_code &lt;code&gt;</code> — Disable a key without deleting.\n"
        "• <code>/unblock_code &lt;code&gt;</code> — Re-enable a blocked key.\n"
        "• <code>/delkey &lt;key&gt;</code> — Delete/revoke keys generated by you.\n"
        "• <code>/list_codes</code> — View keys generated by you.\n\n"
        f"{HTML_EMOJI.FIRE} <b>Attacks (Subject to Owner Limits):</b>\n"
        "• <code>/attack &lt;ip&gt; &lt;port&gt; &lt;time&gt;</code> — Launch VIP attack.\n"
        "• <code>/status</code> — View live running attacks.\n\n"
        f"{HTML_EMOJI.PROFILE} <b>Account:</b>\n"
        "• <code>/myplan</code> / <code>/myinfo</code> — View your credit balance, linked resellers &amp; validity duration."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    chat     = update.effective_chat
    is_group = chat.type in ("group", "supergroup") if chat else False

    is_owner_user = is_owner(user_id) if not is_group else False
    is_admin_user = (await is_any_admin(user_id)) if not is_group else False
    reseller      = (await db.get_reseller(user_id) is not None) if not is_group else False

    vd     = await db.get_valid_durations()
    vd_str = ", ".join(str(d) for d in vd)

    # Check for direct sub-topic arguments: /help owner, /help admin, /help reseller, etc.
    if context.args:
        sub = context.args[0].lower().strip()
        if sub in ("owner", "owners") and is_owner_user:
            return await update.message.reply_text(get_owner_help_text(), parse_mode="HTML")
        elif sub in ("admin", "admins") and (is_admin_user or is_owner_user):
            return await update.message.reply_text(get_admin_help_text(), parse_mode="HTML")
        elif sub in ("reseller", "resellers", "res") and (reseller or is_admin_user or is_owner_user):
            return await update.message.reply_text(get_reseller_help_text(), parse_mode="HTML")
        elif sub in ("attack", "atk"):
            return await update.message.reply_text(get_attack_help_text(vd_str), parse_mode="HTML")
        elif sub in ("user", "usr", "account"):
            return await update.message.reply_text(get_user_help_text(), parse_mode="HTML")
        elif sub in ("keys", "key") and (is_admin_user or is_owner_user):
            return await update.message.reply_text(get_keys_help_text(), parse_mode="HTML")
        elif sub in ("group", "groups", "users") and (is_admin_user or is_owner_user):
            return await update.message.reply_text(get_group_user_help_text(), parse_mode="HTML")
        elif sub in ("sys", "system", "settings") and is_owner_user:
            return await update.message.reply_text(get_system_help_text(), parse_mode="HTML")

    # 1. OWNER VIEW: Comprehensive Owner Control Center
    if is_owner_user:
        text = (
            f"{HTML_EMOJI.CROWN} <b>👑 OWNER CONTROL CENTER — COMMAND HELP</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Welcome <b>Owner</b>! You have supreme authority over all bot operations.\n\n"
            f"{HTML_EMOJI.CROWN} <b>Owner Exclusive Commands:</b>\n"
            "• <code>/addadmin &lt;chatid&gt; &lt;balance&gt; &lt;duration&gt;</code> — Promote user to Admin.\n"
            "• <code>/deladmin &lt;chatid&gt;</code> — Demote / remove an Admin.\n"
            "• <code>/adminlist</code> — View all appointed Admins, balances &amp; validity.\n"
            "• <code>/userlist</code> — Full paginated list of all users &amp; statuses.\n"
            "• <code>/omsg &lt;msg&gt;</code> — Direct private chat to all other owners.\n"
            "• <code>/setimageforward &lt;channel_id&gt;</code> — Auto-forward group images.\n"
            "• <code>/broadcast admins &lt;msg&gt;</code> — Broadcast to all Admins.\n"
            "• <code>/backup_users</code> — Export user database as JSON.\n"
            "• <code>/restore_users</code> — Restore / load users from backup JSON.\n"
            "• <code>/extend_all_users &lt;sec&gt;</code> — Extend all active users.\n\n"
            "👇 <i>Select a category below to explore all commands:</i>"
        )
        keyboard = [
            [InlineKeyboardButton("👑 Owner Panel",      callback_data="h_owner"),
             InlineKeyboardButton("🛠️ Admin Panel",      callback_data="h_admin_panel")],
            [InlineKeyboardButton("🏪 Reseller Panel",   callback_data="h_res"),
             InlineKeyboardButton("🔑 Keys Management",   callback_data="h_keys")],
            [InlineKeyboardButton("👥 Groups & Users",   callback_data="h_grp"),
             InlineKeyboardButton("⚙️ System Settings",  callback_data="h_sys")],
            [InlineKeyboardButton("🔥 Attack Commands",  callback_data="h_atk"),
             InlineKeyboardButton("🛡️ User Commands",     callback_data="h_usr")]
        ]
        return await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    # 2. ADMIN VIEW: Admin Control Panel (Upgraded Reseller UI)
    if is_admin_user:
        text = (
            f"🛡️ <b>ADMIN / SENIOR RESELLER DASHBOARD</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Welcome <b>Admin</b>! You can manage your appointed resellers and customer VIP keys.\n\n"
            f"🛠️ <b>Quick Admin Commands:</b>\n"
            "• <code>/addreseller &lt;id&gt; &lt;bal&gt; &lt;dur&gt;</code> — Add Reseller under you.\n"
            "• <code>/delreseller &lt;id&gt;</code> — Remove your reseller.\n"
            "• <code>/resellerlist</code> — View your resellers list.\n"
            "• <code>/addcredit &lt;id&gt; &lt;amt&gt;</code> | <code>/removecredit &lt;id&gt; &lt;amt&gt;</code>\n"
            "• <code>/genkey &lt;dur&gt; [devices]</code> — Generate customer VIP key.\n"
            "• <code>/delkey &lt;key&gt;</code> — Revoke your generated key.\n"
            "• <code>/myplan</code> — View your credit balance &amp; validity duration.\n\n"
            f"{HTML_EMOJI.WARN} <i>All server limits &amp; rules (time, cooldown, maintenance) set by Owner apply to your attacks.</i>\n\n"
            "👇 <i>Select a category below to explore commands:</i>"
        )
        keyboard = [
            [InlineKeyboardButton("🏪 My Resellers",     callback_data="h_res"),
             InlineKeyboardButton("🔑 Customer Keys",    callback_data="h_keys")],
            [InlineKeyboardButton("🔥 Attack Commands",  callback_data="h_atk"),
             InlineKeyboardButton("🛡️ User Commands",     callback_data="h_usr")],
            [InlineKeyboardButton("👤 My Plan & Info",   callback_data="myinfo")]
        ]
        return await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    # 3. RESELLER VIEW: Reseller Command Directory
    if reseller:
        text = get_reseller_help_text()
        keyboard = [
            [InlineKeyboardButton("🔥 Attack Commands", callback_data="h_atk"),
             InlineKeyboardButton("🛡️ User Commands",   callback_data="h_usr")]
        ]
        return await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    # 4. NORMAL USER VIEW
    text = (
        f"{HTML_EMOJI.BOOK} <b>COMMAND INSTRUCTIONS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.LIGHTNING} <b>Attack Command:</b>\n"
        "• <code>/attack &lt;ip&gt; &lt;port&gt; &lt;duration&gt;</code>\n"
        f"  └ {HTML_EMOJI.TIMER} <b>Valid Durations:</b> <code>{vd_str}</code> seconds\n"
        "  <i>Example:</i> <code>/attack 1.1.1.1 80 120</code>\n\n"
        f"{HTML_EMOJI.PROFILE} <b>Profile Command:</b>\n"
        "• <code>/myinfo</code> or <code>/myplan</code>\n"
        "  Check your active plan validity, attacks sent &amp; expiry.\n\n"
        f"{HTML_EMOJI.KEY} <b>Redeem Key:</b>\n"
        "• <code>/redeem &lt;key&gt;</code>\n"
        "  Activate your VIP key purchased from Admin or Reseller.\n\n"
        f"{HTML_EMOJI.STATS} <b>Live Status:</b>\n"
        "• <code>/status</code> — Live attack countdown &amp; server load.\n\n"
        f"{HTML_EMOJI.TV} <b>Setup Guide:</b> <code>/setup</code>\n"
        f"{HTML_EMOJI.DOWNLOAD} <b>HttpCanary:</b> <code>/canary</code>\n"
        f"{HTML_EMOJI.TIP} <b>Tip:</b> Inside authorized groups, you can use <code>/attack</code> directly without buying a key!\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    keyboard = [
        [InlineKeyboardButton("🔥 Attack Commands", callback_data="h_atk"),
         InlineKeyboardButton("🛡️ User Commands",   callback_data="h_usr")]
    ]
    return await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def build_profile_card(user, user_id: int, chat, is_group: bool) -> str:
    """Generates the rich, unified profile card for /myinfo, /myplan, and inline buttons."""
    # 1. OWNER
    if is_owner(user_id):
        uname_str = f"@{html.escape(user.username)}" if (user and user.username) else "N/A"
        name_str  = html.escape(user.first_name or "Owner") if user else "Owner"
        return (
            f"{HTML_EMOJI.CROWN} <b>OWNER ACCOUNT PROFILE</b> {HTML_EMOJI.CROWN}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.PROFILE} <b>User ID:</b> <code>{user_id}</code>\n"
            f"{HTML_EMOJI.COOL} <b>Name:</b> {name_str} ({uname_str})\n"
            f"{HTML_EMOJI.SHIELD} <b>Role:</b> 👑 <b>Bot Owner (Supreme Admin)</b>\n"
            f"{HTML_EMOJI.CREDIT} <b>Credits / Balance:</b> <code>Unlimited (Infinite)</code>\n"
            f"{HTML_EMOJI.TIMER} <b>Max Attack Duration:</b> <code>Unlimited</code>\n"
            f"{HTML_EMOJI.COOLDOWN} <b>Cooldown Interval:</b> <code>0s (None)</code>\n"
            f"{HTML_EMOJI.CALENDAR} <b>Account Expiry:</b> <code>Never (Lifetime Access)</code>\n"
            f"{HTML_EMOJI.STATS} <b>Status:</b> {HTML_EMOJI.CHECKMARK} Active\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.LIGHTNING} <i>Full root privileges over bot configuration, settings, admins, and groups.</i>"
        )

    # 2. APPOINTED DB ADMIN (Upgraded Reseller)
    adm = await db.get_admin(user_id)
    if adm:
        now = int(time.time())
        exp_val = adm.get("expiry", 0)
        exp_status = format_expiry(exp_val) if exp_val > 0 else "Never (Permanent)"
        is_exp = exp_val > 0 and now > exp_val
        status_badge = f"{HTML_EMOJI.CROSS} Expired" if is_exp else f"{HTML_EMOJI.CHECKMARK} Active"
        uname_str = f"@{html.escape(adm.get('username') or (user.username if user else ''))}" if (adm.get('username') or (user and user.username)) else "N/A"
        name_str = html.escape(adm.get('name') or (user.first_name if user else "Admin"))

        global_max = int(await db.get_setting("max_time") or 300)
        global_cd  = int(await db.get_setting("cooldown") or 30)

        # Count resellers linked to this admin
        my_resellers = await db.get_resellers_by_admin(user_id)
        res_count = len(my_resellers)

        return (
            f"🛡️ <b>ADMIN (Senior Reseller) PROFILE</b> 🛡️\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.PROFILE} <b>User ID:</b> <code>{user_id}</code>\n"
            f"{HTML_EMOJI.COOL} <b>Name:</b> {name_str} ({uname_str})\n"
            f"{HTML_EMOJI.SHIELD} <b>Role:</b> 🛡️ <b>Admin (Upgraded Reseller)</b>\n"
            f"{HTML_EMOJI.CREDIT} <b>Available Credits:</b> <code>{adm.get('balance', 0)}</code> credits\n"
            f"{HTML_EMOJI.CALENDAR} <b>Admin Validity:</b> <code>{exp_status}</code>\n"
            f"{HTML_EMOJI.STORE} <b>Your Resellers:</b> <code>{res_count}</code> reseller(s) linked to you\n"
            f"{HTML_EMOJI.TIMER} <b>Max Attack Duration:</b> <code>{global_max}s</code> <i>(set by Owner)</i>\n"
            f"{HTML_EMOJI.COOLDOWN} <b>Cooldown Interval:</b> <code>{global_cd}s</code> <i>(set by Owner)</i>\n"
            f"{HTML_EMOJI.STATS} <b>Status:</b> {status_badge}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.STAR} <b>Your Capabilities:</b>\n"
            f"• Manage Your Resellers: <code>/addreseller</code>, <code>/delreseller</code>, <code>/resellerlist</code>\n"
            f"• Manage Credits: <code>/addcredit</code>, <code>/removecredit</code> (from your balance)\n"
            f"• Generate Customer Keys: <code>/genkey &lt;dur&gt; [devices]</code>\n"
            f"• Launch VIP Attacks: <code>/attack &lt;ip&gt; &lt;port&gt; &lt;time&gt;</code>"
        )

    # 3. RESELLER
    r = await db.get_reseller(user_id)
    if r:
        now = int(time.time())
        exp_val = r.get("expiry", 0)
        exp_status = format_expiry(exp_val) if exp_val > 0 else "Permanent"
        is_exp = exp_val > 0 and now > exp_val
        status_badge = f"{HTML_EMOJI.CROSS} Expired" if is_exp else f"{HTML_EMOJI.CHECKMARK} Active"
        uname = f"@{html.escape(user.username)}" if (user and user.username) else "N/A"
        name = html.escape(user.first_name or "") if user else "Reseller"
        return (
            f"{HTML_EMOJI.STORE} <b>RESELLER ACCOUNT PROFILE</b> {HTML_EMOJI.STORE}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.PROFILE} <b>Chat ID:</b> <code>{user_id}</code>\n"
            f"{HTML_EMOJI.COOL} <b>Name:</b> {name}\n"
            f"{HTML_EMOJI.COOL} <b>Username:</b> {uname}\n"
            f"{HTML_EMOJI.SHIELD} <b>Role:</b> 🏪 <b>Authorized Reseller</b>\n"
            f"{HTML_EMOJI.CREDIT} <b>Available Credits:</b> <code>{r['credits']}</code> (Cost: 4 per key)\n"
            f"{HTML_EMOJI.KEY} <b>Keys Generated:</b> <code>{r.get('keys_generated', 0)}</code>\n"
            f"{HTML_EMOJI.HOURGLASS} <b>Valid Till:</b> <code>{exp_status}</code>\n"
            f"{HTML_EMOJI.FIRE} <b>Direct Attack:</b> <code>Allowed (No Key Needed)</code>\n"
            f"{HTML_EMOJI.STATS} <b>Status:</b> {status_badge}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.ARROW_RIGHT} Use <code>/genkey &lt;duration&gt;</code> to create customer VIP keys!"
        )

    # 4. REGULAR USER / GROUP MEMBER
    u = await db.get_user(user_id)
    if not u or (not u.get("key_used") and u.get("expiry", 0) == 0):
        if is_group:
            grp = await db.get_group(chat.id) if chat else None
            if grp:
                exp_grp = format_expiry(grp.get("expiry", 0)) if grp.get("expiry", 0) > 0 else "Permanent"
                uname_str = f"@{html.escape(user.username)}" if (user and user.username) else "N/A"
                return (
                    f"{HTML_EMOJI.SHIELD} <b>GROUP MEMBER ACCOUNT PROFILE</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{HTML_EMOJI.PROFILE} <b>User ID:</b> <code>{user_id}</code>\n"
                    f"{HTML_EMOJI.COOL} <b>Username:</b> {uname_str}\n"
                    f"{HTML_EMOJI.LOCK} <b>Group:</b> {html.escape(grp.get('chat_name', str(chat.id)))}\n"
                    f"{HTML_EMOJI.CALENDAR} <b>Group Expiry:</b> <code>{exp_grp}</code>\n"
                    f"{HTML_EMOJI.TIMER} <b>Group Max Duration:</b> <code>{grp.get('max_time', 240)}s</code>\n"
                    f"{HTML_EMOJI.COOLDOWN} <b>Cooldown:</b> <code>{grp.get('cooldown', 30)}s</code>\n"
                    f"{HTML_EMOJI.STATS} <b>Status:</b> {HTML_EMOJI.CHECKMARK} Active (Authorized Group Member)\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{HTML_EMOJI.LIGHTNING} You can run attack commands inside this group without buying individual keys."
                )
        return (
            f"{HTML_EMOJI.CROSS} <b>No Active Plan Found!</b>\n\n"
            "You do not currently have an active VIP subscription.\n"
            f"• Redeem a key: <code>/redeem &lt;key&gt;</code>\n"
            f"• Or use the bot inside an authorized VIP group.\n"
            f"• Contact Admin or Reseller to purchase access."
        )

    now     = int(time.time())
    expired = u["expiry"] > 0 and now > u["expiry"]
    banned  = await db.is_banned(user_id)

    uname_str = f"@{html.escape(u['username'])}" if u.get('username') else "N/A"
    key_str = html.escape(u.get('key_used') or 'N/A')
    status_label = f"{HTML_EMOJI.CROSS} Expired" if expired else f"{HTML_EMOJI.BAN} Banned" if banned else f"{HTML_EMOJI.CHECKMARK} Active"

    if u["expiry"] <= 0:
        exp_display = "Lifetime Access" if u.get("key_used") else "Free User"
    else:
        exp_display = format_expiry(u["expiry"])

    fb_status = f"{HTML_EMOJI.CAMERA} Screenshot Pending" if u.get("feedback_pending") else f"{HTML_EMOJI.CHECKMARK} Clean"

    return (
        f"{HTML_EMOJI.PROFILE} <b>MY ACCOUNT PROFILE &amp; PLAN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.PROFILE} <b>User ID:</b> <code>{user_id}</code>\n"
        f"{HTML_EMOJI.COOL} <b>Username:</b> {uname_str}\n"
        f"{HTML_EMOJI.SHIELD} <b>Role:</b> 🛡️ VIP Member\n"
        f"{HTML_EMOJI.CALENDAR} <b>Plan Expiry:</b> <code>{exp_display}</code>\n"
        f"{HTML_EMOJI.TIMER} <b>Max Attack Duration:</b> <code>{u.get('max_time', 240)}s</code>\n"
        f"{HTML_EMOJI.COOLDOWN} <b>Cooldown Interval:</b> <code>{u.get('cooldown', 30)}s</code>\n"
        f"{HTML_EMOJI.EXPLOSION} <b>Total Attacks Sent:</b> <code>{u.get('attacks_count', 0)}</code>\n"
        f"{HTML_EMOJI.KEY} <b>Key Redeemed:</b> <code>{key_str}</code>\n"
        f"{HTML_EMOJI.CAMERA} <b>Feedback Status:</b> {fb_status}\n"
        f"{HTML_EMOJI.STATS} <b>Account Status:</b> {status_label}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.TIP} Contact Admin or Reseller to renew or upgrade your plan."
    )


async def myinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user     = update.effective_user
    user_id  = user.id if user else 0
    chat     = update.effective_chat
    is_group = chat.type in ("group", "supergroup") if chat else False

    text = await build_profile_card(user, user_id, chat, is_group)
    await update.message.reply_text(text, parse_mode="HTML")



async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    back_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="start_back")]
    ])
    help_back_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Help Menu", callback_data="h_back")]
    ])
    chat_type = query.message.chat.type if query.message and query.message.chat else "private"
    is_group  = chat_type in ("group", "supergroup")

    # In groups, never treat user as admin/reseller in UI navigation
    is_owner_ui  = is_owner(user_id) if not is_group else False
    admin_ui     = await is_any_admin(user_id) if not is_group else False
    reseller_ui  = (await db.get_reseller(user_id) is not None) if not is_group else False


    if query.data in ("help", "h_back"):
        if is_owner_ui:
            keyboard = [
                [InlineKeyboardButton("👑 Owner Panel",      callback_data="h_owner"),
                 InlineKeyboardButton("🛠️ Admin Panel",      callback_data="h_admin_panel")],
                [InlineKeyboardButton("🏪 Reseller Panel",   callback_data="h_res"),
                 InlineKeyboardButton("🔑 Keys Management",   callback_data="h_keys")],
                [InlineKeyboardButton("👥 Groups & Users",   callback_data="h_grp"),
                 InlineKeyboardButton("⚙️ System Settings",  callback_data="h_sys")],
                [InlineKeyboardButton("🔥 Attack Commands",  callback_data="h_atk"),
                 InlineKeyboardButton("🛡️ User Commands",     callback_data="h_usr")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="start_back")]
            ]
            text = (
                f"{HTML_EMOJI.BOOK} <b>COMMAND HELP — 👑 OWNER</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Select a category below to view all commands:\n\n"
                f"{HTML_EMOJI.CROWN} <b>Owner Panel:</b> Admin management, owner chat, full control.\n"
                f"🛠️ <b>Admin Panel:</b> All administrative commands.\n"
                f"{HTML_EMOJI.STORE} <b>Reseller Panel:</b> Reseller commands &amp; key rules.\n"
                f"{HTML_EMOJI.KEY} <b>Keys Management:</b> Keys, trials, block codes.\n"
                f"{HTML_EMOJI.USERS} <b>Groups &amp; Users:</b> Authorize groups, user bans/blocks.\n"
                f"{HTML_EMOJI.SETTINGS} <b>Settings:</b> API, cooldown, ports, maintenance."
            )
        elif admin_ui:
            keyboard = [
                [InlineKeyboardButton("🏪 My Resellers",     callback_data="h_res"),
                 InlineKeyboardButton("🔑 Customer Keys",    callback_data="h_keys")],
                [InlineKeyboardButton("🔥 Attack Commands",  callback_data="h_atk"),
                 InlineKeyboardButton("🛡️ User Commands",     callback_data="h_usr")],
                [InlineKeyboardButton("👤 My Plan & Info",   callback_data="myinfo")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="start_back")]
            ]
            text = (
                f"{HTML_EMOJI.BOOK} <b>COMMAND HELP — 🛡️ ADMIN (Senior Reseller)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Select a category below to view commands:\n\n"
                f"{HTML_EMOJI.STORE} <b>My Resellers:</b> Add &amp; manage your appointed resellers.\n"
                f"{HTML_EMOJI.KEY} <b>Customer Keys:</b> Generate VIP keys for customers.\n"
                f"{HTML_EMOJI.FIRE} <b>Attacks:</b> Launch attacks (obeys Owner limits).\n"
                f"{HTML_EMOJI.SHIELD} <b>User Commands:</b> Redeem keys, view profile &amp; status."
            )
        elif reseller_ui:
            keyboard = [
                [InlineKeyboardButton("🏪 Reseller Panel",   callback_data="h_res")],
                [InlineKeyboardButton("🔥 Attack Commands",  callback_data="h_atk"),
                 InlineKeyboardButton("🛡️ User Commands",     callback_data="h_usr")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="start_back")]
            ]
            text = (
                f"{HTML_EMOJI.BOOK} <b>COMMAND HELP — 🏪 RESELLER</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Select a category to view commands:\n\n"
                f"{HTML_EMOJI.STORE} <b>Reseller Panel:</b> Generate keys, check balance &amp; rules.\n"
                f"{HTML_EMOJI.FIRE} <b>Attack:</b> Launch attacks &amp; check live status.\n"
                f"{HTML_EMOJI.SHIELD} <b>User:</b> Redeem keys, profile &amp; setup."
            )
        else:
            keyboard = [
                [InlineKeyboardButton("🔥 Attack Commands", callback_data="h_atk"),
                 InlineKeyboardButton("🛡️ User Commands",   callback_data="h_usr")],
                [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="start_back")]
            ]
            text = (
                f"{HTML_EMOJI.BOOK} <b>COMMAND HELP — 👤 USER</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Select a category to view commands:\n\n"
                f"{HTML_EMOJI.FIRE} <b>Attack:</b> Launch attacks &amp; check live status.\n"
                f"{HTML_EMOJI.SHIELD} <b>User:</b> Redeem keys, check plan &amp; profile."
            )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif query.data == "h_atk":
        vd     = await db.get_valid_durations()
        vd_str = ", ".join(str(d) for d in vd)
        text   = get_attack_help_text(vd_str)
        await query.edit_message_text(text, reply_markup=help_back_markup, parse_mode="HTML")

    elif query.data == "h_usr":
        text = get_user_help_text()
        await query.edit_message_text(text, reply_markup=help_back_markup, parse_mode="HTML")

    elif query.data == "h_res":
        if not (reseller_ui or admin_ui or is_owner_ui):
            return await query.answer("⛔ Reseller only.", show_alert=True)
        text = get_reseller_help_text()
        await query.edit_message_text(text, reply_markup=help_back_markup, parse_mode="HTML")

    elif query.data == "h_keys":
        if not (admin_ui or is_owner_ui):
            return await query.answer("⛔ Admin only.", show_alert=True)
        text = get_keys_help_text()
        await query.edit_message_text(text, reply_markup=help_back_markup, parse_mode="HTML")

    elif query.data == "h_grp":
        if not is_owner_ui:
            return await query.answer("⛔ Owner only.", show_alert=True)
        text = get_group_user_help_text()
        await query.edit_message_text(text, reply_markup=help_back_markup, parse_mode="HTML")

    elif query.data == "h_sys":
        if not is_owner_ui:
            return await query.answer("⛔ Owner only.", show_alert=True)
        text = get_system_help_text()
        await query.edit_message_text(text, reply_markup=help_back_markup, parse_mode="HTML")

    elif query.data == "h_owner":
        if not is_owner_ui:
            return await query.answer("⛔ Owner only.", show_alert=True)
        text = get_owner_help_text()
        await query.edit_message_text(text, reply_markup=help_back_markup, parse_mode="HTML")

    elif query.data == "h_admin_panel":
        if not is_owner_ui:
            return await query.answer("⛔ Owner only.", show_alert=True)
        text = get_admin_help_text()
        await query.edit_message_text(text, reply_markup=help_back_markup, parse_mode="HTML")

    elif query.data == "h_key":
        text = (
            f"{HTML_EMOJI.KEY} <b>HOW TO REDEEM YOUR ACCESS KEY</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.PIN} <b>Redeem Command:</b>\n"
            "Send: <code>/redeem &lt;your_key&gt;</code>\n"
            "<i>Example:</i> <code>/redeem DelvornCheat-30d-XYZ12</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        await query.edit_message_text(text, reply_markup=back_markup, parse_mode="HTML")

    elif query.data == "kp_menu":
        if not (admin_ui or is_owner_ui):
            return await query.answer("⛔ Admin only.", show_alert=True)
        auth_url = await ky.get_remote_server_url()
        online, ping_msg = await ky.ping_remote_server()
        server_badge = "🟢 ONLINE" if online else "🔴 OFFLINE"

        all_keys = await db.get_all_keys()
        tot_keys = len(all_keys)
        avail_keys = sum(1 for k in all_keys if k.get("used_count", 0) < k.get("max_users", 1) and not k.get("is_blocked"))
        blocked_keys = sum(1 for k in all_keys if k.get("is_blocked"))

        url_line = f"• URL: <code>{auth_url}</code>\n" if is_owner_ui else "• URL: <code>[PROTECTED]</code>\n"

        kp_text = (
            f"{HTML_EMOJI.CROWN} <b>KEY MANAGEMENT &amp; AUTH CONTROL PANEL</b> {HTML_EMOJI.CROWN}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 <b>Remote Auth Server:</b>\n"
            f"{url_line}"
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
        kp_kb = [
            [InlineKeyboardButton("📋 All Keys List", callback_data="h_keys"),
             InlineKeyboardButton("🌐 Ping Server",   callback_data="kp_ping")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="start_back")]
        ]
        await query.edit_message_text(kp_text, reply_markup=InlineKeyboardMarkup(kp_kb), parse_mode="HTML")

    elif query.data == "kp_ping":
        online, ping_msg = await ky.ping_remote_server()
        badge = "🟢 ONLINE" if online else "🔴 OFFLINE"
        await query.answer(f"Server {badge}: {ping_msg}", show_alert=True)

    elif query.data == "status":
        status_text = atk.build_status_text()
        await query.edit_message_text(
            f"{HTML_EMOJI.STATS} <b>Attack Status:</b>\n\n{status_text}",
            reply_markup=back_markup,
            parse_mode="HTML"
        )

    elif query.data == "myinfo":
        user_obj = query.from_user
        chat     = update.effective_chat
        is_group = chat.type in ("group", "supergroup") if chat else False
        info_text = await build_profile_card(user_obj, user_id, chat, is_group)
        await query.edit_message_text(info_text, reply_markup=back_markup, parse_mode="HTML")


    elif query.data == "start_back":
        user_name = query.from_user.first_name or "Friend"
        reseller_acc = await db.get_reseller(user_id)
        u         = await db.get_user(user_id) if not admin_ui else None

        if admin_ui:
            role = f"{HTML_EMOJI.CROWN} ADMIN"
        elif reseller_acc and not is_group:
            role = f"{HTML_EMOJI.STORE} RESELLER"
        elif u and (u["expiry"] == 0 or u["expiry"] > int(time.time())):
            role = f"{HTML_EMOJI.LIGHTNING} VIP USER"
        elif is_admin(user_id) and is_group:
            role = f"{HTML_EMOJI.LIGHTNING} VIP USER"
        else:
            role = f"{HTML_EMOJI.PROFILE} FREE USER"

        text = (
            f"{HTML_EMOJI.WAVE} <b>Welcome to Delvorn Stresser, {html.escape(user_name)}!</b> {HTML_EMOJI.FIRE}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.PROFILE} <b>Account:</b> <code>{html.escape(user_name)}</code>\n"
            f"{HTML_EMOJI.PROFILE} <b>User ID:</b> <code>{user_id}</code>\n"
            f"{HTML_EMOJI.MEDAL} <b>Status:</b> <code>{role}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{HTML_EMOJI.KEY} <b>Key Activation:</b>\n"
            "To unlock private access, buy a key from Admin and redeem it:\n"
            f"{HTML_EMOJI.ARROW_RIGHT} <code>/redeem &lt;your_key&gt;</code>\n\n"
            f"{HTML_EMOJI.LIGHTNING} <b>Free Group Access:</b>\n"
            "You can use all attack features without a key inside any authorized group!\n\n"
            f"{HTML_EMOJI.BOOK} <b>Setup Tutorial:</b> <code>/setup</code> to watch video tutorial.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        markup = get_start_markup() if admin_ui else None
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")



# ─────────────────────────────────────────
#  SETCONC (shortcut kept from old bot)
# ─────────────────────────────────────────

async def setconc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await adm.setconcurrent_cmd(update, context)


# ─────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────

def main():
    print("[*] Starting bot...")
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .connection_pool_size(100)
        .pool_timeout(30.0)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .build()
    )




    # ── Basic ──────────────────────────────────────────────
    app.add_handler(CommandHandler("start",   start_cmd))
    app.add_handler(CommandHandler("setup",   setup_cmd))
    app.add_handler(CommandHandler("canary",  canary_cmd))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("myinfo",  myinfo_cmd))
    app.add_handler(CommandHandler("myplan",  myinfo_cmd))
    app.add_handler(CommandHandler("ping",    atk.ping_cmd))
    app.add_handler(CommandHandler("getid",   atk.getid_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))

    # ── Attack ─────────────────────────────────────────────
    app.add_handler(CommandHandler("attack", atk.attack_cmd))
    app.add_handler(CommandHandler("status", atk.status_cmd))

    # ── Keys ───────────────────────────────────────────────
    app.add_handler(CommandHandler("redeem",        ky.redeem_cmd))
    app.add_handler(CommandHandler("checkkey",      ky.checkkey_cmd))
    app.add_handler(CommandHandler("verifykey",     ky.checkkey_cmd))
    app.add_handler(CommandHandler("keypanel",      ky.keypanel_cmd))
    app.add_handler(CommandHandler("keys",          ky.keypanel_cmd))
    app.add_handler(CommandHandler("authserver",    ky.authserver_cmd))
    app.add_handler(CommandHandler("setauthserver", ky.setauthserver_cmd))
    app.add_handler(CommandHandler("genkey",        ky.genkey_cmd))
    app.add_handler(CommandHandler("delkey",        ky.delete_code_cmd))
    app.add_handler(CommandHandler("delete_code",   ky.delete_code_cmd))
    app.add_handler(CommandHandler("gentrial",      ky.gentrial_cmd))
    app.add_handler(CommandHandler("gentrialfor",   ky.gentrialfor_cmd))
    app.add_handler(CommandHandler("deletetrials",  ky.deletetrials_cmd))
    app.add_handler(CommandHandler("list_codes",    ky.list_codes_cmd))
    app.add_handler(CommandHandler("block_code",    ky.block_code_cmd))
    app.add_handler(CommandHandler("unblock_code",  ky.unblock_code_cmd))

    # ── Reseller ───────────────────────────────────────────
    app.add_handler(CommandHandler("addreseller",     res.add_reseller_cmd))
    app.add_handler(CommandHandler("add_reseller",    res.add_reseller_cmd))
    app.add_handler(CommandHandler("delreseller",     res.remove_reseller_cmd))
    app.add_handler(CommandHandler("remove_reseller", res.remove_reseller_cmd))
    app.add_handler(CommandHandler("resellerlist",    res.resellers_cmd))
    app.add_handler(CommandHandler("resellers",       res.resellers_cmd))
    app.add_handler(CommandHandler("addcredit",       res.addcredit_cmd))
    app.add_handler(CommandHandler("removecredit",    res.removecredit_cmd))
    app.add_handler(CommandHandler("reseller_info",   res.reseller_info_cmd))

    # ── Group ──────────────────────────────────────────────
    app.add_handler(CommandHandler("approvegroup",    grp.approve_cmd))
    app.add_handler(CommandHandler("approve",         grp.approve_cmd))
    app.add_handler(CommandHandler("delgroup",        grp.disapprove_cmd))
    app.add_handler(CommandHandler("disapprove",      grp.disapprove_cmd))
    app.add_handler(CommandHandler("grouplist",       grp.approved_groups_cmd))
    app.add_handler(CommandHandler("approved_groups", grp.approved_groups_cmd))

    # ── Admin ──────────────────────────────────────────────
    app.add_handler(CommandHandler("ban",              adm.ban_cmd))
    app.add_handler(CommandHandler("unban",            adm.unban_cmd))
    app.add_handler(CommandHandler("banned_list",      adm.banned_list_cmd))
    app.add_handler(CommandHandler("block",            adm.block_cmd))
    app.add_handler(CommandHandler("unblock",          adm.unblock_cmd))
    app.add_handler(CommandHandler("blocklist",        adm.blocklist_cmd))
    app.add_handler(CommandHandler("users",            adm.users_cmd))
    app.add_handler(CommandHandler("userlist",         adm.userlist_cmd))
    app.add_handler(CommandHandler("remove",           adm.remove_cmd))
    app.add_handler(CommandHandler("remove_expired",   adm.remove_expired_cmd))
    app.add_handler(CommandHandler("reset_user",       adm.reset_user_cmd))
    app.add_handler(CommandHandler("get_user_info",    adm.get_user_info_cmd))
    app.add_handler(CommandHandler("set_user_limit",   adm.set_user_limit_cmd))
    app.add_handler(CommandHandler("settime",          adm.settime_cmd))
    app.add_handler(CommandHandler("setcooldown",      adm.setcooldown_cmd))
    app.add_handler(CommandHandler("delcooldown",      adm.delcooldown_cmd))
    app.add_handler(CommandHandler("del_cooldown",     adm.delcooldown_cmd))
    app.add_handler(CommandHandler("setconcurrent",    adm.setconcurrent_cmd))
    app.add_handler(CommandHandler("setconc",          setconc_cmd))
    app.add_handler(CommandHandler("setapi",           adm.setapi_cmd))
    app.add_handler(CommandHandler("changeapikey",     adm.changeapikey_cmd))
    app.add_handler(CommandHandler("port_protection",  adm.port_protection_cmd))
    app.add_handler(CommandHandler("block_port",       adm.block_port_cmd))
    app.add_handler(CommandHandler("state",            adm.state_cmd))
    app.add_handler(CommandHandler("server_stats",     adm.server_stats_cmd))
    app.add_handler(CommandHandler("broadcast",        adm.broadcast_cmd))
    app.add_handler(CommandHandler("backup_users",     adm.backup_users_cmd))
    app.add_handler(CommandHandler("restore_users",    adm.restore_users_cmd))
    app.add_handler(CommandHandler("load_users",       adm.restore_users_cmd))
    app.add_handler(CommandHandler("cancel",           adm.cancel_restore_cmd))
    app.add_handler(CommandHandler("extend_all_users", adm.extend_all_users_cmd))
    app.add_handler(CommandHandler("maintenanceon",    adm.maintenanceon_cmd))
    app.add_handler(CommandHandler("maintenanceoff",   adm.maintenanceoff_cmd))
    app.add_handler(CommandHandler("setvideo",         atk.setvideo_cmd))
    # Owner-only admin management
    app.add_handler(CommandHandler("addadmin",         adm.addadmin_cmd))
    app.add_handler(CommandHandler("deladmin",         adm.deladmin_cmd))
    app.add_handler(CommandHandler("adminlist",        adm.adminlist_cmd))
    app.add_handler(CommandHandler("setimageforward",  adm.setimageforward_cmd))
    # Owner-to-owner messaging
    app.add_handler(CommandHandler("omsg",             adm.omsg_cmd))
    app.add_handler(CommandHandler("oreply",           adm.omsg_cmd))  # alias

    # ── Group Image Auto-Forward ────────────────────────────
    # Handle both compressed photos and uncompressed image documents
    app.add_handler(MessageHandler(
        (filters.PHOTO | (filters.Document.IMAGE)) & filters.ChatType.GROUPS,
        group_image_forward_handler
    ))

    # ── Owner Backup JSON Document Receiver (Private Chat) ──
    app.add_handler(MessageHandler(
        filters.Document.ALL & filters.ChatType.PRIVATE,
        adm.restore_users_document_handler
    ))



    # ── Feedback & Forwarding ──────────────────────────────
    app.add_handler(CommandHandler("feedbackon",       fb.feedbackon_cmd))
    app.add_handler(CommandHandler("feedbackoff",      fb.feedbackoff_cmd))
    # Forward add command & aliases:
    app.add_handler(CommandHandler("forward",          fb.feebackforwad_cmd))
    app.add_handler(CommandHandler("forwardadd",       fb.feebackforwad_cmd))
    app.add_handler(CommandHandler("addforward",       fb.feebackforwad_cmd))
    app.add_handler(CommandHandler("feebackforwad",    fb.feebackforwad_cmd))
    app.add_handler(CommandHandler("feedbackforward",  fb.feebackforwad_cmd))
    app.add_handler(CommandHandler("setfeedback",      fb.feebackforwad_cmd))
    # Forward list command & aliases:
    app.add_handler(CommandHandler("forwardlist",      fb.forwadlist_cmd))
    app.add_handler(CommandHandler("forwadlist",       fb.forwadlist_cmd))
    app.add_handler(CommandHandler("feedbacklist",     fb.forwadlist_cmd))
    # Forward delete command & aliases:
    app.add_handler(CommandHandler("delforward",       fb.delforward_cmd))
    app.add_handler(CommandHandler("removeforward",    fb.delforward_cmd))
    app.add_handler(fb.get_feedback_handler())

    # ── Error Handler ───────────────────────────────────────
    async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        logging.error("Exception while handling an update:", exc_info=context.error)

    app.add_error_handler(global_error_handler)

    print("[+] All handlers registered. Running...")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    app.run_polling()


if __name__ == "__main__":
    main()
