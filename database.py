"""
database.py — PostgreSQL connection pool + all table operations via asyncpg.
All functions are async. Call `init_db(app)` on bot startup via post_init.
"""
import os, time, asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/botdb")

# Global connection pool (set by init_db)
pool: asyncpg.Pool = None


# ─────────────────────────────────────────
#  INIT — create tables if not exist
# ─────────────────────────────────────────

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id          BIGINT PRIMARY KEY,
    username         TEXT,
    join_date        BIGINT   DEFAULT 0,
    expiry           BIGINT   DEFAULT 0,
    max_time         INTEGER  DEFAULT 240,
    cooldown         INTEGER  DEFAULT 30,
    last_attack      BIGINT   DEFAULT 0,
    attacks_count    INTEGER  DEFAULT 0,
    feedback_pending BOOLEAN  DEFAULT FALSE,
    key_used         TEXT     DEFAULT ''
);

CREATE TABLE IF NOT EXISTS keys (
    code        TEXT PRIMARY KEY,
    duration    INTEGER  DEFAULT 240,
    cooldown    INTEGER  DEFAULT 30,
    valid_days  INTEGER  DEFAULT 30,
    created_at  BIGINT   DEFAULT 0,
    used_by     BIGINT   DEFAULT NULL,
    max_users   INTEGER  DEFAULT 1,
    used_count  INTEGER  DEFAULT 0,
    is_trial    BOOLEAN  DEFAULT FALSE,
    is_blocked  BOOLEAN  DEFAULT FALSE,
    created_by  BIGINT   DEFAULT 0
);

CREATE TABLE IF NOT EXISTS resellers (
    user_id        BIGINT PRIMARY KEY,
    name           TEXT DEFAULT '',
    username       TEXT DEFAULT '',
    credits        INTEGER DEFAULT 0,
    keys_generated INTEGER DEFAULT 0,
    expiry         BIGINT DEFAULT 0,
    added_by       BIGINT  DEFAULT 0
);

CREATE TABLE IF NOT EXISTS groups (
    chat_id      BIGINT PRIMARY KEY,
    chat_name    TEXT,
    concurrency  INTEGER DEFAULT 3,
    max_time     INTEGER DEFAULT 240,
    cooldown     INTEGER DEFAULT 30,
    expiry       BIGINT DEFAULT 0,
    approved_by  BIGINT  DEFAULT 0
);

CREATE TABLE IF NOT EXISTS banned (
    user_id  BIGINT PRIMARY KEY,
    username TEXT DEFAULT '',
    reason   TEXT DEFAULT '',
    banned_at BIGINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admins (
    user_id    BIGINT PRIMARY KEY,
    name       TEXT DEFAULT '',
    username   TEXT DEFAULT '',
    balance    INTEGER DEFAULT 0,
    added_by   BIGINT DEFAULT 0,
    added_at   BIGINT DEFAULT 0,
    expiry     BIGINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS blocked_ports (
    ip   TEXT,
    port INTEGER,
    PRIMARY KEY (ip, port)
);

CREATE TABLE IF NOT EXISTS attack_videos (
    id SERIAL PRIMARY KEY,
    file_id TEXT UNIQUE,
    file_type TEXT DEFAULT 'video',
    added_at BIGINT DEFAULT 0
);
"""

DEFAULT_SETTINGS = {
    "max_time":           "300",
    "cooldown":           "30",
    "concurrent":         "3",
    "api_url":            "https://automation.delvorn.in/attack",
    "api_key":            "",
    "port_protection":    "false",
    "feedback":           "false",
    "feedback_destination": "",
    "maintenance":        "false",
    "canary_link":        "",
    "setup_link":         "",
    "valid_durations":    "60,90,120,150,180,210,240,270,300",
    "image_forward_channel": "",   # channel id/username to forward group images
}


async def init_db(application=None):
    """Create pool + tables + seed default settings."""
    global pool
    pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=10,
        max_size=50,
        command_timeout=30,
        max_inactive_connection_lifetime=300
    )
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLES_SQL)

        # Migrate resellers and groups table columns if missing
        await conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='resellers' AND column_name='name') THEN
                    ALTER TABLE resellers ADD COLUMN name TEXT DEFAULT '';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='resellers' AND column_name='username') THEN
                    ALTER TABLE resellers ADD COLUMN username TEXT DEFAULT '';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='resellers' AND column_name='expiry') THEN
                    ALTER TABLE resellers ADD COLUMN expiry BIGINT DEFAULT 0;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='groups' AND column_name='expiry') THEN
                    ALTER TABLE groups ADD COLUMN expiry BIGINT DEFAULT 0;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='keys' AND column_name='max_users') THEN
                    ALTER TABLE keys ADD COLUMN max_users INTEGER DEFAULT 1;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='keys' AND column_name='used_count') THEN
                    ALTER TABLE keys ADD COLUMN used_count INTEGER DEFAULT 0;
                END IF;
                -- Shared key: expiry_ts set when first user redeems
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='keys' AND column_name='shared_expiry_ts') THEN
                    ALTER TABLE keys ADD COLUMN shared_expiry_ts BIGINT DEFAULT 0;
                END IF;
                -- banned table extensions
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='banned' AND column_name='username') THEN
                    ALTER TABLE banned ADD COLUMN username TEXT DEFAULT '';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='banned' AND column_name='reason') THEN
                    ALTER TABLE banned ADD COLUMN reason TEXT DEFAULT '';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='banned' AND column_name='banned_at') THEN
                    ALTER TABLE banned ADD COLUMN banned_at BIGINT DEFAULT 0;
                END IF;
                -- admins table expiry
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='admins' AND column_name='expiry') THEN
                    ALTER TABLE admins ADD COLUMN expiry BIGINT DEFAULT 0;
                END IF;
            END $$;
        """)
        # Seed default settings (skip if already exist)
        for k, v in DEFAULT_SETTINGS.items():
            await conn.execute(
                "INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING",
                k, v
            )
        # Clear obsolete/placeholder URLs from settings if previously stored
        await conn.execute("""
            UPDATE settings 
            SET value = '' 
            WHERE key IN ('setup_link', 'canary_link') 
              AND (value LIKE '%DelvornSetup%' OR value = 'https://t.me/' OR value = 'https://t.me')
        """)
        # Do NOT hardcode api_url — admin must set via /setapi
        # Ensure users max_time is at least the configured global max_time if they were stuck at 240
        row_max = await conn.fetchrow("SELECT value FROM settings WHERE key = 'max_time'")
        if row_max and row_max["value"].isdigit():
            curr_max_val = int(row_max["value"])
            await conn.execute("UPDATE users SET max_time = $1 WHERE max_time < $1", curr_max_val)
        # Ensure users cooldown matches global cooldown
        row_cd = await conn.fetchrow("SELECT value FROM settings WHERE key = 'cooldown'")
        if row_cd and row_cd["value"].isdigit():
            curr_cd_val = int(row_cd["value"])
            await conn.execute("UPDATE users SET cooldown = $1", curr_cd_val)
    print("[DB] PostgreSQL connected & tables ready.")


# ─────────────────────────────────────────
#  SETTINGS helpers
# ─────────────────────────────────────────

async def get_setting(key: str) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM settings WHERE key=$1", key)
        return row["value"] if row else DEFAULT_SETTINGS.get(key, "")


async def set_setting(key: str, value: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO UPDATE SET value=$2",
            key, value
        )


async def get_settings_dict() -> dict:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}


async def get_valid_durations() -> list:
    raw = await get_setting("valid_durations")
    return [int(x) for x in raw.split(",")]


# ─────────────────────────────────────────
#  USERS
# ─────────────────────────────────────────

async def get_user(user_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)
        return dict(row) if row else None


async def upsert_user(user_id: int, username: str = "", join_date: int = 0,
                      expiry: int = 0, max_time: int = 240, cooldown: int = 30,
                      key_used: str = ""):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users(user_id, username, join_date, expiry, max_time, cooldown, key_used)
            VALUES($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT(user_id) DO UPDATE
            SET username=$2,
                join_date=CASE WHEN users.join_date > 0 THEN users.join_date ELSE $3 END,
                -- When a new key is redeemed ($7 <> ''), set exact new expiry; otherwise preserve highest
                expiry=CASE 
                    WHEN $7 <> '' THEN EXCLUDED.expiry 
                    ELSE GREATEST(users.expiry, EXCLUDED.expiry) 
                END,
                max_time=$5,
                cooldown=$6,
                -- Only overwrite key_used if a non-empty key is provided
                key_used=CASE WHEN $7 <> '' THEN $7 ELSE users.key_used END
        """, user_id, username, join_date, expiry, max_time, cooldown, key_used)


async def update_user_attack(user_id: int, last_attack: int, attacks_count: int):
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE users SET last_attack=$2, attacks_count=$3 WHERE user_id=$1
        """, user_id, last_attack, attacks_count)


async def set_feedback_pending(user_id: int, pending: bool):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET feedback_pending=$2 WHERE user_id=$1", user_id, pending)


async def set_user_limits(user_id: int, max_time: int, cooldown: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET max_time=$2, cooldown=$3 WHERE user_id=$1", user_id, max_time, cooldown)


async def reset_user(user_id: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET attacks_count=0, feedback_pending=FALSE WHERE user_id=$1", user_id)


async def delete_user(user_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM users WHERE user_id=$1", user_id)


async def delete_expired_users() -> int:
    now = int(time.time())
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM users WHERE expiry > 0 AND expiry < $1", now)
        return int(result.split()[-1])


async def restore_users(users: list) -> tuple[int, int]:
    """
    Restore or load users from a backup list into the database.
    Returns (restored_count, error_count).
    """
    restored = 0
    errors = 0
    query = """
        INSERT INTO users (
            user_id, username, join_date, expiry, max_time, cooldown,
            last_attack, attacks_count, feedback_pending, key_used
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        ON CONFLICT (user_id) DO UPDATE SET
            username = EXCLUDED.username,
            join_date = CASE WHEN users.join_date > 0 THEN users.join_date ELSE EXCLUDED.join_date END,
            expiry = GREATEST(users.expiry, EXCLUDED.expiry),
            max_time = EXCLUDED.max_time,
            cooldown = EXCLUDED.cooldown,
            last_attack = GREATEST(users.last_attack, EXCLUDED.last_attack),
            attacks_count = GREATEST(users.attacks_count, EXCLUDED.attacks_count),
            key_used = CASE WHEN EXCLUDED.key_used <> '' THEN EXCLUDED.key_used ELSE users.key_used END
    """
    async with pool.acquire() as conn:
        for u in users:
            try:
                user_id = int(u.get("user_id", 0))
                if not user_id:
                    errors += 1
                    continue
                username = str(u.get("username") or "")
                join_date = int(u.get("join_date") or 0)
                expiry = int(u.get("expiry") or 0)
                max_time = int(u.get("max_time") or 240)
                cooldown = int(u.get("cooldown") or 30)
                last_attack = int(u.get("last_attack") or 0)
                attacks_count = int(u.get("attacks_count") or 0)
                feedback_pending = bool(u.get("feedback_pending", False))
                key_used = str(u.get("key_used") or "")

                await conn.execute(
                    query,
                    user_id, username, join_date, expiry, max_time,
                    cooldown, last_attack, attacks_count, feedback_pending, key_used
                )
                restored += 1
            except Exception:
                errors += 1
    return restored, errors


async def get_all_users() -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM users")
        return [dict(r) for r in rows]


async def extend_all_users(seconds: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET expiry = expiry + $1 WHERE expiry > 0", seconds)


# ─────────────────────────────────────────
#  KEYS
# ─────────────────────────────────────────

async def get_key(code: str) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM keys WHERE code=$1", code)
        return dict(row) if row else None


async def insert_key(code: str, duration: int, cooldown: int, valid_days: int,
                     created_at: int, is_trial: bool, created_by: int, max_users: int = 1):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO keys(code, duration, cooldown, valid_days, created_at, is_trial, created_by, max_users, used_count)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,0)
        """, code, duration, cooldown, valid_days, created_at, is_trial, created_by, max_users)


async def use_key(code: str, user_id: int):
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE keys 
            SET used_by=$2, used_count = COALESCE(used_count, 0) + 1 
            WHERE code=$1
        """, code, user_id)


async def block_key(code: str):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE keys SET is_blocked=TRUE WHERE code=$1", code)


async def unblock_key(code: str):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE keys SET is_blocked=FALSE WHERE code=$1", code)


async def delete_key(code: str):
    """Deletes key and instantly revokes access of anyone who redeemed it."""
    async with pool.acquire() as conn:
        # Instantly expire anyone who used this key
        await conn.execute("""
            UPDATE users SET expiry = 1 
            WHERE key_used = $1
        """, code)
        await conn.execute("DELETE FROM keys WHERE code=$1", code)


async def delete_trial_keys():
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM keys WHERE is_trial=TRUE AND (used_by IS NULL OR used_count=0)")
        return int(result.split()[-1])


async def cleanup_unused_and_expired_keys() -> tuple[int, int]:
    """Deletes:
    1. Unused keys whose validity window has fully expired (created_at + valid_days < now)
    2. Unused TRIAL keys that are past their validity window
    3. Fully used keys whose users are all expired (no active user holds this key)
    Returns: (deleted_count, remaining_active_count)
    """
    now = int(time.time())
    async with pool.acquire() as conn:
        res = await conn.execute("""
            DELETE FROM keys
            WHERE
                -- Case 1: Key was never redeemed AND its validity window has passed
                (used_count = 0 AND valid_days > 0 AND (created_at + valid_days) < $1)
                -- Case 2: Key was fully used AND none of the users who used it are still active
                OR (used_count >= max_users AND code NOT IN (
                    SELECT key_used FROM users WHERE expiry > $1
                ))
        """, now)
        deleted_count = int(res.split()[-1])

        # Remaining active keys
        active_count = await conn.fetchval("SELECT COUNT(*) FROM keys")
        return deleted_count, int(active_count or 0)


async def get_all_keys() -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM keys ORDER BY created_at DESC")
        return [dict(r) for r in rows]


# ─────────────────────────────────────────
#  RESELLERS
# ─────────────────────────────────────────

async def get_reseller(user_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM resellers WHERE user_id=$1", user_id)
        return dict(row) if row else None


async def is_reseller(user_id: int) -> bool:
    r = await get_reseller(user_id)
    return r is not None



async def add_reseller(user_id: int, credits: int, added_by: int, name: str = "", username: str = "", expiry: int = 0):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO resellers(user_id, name, username, credits, expiry, added_by)
            VALUES($1,$2,$3,$4,$5,$6)
            ON CONFLICT(user_id) DO UPDATE SET 
                name=EXCLUDED.name,
                username=EXCLUDED.username,
                credits=$4,
                expiry=$5,
                added_by=$6
        """, user_id, name, username, credits, expiry, added_by)


async def remove_reseller(user_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM resellers WHERE user_id=$1", user_id)


async def update_reseller_credits(user_id: int, delta: int) -> int:
    """Add or subtract credits. Returns new balance."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE resellers SET credits = credits + $2
            WHERE user_id=$1 RETURNING credits
        """, user_id, delta)
        return row["credits"] if row else 0


async def increment_reseller_keys(user_id: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE resellers SET keys_generated = keys_generated + 1 WHERE user_id=$1", user_id
        )


async def get_all_resellers() -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM resellers")
        return [dict(r) for r in rows]


# ─────────────────────────────────────────
#  GROUPS
# ─────────────────────────────────────────

async def get_group(chat_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM groups WHERE chat_id=$1", chat_id)
        return dict(row) if row else None


async def approve_group(chat_id: int, chat_name: str, concurrency: int,
                        max_time: int, cooldown: int, approved_by: int, expiry: int = 0):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO groups(chat_id, chat_name, concurrency, max_time, cooldown, approved_by, expiry)
            VALUES($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT(chat_id) DO UPDATE
            SET chat_name=$2, concurrency=$3, max_time=$4, cooldown=$5, approved_by=$6, expiry=$7
        """, chat_id, chat_name, concurrency, max_time, cooldown, approved_by, expiry)


async def disapprove_group(chat_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM groups WHERE chat_id=$1", chat_id)


async def get_all_groups() -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM groups")
        return [dict(r) for r in rows]


# ─────────────────────────────────────────
#  BANNED
# ─────────────────────────────────────────

async def is_banned(user_id: int) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM banned WHERE user_id=$1", user_id)
        return row is not None


async def ban_user(user_id: int, username: str = "", reason: str = ""):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO banned(user_id, username, reason, banned_at)
            VALUES($1,$2,$3,$4)
            ON CONFLICT(user_id) DO UPDATE SET username=$2, reason=$3, banned_at=$4
        """, user_id, username, reason, int(time.time()))


async def unban_user(user_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM banned WHERE user_id=$1", user_id)


async def get_all_banned() -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, username, reason, banned_at FROM banned")
        return [dict(r) for r in rows]


# ─────────────────────────────────────────
#  ADMINS (owner-appointed)
# ─────────────────────────────────────────

async def get_admin(user_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM admins WHERE user_id=$1", user_id)
        return dict(row) if row else None


async def add_admin(user_id: int, name: str, username: str, balance: int, added_by: int, expiry: int = 0):
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO admins(user_id, name, username, balance, added_by, added_at, expiry)
            VALUES($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT(user_id) DO UPDATE SET name=$2, username=$3, balance=$4, added_by=$5, expiry=$7
        """, user_id, name, username, balance, added_by, int(time.time()), expiry)


async def remove_admin(user_id: int):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM admins WHERE user_id=$1", user_id)


async def get_all_admins() -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM admins")
        return [dict(r) for r in rows]


async def update_admin_balance(user_id: int, delta: int) -> int:
    """Add/subtract admin balance. Returns new balance."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            UPDATE admins SET balance = balance + $2
            WHERE user_id=$1 RETURNING balance
        """, user_id, delta)
        return row["balance"] if row else 0


async def is_db_admin(user_id: int) -> bool:
    r = await get_admin(user_id)
    if not r:
        return False
    if r.get("expiry", 0) > 0 and int(time.time()) > r["expiry"]:
        return False
    return True


# ─────────────────────────────────────────
#  SHARED KEY helpers
# ─────────────────────────────────────────

async def get_key_shared_expiry(code: str) -> int:
    """Returns shared_expiry_ts for a multi-user key. 0 = not yet activated."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT shared_expiry_ts FROM keys WHERE code=$1", code)
        return row["shared_expiry_ts"] if row else 0


async def set_key_shared_expiry(code: str, expiry_ts: int):
    """Set the shared expiry timestamp when the FIRST user redeems a multi-user key."""
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE keys SET shared_expiry_ts=$2 WHERE code=$1 AND shared_expiry_ts=0
        """, code, expiry_ts)


async def get_resellers_by_admin(admin_id: int) -> list:
    """Get all resellers added by a specific admin."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM resellers WHERE added_by=$1", admin_id)
        return [dict(r) for r in rows]


# ─────────────────────────────────────────
#  BLOCKED PORTS
# ─────────────────────────────────────────

async def add_blocked_port(ip: str, port: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO blocked_ports(ip,port) VALUES($1,$2) ON CONFLICT DO NOTHING", ip, port
        )


async def is_port_blocked(ip: str, port: int) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM blocked_ports WHERE ip=$1 AND port=$2", ip, port)
        return row is not None


async def get_all_blocked_ports() -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT ip, port FROM blocked_ports")
        return [dict(r) for r in rows]


# ─────────────────────────────────────────
#  ATTACK VIDEOS (Database Storage)
# ─────────────────────────────────────────

async def add_attack_video(file_id: str, file_type: str = "video") -> bool:
    async with pool.acquire() as conn:
        res = await conn.execute("""
            INSERT INTO attack_videos(file_id, file_type, added_at)
            VALUES($1, $2, $3)
            ON CONFLICT (file_id) DO NOTHING
        """, file_id, file_type, int(time.time()))
        return "INSERT" in res


async def get_all_attack_videos() -> list:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT file_id, file_type FROM attack_videos ORDER BY id ASC")
        return [dict(r) for r in rows]


async def delete_attack_video(file_id: str) -> bool:
    async with pool.acquire() as conn:
        res = await conn.execute("DELETE FROM attack_videos WHERE file_id=$1", file_id)
        return "DELETE" in res

