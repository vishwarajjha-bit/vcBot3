import io
import os
import re
import sys
import json
import time
import hmac
import wave
import random
import struct
import base64
import signal
import hashlib
import logging
import sqlite3
import asyncio
import traceback
from datetime import datetime, timezone, timedelta

import qrcode
from PIL import Image, ImageDraw, ImageFont
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from famgateway import FamGateway, FamGatewayError

# =====================================================================
# 0. LOGGING & INDIAN STANDARD TIME (IST - UTC+05:30) ENGINE
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("PaidVCBot")

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    """Return current datetime in Indian Standard Time (UTC+05:30)."""
    return datetime.now(IST)


def log_date_ist() -> str:
    """Short IST timestamp for logs and UI headers."""
    return now_ist().strftime("%b %d %H:%M:%S")


def full_date_ist() -> str:
    """Full readable IST timestamp for receipts and database records."""
    return now_ist().strftime("%d-%m-%Y %I:%M:%S %p IST")


def fmt_num(val: float) -> int | float:
    """Format float cleanly: remove .0 if integer, else round to 2 decimals."""
    try:
        fval = float(val)
        return int(fval) if fval.is_integer() else round(fval, 2)
    except Exception:
        return 0


def esc_html(text: str) -> str:
    """Escape HTML special characters safely for Telegram HTML parse mode."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def make_progress_bar(current: int, total: int, length: int = 10) -> tuple[str, int]:
    """Generate a clean progress bar string and integer percentage."""
    tot = max(1, total)
    ratio = min(1.0, max(0.0, current / tot))
    filled = int(round(ratio * length))
    if current > 0 and filled == 0:
        filled = 1
    if current < tot and filled == length:
        filled = length - 1
    bar = "▰" * filled + "▱" * (length - filled)
    pct = min(100, int((current / tot) * 100))
    return bar, pct


# =====================================================================
# 1. UNIVERSAL AUTO-PATCH FOR PYROGRAM / KURIGRAM & PYTGCALLS
# =====================================================================
try:
    import pyrogram.utils as _pu

    _pu.MIN_CHAT_ID = -999999999999
    _pu.MIN_CHANNEL_ID = -100999999999999
    _pu.MAX_CHANNEL_ID = -1000000000000
    _pu.MAX_USER_ID = 999999999999

    def _patched_get_peer_type(peer_id: int) -> str:
        if peer_id < 0:
            if -999999999999 <= peer_id < 0 and not str(peer_id).startswith("-100"):
                return "chat"
            if str(peer_id).startswith("-100"):
                return "channel"
        elif peer_id > 0:
            return "user"
        raise ValueError(f"Peer id invalid: {peer_id}")

    _pu.get_peer_type = _patched_get_peer_type

    import pyrogram.errors as _pe

    _missing_groupcall_errors = [
        "GroupcallForbidden",
        "GroupcallInvalid",
        "GroupcallNotModified",
        "GroupcallAlreadyStarted",
        "GroupcallJoinMissing",
        "GroupcallParticipantsTooMuch",
        "GroupcallSsrcDuplicateMuch",
        "GroupcallAddParticipantsFailed",
        "VoiceMessagesForbidden",
    ]
    for _err_name in _missing_groupcall_errors:
        if not hasattr(_pe, _err_name):
            setattr(_pe, _err_name, type(_err_name, (Exception,), {}))

    def _pe_getattr(name: str):
        cls = type(name, (Exception,), {})
        setattr(_pe, name, cls)
        return cls

    _pe.__getattr__ = _pe_getattr

    from pyrogram import Client
    from pyrogram.errors import (
        SessionPasswordNeeded,
        PhoneCodeInvalid,
        PhoneCodeExpired,
        PasswordHashInvalid,
        FloodWait,
        UserAlreadyParticipant,
    )
    from pytgcalls import PyTgCalls

    try:
        from pytgcalls.types import MediaStream
        HAS_MEDIA_STREAM = True
    except ImportError:
        from pytgcalls.types.input_stream import AudioPiped
        HAS_MEDIA_STREAM = False

    VC_ENGINE_AVAILABLE = True
except ImportError as e:
    VC_ENGINE_AVAILABLE = False
    logger.warning(f"VC Engine dependencies missing ({e}). Install kurigram/pyrogram & py-tgcalls.")


# =====================================================================
# 2. CONFIGURATION, COMPLETE EMOJI REGISTRY & GRANULAR ADMIN POWERS
# =====================================================================
BOT_TOKEN = "9138059759:AAEO83c45dPyy8NhblRwvVLZCzdVxFzXevQ"
FAMGATEWAY_API_KEY = "fam_992c7775340cc78d1206cb26ee98263ef271490e"

DEFAULT_SUPER_OWNER_ID = 8710593064
OWNER_USERNAME = "LakshmanX"

TG_API_ID = 2040
TG_API_HASH = "b18441a1ff607e10a989891a5462e627"

WEBHOOK_HOST = "0.0.0.0"
WEBHOOK_PORT = 8080

# Every single emoji used across the entire bot is registered here & editable in Admin Panel
DEFAULT_EMOJIS: dict[str, tuple[str, str]] = {
    "WELCOME":   ('<tg-emoji emoji-id="5357315181649076022">✨</tg-emoji>', "Welcome Splash Screen Header"),
    "CROWN":     ('<tg-emoji emoji-id="5357315181649076022">👑</tg-emoji>', "Home Header & Super Owner"),
    "STAR":      ('<tg-emoji emoji-id="5359785904535774578">⭐</tg-emoji>', "Highlights, Referrals & Promos"),
    "FIRE":      ('<tg-emoji emoji-id="5424972470023104089">🔥</tg-emoji>', "Live VC Worker Pool Status"),
    "BOLT":      ('<tg-emoji emoji-id="5431449001532594346">⚡</tg-emoji>', "VC Rates, Instant Launch & Refs"),
    "DIAMOND":   ('<tg-emoji emoji-id="5472164874886846699">💎</tg-emoji>', "Credit Price, VIP & Deposits"),
    "COIN":      ('<tg-emoji emoji-id="5373141891321699086">🪙</tg-emoji>', "Wallet Balance & Credits"),
    "CHECK":     ('<tg-emoji emoji-id="5206607081334906820">☑️</tg-emoji>', "Verified, Paid & Success Alerts"),
    "ROCKET":    ('<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji>', "VIP Progress Bar & VC Launch"),
    "USER":      ('<tg-emoji emoji-id="5373012449597335010">👤</tg-emoji>', "User Name & Account Info"),
    "PROFILE":   ('<tg-emoji emoji-id="5368324170671202286">👾</tg-emoji>', "My Profile Card Header"),
    "WOLF":      ('<tg-emoji emoji-id="5375296873982604963">🐺</tg-emoji>', "VC Orders Counter"),
    "ARROW":     ('<tg-emoji emoji-id="5416117059207572332">↪️</tg-emoji>', "Referral Invite Link"),
    "PLUS":      ('<tg-emoji emoji-id="5397782960512444700">➕</tg-emoji>', "Add ID / Credit / VC Join Logs"),
    "MINUS":     ('<tg-emoji emoji-id="5397916757333648639">➖</tg-emoji>', "Remove ID / Leave / Fine Logs"),
    "BELL":      ('<tg-emoji emoji-id="5458603043203327669">🔔</tg-emoji>', "Scheduled VC & Alerts"),
    "SHIELD":    ('<tg-emoji emoji-id="5370724846936267183">🛡️</tg-emoji>', "Security, F-Sub & Backup"),
    "ALERT":     ('<tg-emoji emoji-id="5447644880824181073">⚠️</tg-emoji>', "Warnings, Fines & Dead IDs"),
    "GIFT":      ('🎁', "Welcome Gift & Referral Bonus"),
    "TROPHY":    ('🏆', "VIP Unlock & Leaderboard"),
    "HISTORY":   ('📜', "Order & Payment History"),
    "GUIDE":     ('📘', "Help & Admin Guide"),
    "FINE":      ('⚖️', "Join-Request Fine Setup"),
    "MIC_ON":    ('🎙️', "VC Members Mic ON Status"),
    "MIC_OFF":   ('🔇', "VC Members Mic OFF (Muted) Status"),
    "DYNAMIC":   ('🔄', "Dynamic Joining (10s Cycle) Mode"),
    "TIMER":     ('⏳', "VC Duration & Live Timer"),
    "LINK":      ('🔗', "VC Target Group/Channel Link"),
    "ADMIN":     ('🛠️', "Admin Control Room Header"),
    "MONEY":     ('💵', "Starting Fee & INR Revenue"),
    "QR":        ('📲', "UPI QR Payment Card"),
    "PROMO":     ('🎟️', "Promo Code Redeem & Manager"),
}

ALL_ADMIN_POWERS: list[tuple[str, str]] = [
    ("vc_ids",      "📱 Manage VC IDs, Login & Live VCs"),
    ("fsub",        "📢 Manage Force-Subscribe"),
    ("credits",     "💰 Add / Cut User Credits"),
    ("pricing",     "💱 Rates, Start Fee, Gift & Promos"),
    ("referral",    "🎁 Referral & VIP Settings"),
    ("fine_setup",  "⚖️ VC Fine Setup"),
    ("emojis",      "🎨 Edit Bot Emojis"),
    ("broadcast",   "📣 Broadcast Messages"),
    ("logs_backup", "💾 Logs, Logo & Backup/Revive"),
    ("user_lookup", "🔍 User Lookup & Exports"),
]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
fg = FamGateway(api_key=FAMGATEWAY_API_KEY)

pending_logins: dict[int, dict] = {}
background_tasks: set[asyncio.Task] = set()
auto_backup_task: asyncio.Task | None = None
health_check_task: asyncio.Task | None = None


def track_task(coro) -> asyncio.Task:
    """Create and track a background asyncio task so it is never garbage-collected."""
    t = asyncio.create_task(coro)
    background_tasks.add(t)
    t.add_done_callback(background_tasks.discard)
    return t


# =====================================================================
# 3. SAFE CALLBACK ANSWER & SAFE MESSAGE EDITOR
# =====================================================================
async def safe_answer(callback: types.CallbackQuery, text: str = None, show_alert: bool = False):
    """Safely answer callback queries without crashing on expired query IDs."""
    try:
        if text is not None:
            return await callback.answer(text=text, show_alert=show_alert)
        return await callback.answer()
    except TelegramBadRequest as e:
        err = str(e).lower()
        if (
            "query is too old" in err
            or "query id is invalid" in err
            or "response timeout expired" in err
        ):
            return None
        raise e
    except Exception:
        return None


async def safe_edit(
    message: types.Message,
    text: str,
    reply_markup: InlineKeyboardMarkup = None,
):
    """Safely edit a text message, or delete-and-resend if it is a media message."""
    try:
        if message.photo or message.document or message.video or message.text is None:
            try:
                await message.delete()
            except Exception:
                pass
            return await message.answer(
                text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        return await message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as e:
        err_str = str(e).lower()
        if "message is not modified" in err_str:
            return message
        if (
            "there is no text in the message to edit" in err_str
            or "message can't be edited" in err_str
            or "message to edit not found" in err_str
        ):
            try:
                await message.delete()
            except Exception:
                pass
            return await message.answer(
                text,
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        raise e


# =====================================================================
# 4. SILENT AUDIO, BRAND LOGO & PERSISTENT SQLITE DATABASE
# =====================================================================
SILENT_WAV_PATH = os.path.abspath("silent_vc_stream.wav")
BRAND_LOGO_PATH = os.path.abspath("brand_logo.png")
DB_PATH = "vc_bot_production.db"


def ensure_silent_wav():
    """Generate a lightweight 30-second 48kHz mono silent WAV file for PyTgCalls."""
    if not os.path.exists(SILENT_WAV_PATH):
        with wave.open(SILENT_WAV_PATH, "w") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(48000)
            silence = struct.pack("<h", 0) * 48000 * 30
            f.writeframes(silence)


db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
db_conn.row_factory = sqlite3.Row
cursor = db_conn.cursor()


def init_db():
    """Initialize all SQLite tables, run safe migrations, and seed default settings."""
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        first_name TEXT DEFAULT '',
        credits REAL DEFAULT 0.0,
        total_paid_inr REAL DEFAULT 0.0,
        total_credits_spent REAL DEFAULT 0.0,
        total_vc_minutes INTEGER DEFAULT 0,
        verified INTEGER DEFAULT 0,
        ref_rewarded INTEGER DEFAULT 0,
        orders_count INTEGER DEFAULT 0,
        referrals INTEGER DEFAULT 0,
        referred_by INTEGER DEFAULT NULL,
        fines_paid REAL DEFAULT 0.0,
        joined_at TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );
    CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        powers TEXT DEFAULT '[]',
        added_by INTEGER,
        added_at TEXT
    );
    CREATE TABLE IF NOT EXISTS fsub_targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_type TEXT,
        visibility TEXT,
        title TEXT,
        chat_identifier TEXT,
        invite_url TEXT,
        added_at TEXT
    );
    CREATE TABLE IF NOT EXISTS promo_codes (
        code TEXT PRIMARY KEY,
        amount REAL,
        max_uses INTEGER,
        current_uses INTEGER DEFAULT 0,
        created_at TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS promo_claims (
        code TEXT,
        user_id INTEGER,
        claimed_at TEXT DEFAULT '',
        PRIMARY KEY (code, user_id)
    );
    CREATE TABLE IF NOT EXISTS payments (
        order_id TEXT PRIMARY KEY,
        user_id INTEGER,
        amount REAL,
        credits REAL,
        status TEXT DEFAULT 'pending',
        utr TEXT DEFAULT '',
        chat_id INTEGER DEFAULT 0,
        message_id INTEGER DEFAULT 0,
        created_at TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS vc_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT,
        session_string TEXT UNIQUE,
        label TEXT,
        added_at TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS vc_history (
        job_id TEXT PRIMARY KEY,
        user_id INTEGER,
        target_link TEXT,
        qty INTEGER,
        duration_mins INTEGER,
        cost_credits REAL,
        fine_deducted REAL DEFAULT 0.0,
        scheduled_ist TEXT,
        status TEXT
    );
    """)

    migrations = [
        "ALTER TABLE users ADD COLUMN first_name TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN total_paid_inr REAL DEFAULT 0.0",
        "ALTER TABLE users ADD COLUMN total_credits_spent REAL DEFAULT 0.0",
        "ALTER TABLE users ADD COLUMN total_vc_minutes INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN joined_at TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN ref_rewarded INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN fines_paid REAL DEFAULT 0.0",
        "ALTER TABLE payments ADD COLUMN created_at TEXT DEFAULT ''",
        "ALTER TABLE admins ADD COLUMN powers TEXT DEFAULT '[]'",
        "ALTER TABLE promo_codes ADD COLUMN created_at TEXT DEFAULT ''",
        "ALTER TABLE promo_claims ADD COLUMN claimed_at TEXT DEFAULT ''",
        "ALTER TABLE vc_sessions ADD COLUMN added_at TEXT DEFAULT ''",
        "ALTER TABLE vc_history ADD COLUMN fine_deducted REAL DEFAULT 0.0",
    ]
    for col_sql in migrations:
        try:
            cursor.execute(col_sql)
        except Exception:
            pass

    default_settings = {
        "super_owner_id": str(DEFAULT_SUPER_OWNER_ID),
        "owner_username": OWNER_USERNAME,
        "base_ids": "1",
        "base_time": "1",
        "base_credits": "5.0",
        "vc_start_fee": "10.0",
        "global_mic_muted": "1",
        "welcome_bonus": "50.0",
        "bonus_enabled": "1",
        "referral_enabled": "1",
        "ref_bonus": "25.0",
        "vip_ref_target": "10",
        "vc_request_fine": "10.0",
        "rupee_to_credit": "1.0",
        "log_channel_id": "0",
        "log_channel_title": "Not Configured",
        "auto_backup_mins": "0",
        "brand_logo_enabled": "1",
        "health_ping_mins": "10",
    }
    for ek, (eval_default, _) in DEFAULT_EMOJIS.items():
        default_settings[f"emoji_{ek}"] = eval_default

    for k, v in default_settings.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    all_p = json.dumps([p[0] for p in ALL_ADMIN_POWERS])
    cursor.execute(
        "INSERT OR IGNORE INTO admins (user_id, powers, added_by, added_at) VALUES (?, ?, ?, ?)",
        (DEFAULT_SUPER_OWNER_ID, all_p, DEFAULT_SUPER_OWNER_ID, full_date_ist()),
    )
    db_conn.commit()


init_db()
ensure_silent_wav()


def get_setting(key: str, as_type=str):
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    if row is None:
        return as_type(0) if as_type in (int, float) else ""
    try:
        return as_type(row["value"])
    except Exception:
        return as_type(0) if as_type in (int, float) else ""


def set_setting(key: str, value):
    cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    db_conn.commit()


def get_super_owner_id() -> int:
    try:
        val = get_setting("super_owner_id", str)
        return int(val) if val else DEFAULT_SUPER_OWNER_ID
    except Exception:
        return DEFAULT_SUPER_OWNER_ID


def get_owner_username() -> str:
    val = get_setting("owner_username", str)
    return val if val else OWNER_USERNAME


def pe(key: str) -> str:
    """Fetch customizable emoji (normal or <tg-emoji>) for a given bot location."""
    val = get_setting(f"emoji_{key}", str)
    if val:
        return val
    return DEFAULT_EMOJIS.get(key, ("✨", ""))[0]


def get_cost_per_id_min() -> float:
    b_ids = max(1, get_setting("base_ids", int))
    b_time = max(1, get_setting("base_time", int))
    b_cr = max(0.0, get_setting("base_credits", float))
    return b_cr / (b_ids * b_time)


def get_global_mic_muted() -> bool:
    return get_setting("global_mic_muted", int) == 1


def get_user(user_id: int) -> sqlite3.Row | None:
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone()


def ensure_user_exists(user: types.User, referred_by: int | None = None) -> sqlite3.Row:
    u = get_user(user.id)
    if not u:
        cursor.execute(
            """
            INSERT INTO users (user_id, username, first_name, credits, verified, referred_by, joined_at)
            VALUES (?, ?, ?, 0.0, 0, ?, ?)
            """,
            (
                user.id,
                user.username or "",
                user.first_name or "User",
                referred_by,
                full_date_ist(),
            ),
        )
        db_conn.commit()
        return get_user(user.id)
    cursor.execute(
        "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
        (user.username or "", user.first_name or "User", user.id),
    )
    db_conn.commit()
    return get_user(user.id)


async def resolve_target_user(raw_input: str) -> sqlite3.Row | None:
    """Resolve a user from numeric ID or @username via local DB or Telegram API."""
    clean_q = (raw_input or "").strip().lstrip("@")
    if not clean_q:
        return None
    if clean_q.isdigit():
        uid = int(clean_q)
        row = get_user(uid)
        if row:
            return row
        try:
            ch = await bot.get_chat(uid)
            cursor.execute(
                """
                INSERT OR IGNORE INTO users (user_id, username, first_name, credits, verified, joined_at)
                VALUES (?, ?, ?, 0.0, 1, ?)
                """,
                (ch.id, ch.username or "", ch.first_name or "User", full_date_ist()),
            )
            db_conn.commit()
            return get_user(ch.id)
        except Exception:
            return None

    cursor.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (clean_q,))
    row = cursor.fetchone()
    if row:
        return row
    try:
        ch = await bot.get_chat("@" + clean_q)
        cursor.execute(
            """
            INSERT OR IGNORE INTO users (user_id, username, first_name, credits, verified, joined_at)
            VALUES (?, ?, ?, 0.0, 1, ?)
            """,
            (ch.id, ch.username or clean_q, ch.first_name or "User", full_date_ist()),
        )
        db_conn.commit()
        return get_user(ch.id)
    except Exception:
        return None


def is_super_owner(user_id: int) -> bool:
    return user_id == get_super_owner_id()


def is_admin(user_id: int) -> bool:
    if is_super_owner(user_id):
        return True
    cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    return cursor.fetchone() is not None


def get_admin_powers(user_id: int) -> list[str]:
    if is_super_owner(user_id):
        return [p[0] for p in ALL_ADMIN_POWERS]
    cursor.execute("SELECT powers FROM admins WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        return []
    try:
        return json.loads(row["powers"] or "[]")
    except Exception:
        return []


def has_power(user_id: int, power_key: str) -> bool:
    if is_super_owner(user_id):
        return True
    plist = get_admin_powers(user_id)
    return power_key in plist or "all" in plist


def get_fsub_targets() -> list[sqlite3.Row]:
    cursor.execute("SELECT * FROM fsub_targets ORDER BY id ASC")
    return cursor.fetchall()


def build_vip_bar(refs: int, vip_target: int) -> tuple[str, int, bool]:
    bar, pct = make_progress_bar(refs, max(1, vip_target), length=10)
    return bar, pct, (refs >= max(1, vip_target))

# =====================================================================
# 5. LOG CHANNEL DISPATCHER (RICH FORMATTED LOGS)
# =====================================================================
async def send_bot_log(
    tag: str,
    emoji_prefix: str,
    user: types.User | dict | sqlite3.Row | None = None,
    fields: list[tuple[str, str]] | None = None,
    extra_id_tags: list[int] | None = None,
    photo_file: BufferedInputFile | None = None,
):
    """Send structured HTML logs to the configured Log Channel."""
    log_cid = get_setting("log_channel_id", int)
    if not log_cid or log_cid == 0:
        return

    lines = [
        "<b>VC Bot Logs !!</b>",
        f"{emoji_prefix} #{tag}",
    ]

    id_tags = []
    if user is not None:
        if isinstance(user, types.User):
            uid = user.id
            uname = esc_html(user.first_name or user.username or "User")
        else:
            uid = user["user_id"] if "user_id" in user.keys() else 0
            uname = esc_html(user["first_name"] or user["username"] or "User")
        lines.append(f"• <b>Of:</b> <a href='tg://user?id={uid}'>{uname}</a> [<code>{uid}</code>]")
        id_tags.append(f"#id{uid}")

    if fields:
        for k, v in fields:
            lines.append(f"• <b>{k}:</b> {v}")

    lines.append(f"• <b>Date:</b> {log_date_ist()} IST")

    if extra_id_tags:
        for eid in extra_id_tags:
            t_str = f"#id{str(eid).replace('-', '')}"
            if t_str not in id_tags:
                id_tags.append(t_str)

    if id_tags:
        lines.append(" ".join(id_tags))

    log_html = "\n".join(lines)

    try:
        if photo_file:
            await bot.send_photo(
                chat_id=log_cid,
                photo=photo_file,
                caption=log_html,
                parse_mode="HTML",
            )
        else:
            await bot.send_message(
                chat_id=log_cid,
                text=log_html,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
    except Exception as e:
        logger.warning(f"Failed to send log to {log_cid}: {e}")


# =====================================================================
# 6. CUSTOM PHOTO (PNG) + PDF INVOICE RECEIPT GENERATOR
# =====================================================================
def generate_custom_receipt_files(
    order_id: str,
    user_id: int,
    user_name: str,
    amount_inr: float,
    credits_added: float,
    utr: str,
    new_balance: float,
) -> tuple[bytes, bytes]:
    """Generate high-resolution PNG and PDF official payment receipts with QR & logo."""
    W, H = 900, 1180
    img = Image.new("RGB", (W, H), "#0b0f19")
    draw = ImageDraw.Draw(img)

    def load_f(size: int, bold: bool = False):
        font_candidates = [
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/system/fonts/Roboto-Bold.ttf" if bold else "/system/fonts/Roboto-Regular.ttf",
        ]
        for fp in font_candidates:
            if os.path.exists(fp):
                try:
                    return ImageFont.truetype(fp, size)
                except Exception:
                    pass
        return ImageFont.load_default()

    f_title = load_f(34, True)
    f_sub = load_f(19, False)
    f_sec = load_f(22, True)
    f_lbl = load_f(22, False)
    f_val = load_f(23, True)
    f_big = load_f(48, True)
    f_foot = load_f(17, False)

    draw.rounded_rectangle([35, 35, W - 35, H - 35], radius=28, fill="#131b2e", outline="#2563eb", width=3)
    draw.rounded_rectangle([38, 38, W - 38, 210], radius=26, fill="#1e3a8a")

    draw.text((75, 68), "FAMGATEWAY x PAID VC BOT", fill="#ffffff", font=f_title)
    draw.text((75, 118), "OFFICIAL VERIFIED UPI PAYMENT RECEIPT (PDF / PHOTO)", fill="#93c5fd", font=f_sub)
    draw.text((75, 152), "MSME Govt. of India Reg: UDYAM-BR-28-0050000", fill="#bfdbfe", font=f_foot)

    if get_setting("brand_logo_enabled", int) == 1 and os.path.exists(BRAND_LOGO_PATH):
        try:
            logo_raw = Image.open(BRAND_LOGO_PATH).convert("RGBA")
            logo_size = 126
            logo_raw = logo_raw.resize((logo_size, logo_size), Image.Resampling.LANCZOS)
            mask = Image.new("L", (logo_size, logo_size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, logo_size, logo_size), fill=255)
            logo_x, logo_y = W - 200, 60
            draw.ellipse(
                (logo_x - 4, logo_y - 4, logo_x + logo_size + 4, logo_y + logo_size + 4),
                fill="#38bdf8",
            )
            img.paste(logo_raw, (logo_x, logo_y), mask)
        except Exception:
            pass

    draw.rounded_rectangle([75, 240, W - 75, 395], radius=20, fill="#064e3b", outline="#10b981", width=2)
    draw.text((105, 260), "PAYMENT STATUS: VERIFIED & CREDITED", fill="#34d399", font=f_sec)
    draw.text((105, 305), f"INR {fmt_num(amount_inr)}.00 /-", fill="#ffffff", font=f_big)
    draw.text((W - 340, 318), f"+{fmt_num(credits_added)} Credits", fill="#6ee7b7", font=f_title)

    qr = qrcode.QRCode(box_size=4, border=2)
    qr.add_data(f"https://famgateway.in/transaction-details.php?id={order_id}&utr={utr}")
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="#0b0f19", back_color="#ffffff").convert("RGB").resize((135, 135))

    rows = [
        ("Receipt / Order ID", str(order_id)),
        ("Bank UPI UTR No.", str(utr or "VERIFIED_INSTANT")),
        ("Date & Time (IST)", full_date_ist()),
        ("Customer Name", str(user_name)[:26]),
        ("Customer TG ID", str(user_id)),
        ("Amount Paid", f"Rs. {fmt_num(amount_inr)}/- (Locked QR)"),
        ("Credits Purchased", f"+{fmt_num(credits_added)} Cr"),
        ("Updated Balance", f"{fmt_num(new_balance)} Cr"),
        ("Payment Gateway", "FamGateway Zero-Fee P2P UPI"),
        ("Authorized Owner", f"@{get_owner_username()} ({get_super_owner_id()})"),
    ]

    y = 430
    for label, val in rows:
        draw.text((80, y), label, fill="#94a3b8", font=f_lbl)
        draw.text((390, y), f":  {val}", fill="#f8fafc", font=f_val)
        y += 50
        draw.line([(80, y - 10), (W - 80, y - 10)], fill="#1e293b", width=1)

    img.paste(qr_img, (W - 225, H - 210))
    draw.text((80, H - 195), "Cryptographically Verified via HMAC-SHA256", fill="#38bdf8", font=f_sec)
    draw.text((80, H - 160), "Non-Custodial Direct UPI Settlement • 0% Fee", fill="#64748b", font=f_foot)
    draw.text((80, H - 130), f"Timestamp: {log_date_ist()} IST • Support: @{get_owner_username()}", fill="#64748b", font=f_foot)

    png_bio = io.BytesIO()
    pdf_bio = io.BytesIO()
    img.save(png_bio, format="PNG")
    img.save(pdf_bio, format="PDF", resolution=150.0)
    return png_bio.getvalue(), pdf_bio.getvalue()


# =====================================================================
# 7. FULL MEMORY BACKUP (.JSON), REVIVE & AUTO-BACKUP ENGINE
# =====================================================================
BACKUP_TABLES = [
    "users",
    "settings",
    "admins",
    "fsub_targets",
    "promo_codes",
    "promo_claims",
    "payments",
    "vc_sessions",
    "vc_history",
]


def export_full_memory_json() -> tuple[bytes, dict]:
    """Export every SQLite table plus base64-encoded Brand Logo into a single JSON file."""
    logo_b64 = None
    if os.path.exists(BRAND_LOGO_PATH):
        try:
            with open(BRAND_LOGO_PATH, "rb") as lf:
                logo_b64 = base64.b64encode(lf.read()).decode("utf-8")
        except Exception:
            pass

    dump = {
        "meta": {
            "bot": "Paid VC Bot Enterprise",
            "version": "v4.0-DynamicMultiVC",
            "owner_id": get_super_owner_id(),
            "exported_at_ist": full_date_ist(),
        },
        "brand_logo_b64": logo_b64,
        "tables": {},
    }

    stats = {}
    for t in BACKUP_TABLES:
        cursor.execute(f"SELECT * FROM {t}")
        rows = [dict(r) for r in cursor.fetchall()]
        dump["tables"][t] = rows
        stats[t] = len(rows)

    raw_bytes = json.dumps(dump, indent=2, ensure_ascii=False).encode("utf-8")
    return raw_bytes, stats


async def restore_full_memory_json(raw_bytes: bytes) -> dict:
    """Restore entire bot database, settings, admins, brand logo & reload VC workers."""
    payload = json.loads(raw_bytes.decode("utf-8"))
    tables_data = payload.get("tables", payload)

    if payload.get("brand_logo_b64"):
        try:
            with open(BRAND_LOGO_PATH, "wb") as lf:
                lf.write(base64.b64decode(payload["brand_logo_b64"]))
        except Exception:
            pass

    restored_counts = {}
    for table_name in BACKUP_TABLES:
        rows = tables_data.get(table_name)
        if not isinstance(rows, list):
            continue
        cursor.execute(f"DELETE FROM {table_name}")
        for r in rows:
            if not isinstance(r, dict) or not r:
                continue
            cols = ", ".join(r.keys())
            placeholders = ", ".join(["?"] * len(r))
            try:
                cursor.execute(
                    f"INSERT OR REPLACE INTO {table_name} ({cols}) VALUES ({placeholders})",
                    tuple(r.values()),
                )
            except Exception as e:
                logger.warning(f"Skipping incompatible row in {table_name}: {e}")
        restored_counts[table_name] = len(rows)

    db_conn.commit()

    for w in list(vc_engine.workers):
        try:
            if w.get("client"):
                await w["client"].stop()
        except Exception:
            pass
    vc_engine.workers.clear()
    await vc_engine.load_all_workers()
    return restored_counts


async def send_memory_backup_to_log_channel(reason: str = "SCHEDULED_AUTO_BACKUP") -> bool:
    """Push a full JSON backup document to the Log Channel."""
    log_cid = get_setting("log_channel_id", int)
    if not log_cid or log_cid == 0:
        return False
    try:
        raw_bytes, stats = export_full_memory_json()
        fname = f"VCBot_Memory_{now_ist().strftime('%Y%m%d_%H%M%S')}_IST.json"
        caption = (
            f"<b>VC Bot Logs !!</b>\n"
            f"{pe('SHIELD')} #{reason}\n"
            f"• <b>Users:</b> <code>{stats.get('users', 0)}</code> | "
            f"<b>VC IDs:</b> <code>{stats.get('vc_sessions', 0)}</code> | "
            f"<b>Payments:</b> <code>{stats.get('payments', 0)}</code>\n"
            f"• <b>Date:</b> {log_date_ist()} IST\n"
            f"#id{get_super_owner_id()}"
        )
        await bot.send_document(
            chat_id=log_cid,
            document=BufferedInputFile(raw_bytes, filename=fname),
            caption=caption,
            parse_mode="HTML",
        )
        return True
    except Exception as e:
        logger.warning(f"Backup dispatch failed: {e}")
        return False


async def auto_backup_loop():
    """Background task that sends automatic JSON memory backups at configured intervals."""
    while True:
        try:
            mins = get_setting("auto_backup_mins", int)
            if mins <= 0:
                await asyncio.sleep(20)
                continue
            await asyncio.sleep(mins * 60)
            if get_setting("auto_backup_mins", int) > 0:
                await send_memory_backup_to_log_channel("AUTO_MEMORY_BACKUP")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Auto backup loop error: {e}")
            await asyncio.sleep(30)


# =====================================================================
# 8. FAST MULTI-VC ENGINE (MIC CONTROL, PROGRESS BAR, CANCEL, DYNAMIC 10s,
#    LIVE +TIME/+ID/-ID & PER-MINUTE USAGE BILLING + STARTING FEE)
# =====================================================================
class VCEngineManager:
    """
    Production Multi-VC Engine with:
    - Fast parallel joining (Semaphore batching) + live progress bar + mid-join cancel
    - Mic ON/OFF control for all members in a VC
    - Dynamic Joining Mode (10s random leave/join simulation like real members)
    - Live +Time, +ID, -ID controls
    - Fixed Starting Fee + Per-Minute actual-ID usage billing
    """

    def __init__(self):
        self.workers: list[dict] = []
        self.active_jobs: dict[str, dict] = {}
        self.scheduled_jobs: dict[str, dict] = {}
        self.joining_sessions: dict[str, dict] = {}
        self.job_counter: int = 1000

    async def load_all_workers(self):
        cursor.execute("SELECT * FROM vc_sessions ORDER BY id ASC")
        for r in cursor.fetchall():
            await self.start_single_worker(
                r["id"], r["session_string"], r["label"], r["phone"]
            )

    async def start_single_worker(
        self, db_id: int, session_str: str, label: str, phone: str = ""
    ) -> bool:
        if not VC_ENGINE_AVAILABLE:
            return False
        if any(w["id"] == db_id for w in self.workers):
            return True
        try:
            app = Client(
                f"vc_worker_{db_id}_{int(time.time())}",
                api_id=TG_API_ID,
                api_hash=TG_API_HASH,
                session_string=session_str,
                in_memory=True,
            )
            await app.start()
            call = PyTgCalls(app)
            await call.start()
            self.workers.append({
                "id": db_id,
                "phone": phone,
                "client": app,
                "call": call,
                "active_chats": set(),
                "label": label,
            })
            logger.info(f"REAL VC Worker Ready (Multi-VC Enabled): {label} (ID #{db_id})")
            return True
        except Exception as e:
            logger.error(f"Failed VC Worker #{db_id} ({label}): {e}")
            await send_bot_log(
                "SESSION_DEAD",
                pe("ALERT"),
                fields=[
                    ("Account", f"<code>{esc_html(label)}</code>"),
                    ("Error", f"<code>{esc_html(str(e)[:120])}</code>"),
                ],
            )
            return False

    async def ping_and_verify_all_workers(self) -> tuple[int, list[dict]]:
        alive = 0
        dead = []
        for w in list(self.workers):
            try:
                await w["client"].get_me()
                alive += 1
            except Exception as e:
                dead.append({"id": w["id"], "label": w["label"], "error": str(e)})
                try:
                    await w["client"].stop()
                except Exception:
                    pass
                if w in self.workers:
                    self.workers.remove(w)
                await send_bot_log(
                    "SESSION_DEAD_REMOVED",
                    pe("ALERT"),
                    fields=[
                        ("Account", f"<code>{esc_html(w['label'])}</code>"),
                        ("Reason", f"<code>{esc_html(str(e)[:100])}</code>"),
                    ],
                )
        return alive, dead

    async def remove_worker(self, db_id: int):
        cursor.execute("DELETE FROM vc_sessions WHERE id = ?", (db_id,))
        db_conn.commit()
        for w in list(self.workers):
            if w["id"] == db_id:
                for cid in list(w["active_chats"]):
                    try:
                        await self._leave_worker_vc_call(w.get("call"), cid)
                    except Exception:
                        pass
                w["active_chats"].clear()
                try:
                    await w["client"].stop()
                except Exception:
                    pass
                self.workers.remove(w)

    def available_count(self) -> int:
        return len(self.workers)

    def total_active_streams(self) -> int:
        return sum(len(w["active_chats"]) for w in self.workers)

    def eligible_workers_for_chat(self, chat_id: int) -> list[dict]:
        """Return online workers not currently inside this exact chat_id VC."""
        eligible = [w for w in self.workers if chat_id not in w["active_chats"]]
        return sorted(eligible, key=lambda x: len(x["active_chats"]))

    async def _resolve_and_join_chat(self, client: Client, invite_link: str) -> tuple[int, str]:
        target = invite_link.strip()
        if "t.me/" in target and not ("t.me/+" in target or "joinchat/" in target):
            slug = target.split("t.me/")[-1].split("/")[0].split("?")[0].strip()
            if slug and not slug.startswith("+"):
                target = slug
        if target.startswith("@"):
            target = target[1:]

        try:
            chat = await client.join_chat(target)
            return chat.id, (chat.title or invite_link)
        except UserAlreadyParticipant:
            chat = await client.get_chat(target)
            return chat.id, (chat.title or invite_link)
        except Exception as e:
            err_up = str(e).upper()
            if "INVITE_REQUEST_SENT" in err_up or "REQUEST" in err_up or "APPROVE" in err_up:
                raise e
            chat = await client.get_chat(target)
            return chat.id, (chat.title or invite_link)

    async def _apply_mic_state(self, call, chat_id: int, muted: bool):
        """Mute or unmute a worker's mic in a specific VC across PyTgCalls versions."""
        if call is None or not isinstance(chat_id, int):
            return
        method_candidates = (
            ("mute_stream", "mute") if muted else ("unmute_stream", "unmute")
        )
        for m_name in method_candidates:
            if hasattr(call, m_name):
                try:
                    await getattr(call, m_name)(chat_id)
                    return
                except Exception:
                    pass

    async def _join_single_worker_to_vc(
        self, w: dict, invite_link: str, mic_muted: bool = True
    ) -> tuple[bool, int | None, str, str]:
        """Join a single worker into target VC and apply mic state."""
        chat_id = None
        try:
            chat_id, chat_title = await self._resolve_and_join_chat(w["client"], invite_link)
            if chat_id in w["active_chats"]:
                return False, chat_id, chat_title, "ID already live in this same VC"

            w["active_chats"].add(chat_id)
            if HAS_MEDIA_STREAM:
                stream = MediaStream(SILENT_WAV_PATH)
                if hasattr(w["call"], "play"):
                    await w["call"].play(chat_id, stream)
                else:
                    await w["call"].join_group_call(chat_id, stream)
            else:
                await w["call"].join_group_call(chat_id, AudioPiped(SILENT_WAV_PATH))

            # Apply mic state immediately after joining
            await self._apply_mic_state(w["call"], chat_id, mic_muted)
            return True, chat_id, chat_title, ""
        except Exception as e:
            if chat_id is not None:
                w["active_chats"].discard(chat_id)
            return False, None, invite_link, str(e)

    async def _leave_worker_vc_call(self, call, chat_id: int):
        if call is None or not isinstance(chat_id, int):
            return
        for method_name in ("leave_call", "leave_group_call"):
            if hasattr(call, method_name):
                try:
                    await getattr(call, method_name)(chat_id)
                    return
                except Exception:
                    pass

    async def set_job_mic_state(self, job_id: str, muted: bool) -> bool:
        """Toggle mic ON/OFF for every worker currently active in job_id."""
        job = self.active_jobs.get(job_id)
        if not job:
            return False
        job["mic_muted"] = muted
        tasks = []
        for w in list(job["workers"]):
            cid = job["chat_ids"].get(w["id"])
            if cid is not None:
                tasks.append(self._apply_mic_state(w.get("call"), cid, muted))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return True

    async def set_all_jobs_mic_state(self, muted: bool):
        """Apply global admin mic ON/OFF setting to all currently live VC jobs."""
        for jid in list(self.active_jobs.keys()):
            await self.set_job_mic_state(jid, muted)

    async def add_member_to_job(self, job_id: str) -> tuple[bool, str]:
        """Add +1 available worker ID live into a running VC job."""
        job = self.active_jobs.get(job_id)
        if not job:
            return False, "VC order active nahi hai."
        cid = job["chat_id_num"]
        eligible = self.eligible_workers_for_chat(cid)
        if not eligible:
            return False, "Koi aur extra VC ID available nahi hai!"

        for w in eligible:
            ok, joined_cid, _, err = await self._join_single_worker_to_vc(
                w, job["link"], mic_muted=job.get("mic_muted", True)
            )
            if ok and joined_cid is not None:
                job["workers"].append(w)
                job["chat_ids"][w["id"]] = joined_cid
                job["qty"] = len(job["workers"])
                cursor.execute(
                    "UPDATE vc_history SET qty = ? WHERE job_id = ?",
                    (job["qty"], job_id),
                )
                db_conn.commit()
                return True, f"Added {w['label']} (Now {job['qty']} IDs live)"
        return False, "Extra ID join karne mein error aaya."

    async def remove_member_from_job(self, job_id: str) -> tuple[bool, str]:
        """Remove -1 worker ID live from a running VC job."""
        job = self.active_jobs.get(job_id)
        if not job:
            return False, "VC order active nahi hai."
        if len(job["workers"]) <= 1:
            return False, "Kam se kam 1 ID honi chahiye (poora band karne ke liye Stop dabayein)."

        w = job["workers"].pop()
        cid = job["chat_ids"].pop(w["id"], job["chat_id_num"])
        try:
            await self._leave_worker_vc_call(w.get("call"), cid)
        finally:
            if cid is not None:
                w["active_chats"].discard(cid)

        job["qty"] = len(job["workers"])
        cursor.execute("UPDATE vc_history SET qty = ? WHERE job_id = ?", (job["qty"], job_id))
        db_conn.commit()
        return True, f"Removed {w['label']} (Now {job['qty']} IDs live)"

    async def extend_job_time(self, job_id: str, extra_mins: int) -> tuple[bool, str]:
        """Extend a live VC job's timer if user has sufficient credits."""
        job = self.active_jobs.get(job_id)
        if not job:
            return False, "VC order active nahi hai."

        uid = job["user_id"]
        if not is_admin(uid):
            rem_mins = max(1, int((job["end_time"] - time.time()) / 60) + 1)
            needed_total = round((rem_mins + extra_mins) * len(job["workers"]) * get_cost_per_id_min(), 2)
            u = get_user(uid)
            bal = u["credits"] if u else 0.0
            if bal < needed_total:
                return False, f"Insufficient Balance! Extra {extra_mins}m ke liye kam se kam {fmt_num(needed_total)} Cr chahiye (Aapke paas {fmt_num(bal)} Cr hain)."

        job["end_time"] += extra_mins * 60
        job["duration_mins"] += extra_mins
        cursor.execute(
            "UPDATE vc_history SET duration_mins = ? WHERE job_id = ?",
            (job["duration_mins"], job_id),
        )
        db_conn.commit()
        return True, f"+{extra_mins} Mins added! (Usage billing har minute ke hisab se kategi)"

    def toggle_dynamic_mode(self, job_id: str) -> tuple[bool, bool]:
        """Toggle 10-second real-member dynamic leave/join mode on a running job."""
        job = self.active_jobs.get(job_id)
        if not job:
            return False, False
        new_state = not job.get("dynamic_mode", False)
        job["dynamic_mode"] = new_state
        if new_state:
            if not job.get("dyn_task") or job["dyn_task"].done():
                job["dyn_task"] = track_task(self._dynamic_join_loop(job_id))
        else:
            if job.get("dyn_task") and not job["dyn_task"].done():
                job["dyn_task"].cancel()
        return True, new_state

    async def _dynamic_join_loop(self, job_id: str):
        """Every 10 seconds, 1 random member leaves & a random member joins (real members effect)."""
        try:
            while job_id in self.active_jobs:
                await asyncio.sleep(10)
                job = self.active_jobs.get(job_id)
                if not job or not job.get("dynamic_mode", False):
                    return
                if not job["workers"]:
                    continue

                cid = job["chat_id_num"]
                leaving_w = random.choice(job["workers"])
                try:
                    await self._leave_worker_vc_call(leaving_w.get("call"), cid)
                    leaving_w["active_chats"].discard(cid)
                    if leaving_w in job["workers"]:
                        job["workers"].remove(leaving_w)
                    job["chat_ids"].pop(leaving_w["id"], None)
                except Exception:
                    continue

                await asyncio.sleep(1.5)

                job = self.active_jobs.get(job_id)
                if not job or not job.get("dynamic_mode", False):
                    return

                # Pick another random available worker, or re-join the same worker if none else free
                eligible = self.eligible_workers_for_chat(cid)
                other_eligible = [w for w in eligible if w["id"] != leaving_w["id"]]
                joining_w = random.choice(other_eligible) if other_eligible else leaving_w

                ok, joined_cid, _, _ = await self._join_single_worker_to_vc(
                    joining_w, job["link"], mic_muted=job.get("mic_muted", True)
                )
                if ok and joined_cid is not None:
                    job["workers"].append(joining_w)
                    job["chat_ids"][joining_w["id"]] = joined_cid
                else:
                    # Fallback: try re-joining leaving_w
                    ok2, jcid2, _, _ = await self._join_single_worker_to_vc(
                        leaving_w, job["link"], mic_muted=job.get("mic_muted", True)
                    )
                    if ok2 and jcid2 is not None:
                        job["workers"].append(leaving_w)
                        job["chat_ids"][leaving_w["id"]] = jcid2

                job["qty"] = len(job["workers"])
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Dynamic join loop error for {job_id}: {e}")

    async def dispatch_vc_order(
        self,
        user_id: int,
        invite_link: str,
        requested_qty: int,
        duration_mins: int,
        mic_muted: bool = True,
        dynamic_mode: bool = False,
        progress_msg: types.Message | None = None,
        join_token: str | None = None,
    ) -> tuple[str | None, int, str, bool]:
        """
        Fast parallel dispatch with Semaphore(4), live progress bar, mid-join cancel,
        fixed starting fee, and per-minute usage billing.
        Returns: (job_id, actual_joined, error_reason, was_cancelled)
        """
        candidate_workers = sorted(self.workers, key=lambda x: len(x["active_chats"]))
        selected_pool = candidate_workers[: max(requested_qty, len(candidate_workers))]

        token = join_token or f"JT_{int(time.time() * 1000)}_{user_id}"
        session = {
            "token": token,
            "user_id": user_id,
            "cancelled": False,
            "attempted": 0,
            "joined": 0,
            "total": requested_qty,
        }
        self.joining_sessions[token] = session

        joined_workers: list[dict] = []
        worker_chat_ids: dict[int, int] = {}
        last_error = ""
        resolved_title = invite_link
        resolved_cid = 0
        lock = asyncio.Lock()
        sem = asyncio.Semaphore(4)
        last_ui_update = 0.0

        async def update_progress_ui(force: bool = False):
            nonlocal last_ui_update
            if progress_msg is None:
                return
            now_t = time.time()
            if not force and (now_t - last_ui_update) < 1.1:
                return
            last_ui_update = now_t
            bar, pct = make_progress_bar(len(joined_workers), requested_qty, length=10)
            mic_txt = f"{pe('MIC_OFF')} Muted" if mic_muted else f"{pe('MIC_ON')} Unmuted"
            dyn_txt = f"{pe('DYNAMIC')} ON (10s)" if dynamic_mode else "OFF"
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Cancel Joining", callback_data=f"canceljoin_{token}", style="danger")]
                ]
            )
            try:
                await safe_edit(
                    progress_msg,
                    f"<blockquote>{pe('BOLT')} <b>FAST JOINING VC IN PROGRESS...</b>\n\n"
                    f"{pe('LINK')} <b>Target:</b> <code>{esc_html(invite_link)}</code>\n"
                    f"🤖 <b>Progress:</b> <code>{len(joined_workers)}/{requested_qty} IDs Joined</code>\n"
                    f"<code>{bar} {pct}%</code>\n\n"
                    f"🎙️ <b>Mic:</b> {mic_txt} | 🔄 <b>Dynamic:</b> {dyn_txt}\n"
                    f"<i>Joining fast in parallel batches... Aap beech mein bhi cancel kar sakte hain.</i></blockquote>",
                    reply_markup=kb,
                )
            except Exception:
                pass

        await update_progress_ui(force=True)

        async def worker_task(w: dict):
            nonlocal last_error, resolved_title, resolved_cid
            async with sem:
                if session["cancelled"]:
                    return
                async with lock:
                    if len(joined_workers) >= requested_qty:
                        return
                ok, cid, ctitle, err = await self._join_single_worker_to_vc(
                    w, invite_link, mic_muted=mic_muted
                )
                async with lock:
                    session["attempted"] += 1
                    if session["cancelled"]:
                        if ok and cid is not None:
                            await self._leave_worker_vc_call(w.get("call"), cid)
                            w["active_chats"].discard(cid)
                        return
                    if ok and cid is not None:
                        if len(joined_workers) < requested_qty:
                            joined_workers.append(w)
                            worker_chat_ids[w["id"]] = cid
                            resolved_title = ctitle
                            resolved_cid = cid
                            session["joined"] = len(joined_workers)
                        else:
                            await self._leave_worker_vc_call(w.get("call"), cid)
                            w["active_chats"].discard(cid)
                    else:
                        last_error = err
                await update_progress_ui(force=False)

        # Launch in fast waves until requested_qty reached or pool exhausted
        wave_size = 6
        idx = 0
        while idx < len(selected_pool) and len(joined_workers) < requested_qty and not session["cancelled"]:
            needed = requested_qty - len(joined_workers)
            batch = selected_pool[idx : idx + max(needed, wave_size)]
            idx += len(batch)
            await asyncio.gather(*(worker_task(w) for w in batch), return_exceptions=True)

        self.joining_sessions.pop(token, None)

        # If user cancelled mid-join, cleanly leave all already-joined IDs
        if session["cancelled"]:
            for w in joined_workers:
                cid = worker_chat_ids.get(w["id"])
                try:
                    await self._leave_worker_vc_call(w.get("call"), cid)
                finally:
                    if cid is not None:
                        w["active_chats"].discard(cid)
            return None, 0, "Cancelled by user during joining", True

        if not joined_workers:
            return None, 0, last_error or "No eligible IDs could join the VC", False

        # Deduct Fixed Starting Fee once order goes live (Admins free)
        start_fee = 0.0 if is_admin(user_id) else max(0.0, get_setting("vc_start_fee", float))
        if start_fee > 0:
            cursor.execute(
                "UPDATE users SET credits = MAX(0, credits - ?), total_credits_spent = total_credits_spent + ? WHERE user_id = ?",
                (start_fee, start_fee, user_id),
            )

        self.job_counter += 1
        job_id = f"VC_{self.job_counter}"
        total_seconds = max(10, int(duration_mins * 60))
        end_time = time.time() + total_seconds

        cursor.execute(
            """
            INSERT OR REPLACE INTO vc_history
            (job_id, user_id, target_link, qty, duration_mins, cost_credits, fine_deducted, scheduled_ist, status)
            VALUES (?, ?, ?, ?, ?, ?, 0.0, ?, 'ACTIVE')
            """,
            (
                job_id,
                user_id,
                f"{resolved_title} ({invite_link})",
                len(joined_workers),
                duration_mins,
                start_fee,
                full_date_ist(),
            ),
        )
        cursor.execute(
            "UPDATE users SET orders_count = orders_count + 1 WHERE user_id = ?",
            (user_id,),
        )
        db_conn.commit()

        job_obj = {
            "job_id": job_id,
            "user_id": user_id,
            "link": invite_link,
            "title": resolved_title,
            "chat_id_num": resolved_cid,
            "qty": len(joined_workers),
            "duration_mins": duration_mins,
            "start_fee": start_fee,
            "cost": start_fee,
            "minutes_billed": 0,
            "mic_muted": mic_muted,
            "dynamic_mode": dynamic_mode,
            "end_time": end_time,
            "workers": joined_workers,
            "chat_ids": worker_chat_ids,
            "task": None,
            "dyn_task": None,
        }
        self.active_jobs[job_id] = job_obj
        job_obj["task"] = track_task(self._per_minute_billing_and_timer(job_id))
        if dynamic_mode:
            job_obj["dyn_task"] = track_task(self._dynamic_join_loop(job_id))

        await send_bot_log(
            "VC_ORDER_LIVE",
            pe("PLUS"),
            user=get_user(user_id),
            fields=[
                ("Job ID", f"<code>{job_id}</code>"),
                ("Target", f"<code>{esc_html(resolved_title)}</code>"),
                ("Accounts", f"<code>{len(joined_workers)}/{requested_qty} IDs</code>"),
                ("Duration", f"<code>{duration_mins} Mins</code>"),
                ("Start Fee", f"<code>{fmt_num(start_fee)} Cr</code>"),
                ("Mic", "Muted" if mic_muted else "Unmuted"),
                ("Dynamic", "ON" if dynamic_mode else "OFF"),
            ],
            extra_id_tags=[resolved_cid] if resolved_cid else None,
        )
        return job_id, len(joined_workers), "", False

    async def _per_minute_billing_and_timer(self, job_id: str):
        """
        Every 60 seconds:
        - Calculates exact credits for CURRENT live worker count in that minute
        - Deducts from user wallet (Admins free)
        - Auto-stops VC if balance runs out OR when end_time is reached
        """
        try:
            while job_id in self.active_jobs:
                job = self.active_jobs.get(job_id)
                if not job:
                    return
                remaining = job["end_time"] - time.time()
                if remaining <= 0:
                    await self.stop_vc_job(job_id, notify_user=True, auto_expired=True)
                    return

                sleep_chunk = min(60.0, remaining)
                await asyncio.sleep(sleep_chunk)

                job = self.active_jobs.get(job_id)
                if not job:
                    return

                # Bill proportional to fraction of minute elapsed (1.0 for full 60s)
                frac = sleep_chunk / 60.0
                uid = job["user_id"]
                live_ids = len(job["workers"])
                if not is_admin(uid) and live_ids > 0:
                    chunk_cost = round(live_ids * get_cost_per_id_min() * frac, 2)
                    u = get_user(uid)
                    bal = u["credits"] if u else 0.0
                    if bal < chunk_cost:
                        deducted = round(bal, 2)
                        cursor.execute(
                            "UPDATE users SET credits = 0, total_credits_spent = total_credits_spent + ?, total_vc_minutes = total_vc_minutes + 1 WHERE user_id = ?",
                            (deducted, uid),
                        )
                        job["cost"] = round(job["cost"] + deducted, 2)
                        job["minutes_billed"] += 1
                        cursor.execute(
                            "UPDATE vc_history SET cost_credits = ? WHERE job_id = ?",
                            (job["cost"], job_id),
                        )
                        db_conn.commit()
                        try:
                            await bot.send_message(
                                uid,
                                f"<blockquote>{pe('ALERT')} <b>BALANCE EXHAUSTED — VC AUTO-STOPPED!</b>\n\n"
                                f"🆔 <b>Job ID:</b> <code>{job_id}</code>\n"
                                f"⏱️ <b>Minutes Ran:</b> <code>{job['minutes_billed']}m</code>\n"
                                f"💸 <b>Total Billed:</b> <code>{fmt_num(job['cost'])} Cr</code>\n"
                                f"<i>Credits deposit karke dobara start karein.</i></blockquote>",
                                reply_markup=make_nav_kb("deposit_menu", "💳 Deposit Credits"),
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass
                        await self.stop_vc_job(job_id, notify_user=False, auto_expired=True)
                        return

                    cursor.execute(
                        "UPDATE users SET credits = credits - ?, total_credits_spent = total_credits_spent + ?, total_vc_minutes = total_vc_minutes + 1 WHERE user_id = ?",
                        (chunk_cost, chunk_cost, uid),
                    )
                    job["cost"] = round(job["cost"] + chunk_cost, 2)
                    job["minutes_billed"] += 1
                    cursor.execute(
                        "UPDATE vc_history SET cost_credits = ? WHERE job_id = ?",
                        (job["cost"], job_id),
                    )
                    db_conn.commit()
                else:
                    job["minutes_billed"] += 1

                if time.time() >= job["end_time"]:
                    await self.stop_vc_job(job_id, notify_user=True, auto_expired=True)
                    return
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Billing timer error for {job_id}: {e}")

    def schedule_future_vc_order(
        self,
        user_id: int,
        invite_link: str,
        req_qty: int,
        duration_mins: int,
        mic_muted: bool,
        dynamic_mode: bool,
        delay_secs: float,
        target_ist_str: str,
    ) -> str:
        self.job_counter += 1
        sched_id = f"SCHED_{self.job_counter}"
        cursor.execute(
            """
            INSERT OR REPLACE INTO vc_history
            (job_id, user_id, target_link, qty, duration_mins, cost_credits, fine_deducted, scheduled_ist, status)
            VALUES (?, ?, ?, ?, ?, 0.0, 0.0, ?, 'SCHEDULED')
            """,
            (sched_id, user_id, invite_link, req_qty, duration_mins, target_ist_str),
        )
        db_conn.commit()

        task = track_task(
            self._run_scheduled_job(
                sched_id, user_id, invite_link, req_qty, duration_mins, mic_muted, dynamic_mode, delay_secs, target_ist_str
            )
        )
        self.scheduled_jobs[sched_id] = {
            "job_id": sched_id,
            "user_id": user_id,
            "link": invite_link,
            "qty": req_qty,
            "duration_mins": duration_mins,
            "mic_muted": mic_muted,
            "dynamic_mode": dynamic_mode,
            "run_at_ist": target_ist_str,
            "task": task,
        }
        return sched_id

    async def _run_scheduled_job(
        self, sched_id, user_id, link, req_qty, duration_mins, mic_muted, dynamic_mode, delay_secs, target_ist_str
    ):
        try:
            await asyncio.sleep(delay_secs)
            self.scheduled_jobs.pop(sched_id, None)

            if not is_admin(user_id):
                start_fee = max(0.0, get_setting("vc_start_fee", float))
                min_req = round(start_fee + (req_qty * get_cost_per_id_min()), 2)
                u = get_user(user_id)
                if not u or u["credits"] < min_req:
                    cursor.execute("UPDATE vc_history SET status = 'FAILED_LOW_BAL' WHERE job_id = ?", (sched_id,))
                    db_conn.commit()
                    await bot.send_message(
                        user_id,
                        f"<blockquote>{pe('ALERT')} Scheduled VC <code>{sched_id}</code> cancel ho gaya — balance kam hai (min <code>{fmt_num(min_req)} Cr</code> required).</blockquote>",
                        parse_mode="HTML",
                    )
                    return

            job_id, actual_joined, err_reason, _ = await self.dispatch_vc_order(
                user_id, link, req_qty, duration_mins, mic_muted=mic_muted, dynamic_mode=dynamic_mode
            )
            if actual_joined == 0:
                fine_amt = get_setting("vc_request_fine", float) if not is_admin(user_id) else 0.0
                is_req_fail = any(
                    k in (err_reason or "").lower() for k in ["request", "approve", "invite_request"]
                )
                if is_req_fail and fine_amt > 0:
                    cursor.execute(
                        "UPDATE users SET credits = MAX(0, credits - ?), fines_paid = fines_paid + ? WHERE user_id = ?",
                        (fine_amt, fine_amt, user_id),
                    )
                    cursor.execute(
                        "UPDATE vc_history SET status = 'FAILED_FINED', fine_deducted = ? WHERE job_id = ?",
                        (fine_amt, sched_id),
                    )
                    db_conn.commit()
                    await bot.send_message(
                        user_id,
                        f"<blockquote>{pe('ALERT')} <b>SCHEDULED VC FAILED (JOIN-REQUEST FINE)!</b>\n\n"
                        f"🆔 <code>{sched_id}</code>\n"
                        f"⚖️ <b>Fine Deducted:</b> <code>-{fmt_num(fine_amt)} Cr</code></blockquote>",
                        parse_mode="HTML",
                    )
                    return

                cursor.execute("UPDATE vc_history SET status = 'FAILED' WHERE job_id = ?", (sched_id,))
                db_conn.commit()
                await bot.send_message(
                    user_id,
                    f"<blockquote>❌ Scheduled VC <code>{sched_id}</code> Failed: <code>{esc_html(err_reason)}</code></blockquote>",
                    parse_mode="HTML",
                )
                return

            cursor.execute("UPDATE vc_history SET status = 'EXECUTED' WHERE job_id = ?", (sched_id,))
            db_conn.commit()
            await bot.send_message(
                user_id,
                f"<blockquote>{pe('BELL')} <b>SCHEDULED VC STARTED!</b>\n\n"
                f"🆔 <b>Live Job:</b> <code>{job_id}</code>\n"
                f"🤖 <b>Joined:</b> <code>{actual_joined}/{req_qty} IDs</code>\n"
                f"⏳ <b>Duration:</b> <code>{duration_mins} Mins</code></blockquote>",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=f"🎛️ Live Controls ({job_id})", callback_data=f"managevc_{job_id}", style="success")]
                    ]
                ),
                parse_mode="HTML",
            )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Scheduled job {sched_id} error: {e}")

    async def stop_vc_job(
        self, job_id: str, notify_user: bool = False, auto_expired: bool = False
    ) -> bool:
        job = self.active_jobs.pop(job_id, None)
        if not job:
            return False

        cur_task = asyncio.current_task()
        if job.get("task") and not job["task"].done() and job["task"] is not cur_task:
            job["task"].cancel()
        if job.get("dyn_task") and not job["dyn_task"].done() and job["dyn_task"] is not cur_task:
            job["dyn_task"].cancel()

        for w in list(job["workers"]):
            cid = job["chat_ids"].get(w["id"], job["chat_id_num"])
            try:
                await self._leave_worker_vc_call(w.get("call"), cid)
            finally:
                if cid is not None:
                    w["active_chats"].discard(cid)

        status_val = "AUTO_COMPLETED" if auto_expired else "STOPPED_MANUAL"
        cursor.execute(
            "UPDATE vc_history SET status = ?, cost_credits = ? WHERE job_id = ?",
            (status_val, job["cost"], job_id),
        )
        db_conn.commit()

        await send_bot_log(
            "VC_AUTO_LEFT" if auto_expired else "VC_ORDER_LEFT",
            pe("MINUS"),
            user=get_user(job["user_id"]),
            fields=[
                ("Job ID", f"<code>{job_id}</code>"),
                ("Target", f"<code>{esc_html(job['title'])}</code>"),
                ("Accounts", f"<code>{job['qty']} IDs</code>"),
                ("Total Billed", f"<code>{fmt_num(job['cost'])} Cr</code>"),
            ],
        )

        if notify_user:
            try:
                await bot.send_message(
                    job["user_id"],
                    f"<blockquote>{pe('CHECK')} <b>VC COMPLETED — ALL IDs LEFT VC!</b>\n\n"
                    f"🆔 <b>Job ID:</b> <code>{job_id}</code>\n"
                    f"🔗 <b>Target:</b> <code>{esc_html(job['link'])}</code>\n"
                    f"⏱️ <b>Minutes Ran:</b> <code>{job['minutes_billed']}m</code>\n"
                    f"💸 <b>Total Credits Used:</b> <code>{fmt_num(job['cost'])} Cr</code></blockquote>",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="🚀 Join Another VC", callback_data="start_vc_flow", style="success")],
                            [InlineKeyboardButton(text="🏠 Back to Home", callback_data="main_menu", style="primary")],
                        ]
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass
        return True

    async def shutdown_all(self):
        for s in list(self.joining_sessions.values()):
            s["cancelled"] = True
        for jid in list(self.active_jobs.keys()):
            try:
                await self.stop_vc_job(jid, notify_user=False, auto_expired=False)
            except Exception:
                pass
        for s in list(self.scheduled_jobs.values()):
            if s.get("task") and not s["task"].done():
                s["task"].cancel()
        self.scheduled_jobs.clear()
        for w in list(self.workers):
            try:
                await w["client"].stop()
            except Exception:
                pass
        self.workers.clear()


vc_engine = VCEngineManager()


async def session_health_monitor_loop():
    while True:
        try:
            mins = max(5, get_setting("health_ping_mins", int) or 10)
            await asyncio.sleep(mins * 60)
            if vc_engine.workers:
                await vc_engine.ping_and_verify_all_workers()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Health monitor error: {e}")
            await asyncio.sleep(60)


# =====================================================================
# 9. FSM STATES
# =====================================================================
class VCJoinFlow(StatesGroup):
    waiting_for_link = State()
    waiting_for_quantity = State()
    waiting_for_time = State()
    waiting_for_launch_mode = State()
    waiting_for_schedule_ist = State()


class DepositFlow(StatesGroup):
    waiting_for_amount = State()


class RedeemFlow(StatesGroup):
    waiting_for_code = State()


class AdminFlow(StatesGroup):
    waiting_for_ratio = State()
    waiting_for_start_fee = State()
    waiting_for_credit_price = State()
    waiting_for_welcome_bonus = State()
    waiting_for_code_creation = State()
    waiting_for_credit_mod = State()
    waiting_for_broadcast = State()
    waiting_for_broadcast_style = State()
    waiting_for_phone = State()
    waiting_for_otp = State()
    waiting_for_2fa = State()
    waiting_for_new_admin = State()
    waiting_for_fsub_input = State()
    waiting_for_log_forward = State()
    waiting_for_revive_json = State()
    waiting_for_backup_interval = State()
    waiting_for_health_interval = State()
    waiting_for_user_lookup = State()
    waiting_for_brand_logo = State()
    waiting_for_wipe_code = State()
    waiting_for_ref_bonus = State()
    waiting_for_vip_target = State()
    waiting_for_fine_amount = State()
    waiting_for_emoji_value = State()
    waiting_for_transfer_owner = State()


# =====================================================================
# 10. REFERRAL REWARD PROCESSOR & STRICT FORCE-SUB MIDDLEWARE
# =====================================================================
async def process_referral_reward(new_user: types.User):
    if get_setting("referral_enabled", int) != 1:
        return

    u = get_user(new_user.id)
    if not u or not u["referred_by"] or u["ref_rewarded"] == 1:
        return

    ref_id = u["referred_by"]
    if ref_id == new_user.id:
        return

    ref_bonus = get_setting("ref_bonus", float)
    vip_target = max(1, get_setting("vip_ref_target", int))

    cursor.execute("UPDATE users SET ref_rewarded = 1 WHERE user_id = ?", (new_user.id,))
    cursor.execute(
        "UPDATE users SET credits = credits + ?, referrals = referrals + 1 WHERE user_id = ?",
        (ref_bonus, ref_id),
    )
    db_conn.commit()

    ref_u = get_user(ref_id)
    refs_now = ref_u["referrals"] if ref_u else 1
    ref_bal = fmt_num(ref_u["credits"]) if ref_u else fmt_num(ref_bonus)
    bar, pct, is_vip = build_vip_bar(refs_now, vip_target)

    if is_vip:
        vip_block = (
            f"{pe('CROWN')} <b>VIP STATUS:</b> <code>🏆 UNLOCKED!</code>\n"
            f"<code>{bar} 100%</code>"
        )
    else:
        vip_block = (
            f"{pe('ROCKET')} <b>VIP PROGRESS:</b> <code>{refs_now}/{vip_target} Referrals</code>\n"
            f"<code>{bar} {pct}%</code>"
        )

    invited_label = f"@{esc_html(new_user.username)}" if new_user.username else esc_html(new_user.first_name or "New User")
    alert_text = (
        f"<blockquote>{pe('STAR')} <b>NEW REFERRAL VERIFIED!</b> {pe('GIFT')}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{pe('USER')} <b>Invited User:</b> {invited_label} [<code>{new_user.id}</code>]\n"
        f"{pe('CHECK')} <b>Verification:</b> F-Sub Completed &amp; Verified\n"
        f"{pe('COIN')} <b>Credit Reward:</b> <code>+{fmt_num(ref_bonus)} Credits</code>\n"
        f"{pe('DIAMOND')} <b>Updated Balance:</b> <code>{ref_bal} Cr</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{vip_block}\n\n"
        f"<i>Keep sharing your invite link to earn unlimited free VC credits!</i></blockquote>"
    )

    try:
        await bot.send_message(ref_id, alert_text, parse_mode="HTML")
    except Exception:
        pass

    await send_bot_log(
        "REFERRAL_VERIFIED",
        pe("STAR"),
        user=new_user,
        fields=[
            ("Referred By", f"<code>{ref_id}</code>"),
            ("Credit Given", f"<code>+{fmt_num(ref_bonus)} Cr</code>"),
            ("Referrer Total Refs", f"<code>{refs_now}/{vip_target}</code>"),
        ],
        extra_id_tags=[ref_id],
    )


async def check_user_all_fsubs(user_id: int) -> list[sqlite3.Row]:
    targets = get_fsub_targets()
    if not targets:
        return []
    missing = []
    active_statuses = {"member", "administrator", "creator", "restricted"}
    for t in targets:
        ident = (t["chat_identifier"] or "").strip()
        if not ident:
            inv = (t["invite_url"] or "").strip()
            if "t.me/" in inv and not ("t.me/+" in inv or "joinchat/" in inv):
                slug = inv.split("t.me/")[-1].split("/")[0].split("?")[0].strip()
                if slug:
                    ident = "@" + slug
        if not ident:
            missing.append(t)
            continue

        chat_key = int(ident) if (ident.startswith("-100") or ident.lstrip("-").isdigit()) else ident
        try:
            member = await bot.get_chat_member(chat_id=chat_key, user_id=user_id)
            if member.status not in active_statuses:
                missing.append(t)
        except Exception:
            missing.append(t)
    return missing


def build_fsub_gate_keyboard(targets: list[sqlite3.Row]) -> InlineKeyboardMarkup:
    ch_count, grp_count, rows = 0, 0, []
    for t in targets:
        inv = (t["invite_url"] or "").strip()
        ident = (t["chat_identifier"] or "").strip()
        if not inv and ident.startswith("@"):
            inv = f"https://t.me/{ident.lstrip('@')}"
        if t["target_type"] == "channel":
            ch_count += 1
            lbl = f"📢 Join Channel {ch_count} ({t['title'][:18]})"
        else:
            grp_count += 1
            lbl = f"👥 Join Group {grp_count} ({t['title'][:18]})"
        if inv and inv.startswith("http"):
            rows.append([InlineKeyboardButton(text=lbl, url=inv, style="primary")])

    rows.append([InlineKeyboardButton(text="🔄 Verify Membership", callback_data="verify_fsub", style="success")])
    rows.append([
        InlineKeyboardButton(
            text=f"👑 Owner (@{get_owner_username()})",
            url=f"https://t.me/{get_owner_username()}",
            style="primary",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class ForceSubscribeMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)
        user_id = user.id

        if isinstance(event, types.Message) and event.text and event.text.startswith("/start"):
            parts = event.text.split()
            ref_by = None
            if len(parts) > 1 and parts[1].startswith("ref_"):
                try:
                    cand = int(parts[1].replace("ref_", ""))
                    if cand != user_id:
                        ref_by = cand
                except Exception:
                    pass
            is_new = get_user(user_id) is None
            ensure_user_exists(user, referred_by=ref_by)
            if is_new:
                await send_bot_log(
                    "NEW_USER_JOINED",
                    pe("USER"),
                    user=user,
                    fields=[("Referred By", f"<code>{ref_by or 'Direct'}</code>")],
                )
        else:
            ensure_user_exists(user)

        if is_admin(user_id):
            return await handler(event, data)

        if isinstance(event, types.CallbackQuery) and event.data == "verify_fsub":
            return await handler(event, data)

        all_targets = get_fsub_targets()
        if not all_targets:
            u_row = get_user(user_id)
            if u_row and u_row["verified"] == 0:
                bonus = get_setting("welcome_bonus", float) if get_setting("bonus_enabled", int) == 1 else 0.0
                cursor.execute(
                    "UPDATE users SET verified = 1, credits = credits + ? WHERE user_id = ?",
                    (bonus, user_id),
                )
                db_conn.commit()
                await process_referral_reward(user)
            return await handler(event, data)

        missing_targets = await check_user_all_fsubs(user_id)
        if missing_targets:
            u_prev = get_user(user_id)
            if u_prev and u_prev["verified"] == 1:
                cursor.execute("UPDATE users SET verified = 0 WHERE user_id = ?", (user_id,))
                db_conn.commit()

            kb = build_fsub_gate_keyboard(all_targets)
            lock_msg = (
                f"<blockquote>{pe('SHIELD')} <b>FORCE SUBSCRIBE REQUIRED</b>\n\n"
                f"Bot ko use karne ke liye niche diye gaye sabhi Channels aur Groups join karein.\n"
                f"🔒 <i>Join kiye bina ya leave karne par bot locked rahega!</i>\n\n"
                f"Join karne ke baad <b>🔄 Verify Membership</b> dabayein.</blockquote>"
            )
            if isinstance(event, types.Message):
                await event.answer(lock_msg, reply_markup=kb, parse_mode="HTML")
            elif isinstance(event, types.CallbackQuery):
                await safe_answer(event, "⚠️ Pehle sabhi Channels & Groups join karein!", show_alert=True)
                await safe_edit(event.message, lock_msg, reply_markup=kb)
            return

        return await handler(event, data)


dp.message.middleware(ForceSubscribeMiddleware())
dp.callback_query.middleware(ForceSubscribeMiddleware())

# =====================================================================
# 11. NAVIGATION, NUMPAD, WELCOME DP SPLASH & HOME / PROFILE BUILDERS
# =====================================================================
def get_nav_rows(prev_callback: str | None = None, prev_label: str = "🔙 Back") -> list[list[InlineKeyboardButton]]:
    rows = []
    if prev_callback and prev_callback != "main_menu":
        rows.append([InlineKeyboardButton(text=prev_label, callback_data=prev_callback, style="primary")])
    rows.append([InlineKeyboardButton(text="🏠 Back to Home", callback_data="main_menu", style="danger")])
    return rows


def make_nav_kb(prev_callback: str | None = None, prev_label: str = "🔙 Back") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=get_nav_rows(prev_callback, prev_label))


def get_numpad(prefix: str, prev_callback: str = "main_menu", prev_label: str = "🔙 Back") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=str(n), callback_data=f"{prefix}_{n}", style="primary") for n in (1, 2, 3)],
        [InlineKeyboardButton(text=str(n), callback_data=f"{prefix}_{n}", style="primary") for n in (4, 5, 6)],
        [InlineKeyboardButton(text=str(n), callback_data=f"{prefix}_{n}", style="primary") for n in (7, 8, 9)],
        [
            InlineKeyboardButton(text="⌫ Clear", callback_data=f"{prefix}_clear", style="danger"),
            InlineKeyboardButton(text="0", callback_data=f"{prefix}_0", style="primary"),
            InlineKeyboardButton(text="✅ Submit", callback_data=f"{prefix}_submit", style="success"),
        ],
    ]
    rows.extend(get_nav_rows(prev_callback, prev_label))
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_welcome_splash(target_chat_id: int, user: types.User, delete_msg: types.Message | None = None):
    """
    Send aesthetic Welcome Splash screen with the user's own Telegram Profile Picture (DP)
    on top, premium emojis/stickers, and an 'Enter Bot' button to open Main Home.
    """
    if delete_msg is not None:
        try:
            await delete_msg.delete()
        except Exception:
            pass

    uname = f"@{esc_html(user.username)}" if user.username else esc_html(user.first_name or "Friend")
    start_fee = fmt_num(get_setting("vc_start_fee", float))
    caption = (
        f"<blockquote>{pe('WELCOME')} <b>WELCOME TO PREMIUM PAID VC BOT</b> {pe('CROWN')}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{pe('USER')} <b>Hello, {uname}!</b>\n\n"
        f"{pe('ROCKET')} <b>Fast Multi-VC Engine</b> — Ek saath multiple groups &amp; channels!\n"
        f"{pe('MIC_OFF')} <b>Full Live Controls</b> — +Time, +ID / -ID &amp; Dynamic Mode!\n"
        f"{pe('MONEY')} <b>Smart Per-Minute Billing</b> — Fixed <code>{start_fee} Cr</code> start fee + usage billing.\n"
        f"{pe('STAR')} <b>Instant UPI QR &amp; VIP Referrals</b> — 3s auto-deposit &amp; free rewards!\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{pe('BOLT')} <i>Bot mein enter karne ke liye niche button par click karein:</i></blockquote>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Enter Bot (Main Menu)", callback_data="main_menu", style="success")],
            [
                InlineKeyboardButton(text="📖 Help & Guide", callback_data="help_menu", style="primary"),
                InlineKeyboardButton(text=f"👑 Owner", url=f"https://t.me/{get_owner_username()}", style="primary"),
            ],
        ]
    )

    # Fetch user's Telegram Profile Picture (DP)
    dp_file_id = None
    try:
        photos = await bot.get_user_profile_photos(user.id, limit=1)
        if photos and photos.total_count > 0 and photos.photos:
            dp_file_id = photos.photos[0][-1].file_id
    except Exception:
        dp_file_id = None

    if dp_file_id:
        try:
            await bot.send_photo(
                chat_id=target_chat_id,
                photo=dp_file_id,
                caption=caption,
                reply_markup=kb,
                parse_mode="HTML",
            )
            return
        except Exception:
            pass

    # Fallback if user has no DP or hidden DP
    if os.path.exists(BRAND_LOGO_PATH):
        try:
            with open(BRAND_LOGO_PATH, "rb") as bf:
                await bot.send_photo(
                    chat_id=target_chat_id,
                    photo=BufferedInputFile(bf.read(), filename="welcome.png"),
                    caption=caption,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
                return
        except Exception:
            pass

    await bot.send_message(
        chat_id=target_chat_id,
        text=caption,
        reply_markup=kb,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def build_home_text(user: types.User) -> str:
    u = get_user(user.id)
    bal_disp = "∞ Unlimited (Admin)" if is_admin(user.id) else f"{fmt_num(u['credits']) if u else 0} Credits"
    rate = get_setting("rupee_to_credit", float)
    price_per_cr = round(1.0 / rate, 2) if rate > 0 else 1.0
    start_fee = fmt_num(get_setting("vc_start_fee", float))
    ref_on = get_setting("referral_enabled", int) == 1
    ref_line = (
        f"{pe('GIFT')} <b>Referral Bonus:</b> <code>+{fmt_num(get_setting('ref_bonus', float))} Cr/Ref</code> (VIP @ <code>{get_setting('vip_ref_target', int)} Refs</code>)\n"
        if ref_on
        else ""
    )
    return (
        f"<blockquote>{pe('CROWN')} <b>PREMIUM PAID VC BOT — HOME</b>\n\n"
        f"{pe('USER')} <b>Welcome:</b> @{esc_html(user.username) if user.username else esc_html(user.first_name)}\n"
        f"{pe('COIN')} <b>Wallet Balance:</b> <code>{bal_disp}</code>\n"
        f"{pe('FIRE')} <b>Live VC Worker Pool:</b> <code>{vc_engine.available_count()} IDs Online</code> "
        f"(<code>{vc_engine.total_active_streams()} Live VCs</code>)\n"
        f"{pe('TIMER')} <b>Server Time (IST):</b> <code>{log_date_ist()} IST</code>\n\n"
        f"{pe('DIAMOND')} <b>Credit Price:</b> <code>₹1/- = {fmt_num(rate)} Cr</code> (<code>₹{fmt_num(price_per_cr)}/Cr</code>)\n"
        f"{pe('BOLT')} <b>VC Usage Rate:</b> <code>{get_setting('base_ids', int)} IDs × {get_setting('base_time', int)} Min = {fmt_num(get_setting('base_credits', float))} Cr</code>\n"
        f"{pe('MONEY')} <b>Fixed Starting Fee:</b> <code>{start_fee} Cr</code> (+ per-min live billing)\n"
        f"{ref_line}\n"
        f"{pe('STAR')} <b>Owner:</b> @{get_owner_username()} | <b>ID:</b> <code>{get_super_owner_id()}</code></blockquote>"
    )


async def build_my_profile_card(user: types.User) -> str:
    u = get_user(user.id)
    bal_disp = "∞ Unlimited (Admin)" if is_admin(user.id) else f"{fmt_num(u['credits']) if u else 0} Cr"
    orders = u["orders_count"] if u else 0
    refs = u["referrals"] if u else 0
    paid_inr = fmt_num(u["total_paid_inr"]) if u else 0
    vc_mins = u["total_vc_minutes"] if u else 0
    vip_target = max(1, get_setting("vip_ref_target", int))
    bar, pct, is_vip = build_vip_bar(refs, vip_target)
    vip_txt = f"{pe('TROPHY')} VIP UNLOCKED!" if is_vip else f"{refs}/{vip_target} Referrals"
    ref_status = "🟢 Active" if get_setting("referral_enabled", int) == 1 else "🔴 Paused"
    me = await bot.get_me()
    ref_link = f"https://t.me/{me.username}?start=ref_{user.id}"

    return (
        f"<blockquote>{pe('PROFILE')} <b>MY PROFILE &amp; VIP CARD</b>\n\n"
        f"{pe('USER')} <b>Account:</b> @{esc_html(user.username) if user.username else esc_html(user.first_name)}\n"
        f"{pe('CHECK')} <b>User ID:</b> <code>{user.id}</code>\n"
        f"{pe('COIN')} <b>Wallet Credits:</b> <code>{bal_disp}</code>\n"
        f"{pe('DIAMOND')} <b>Total Deposited:</b> <code>₹{paid_inr}/-</code>\n"
        f"{pe('WOLF')} <b>VC Orders:</b> <code>{orders}</code> (<code>{vc_mins} Mins</code>)\n"
        f"{pe('BOLT')} <b>Successful Referrals:</b> <code>{refs}</code> ({ref_status})\n\n"
        f"{pe('ROCKET')} <b>VIP UNLOCK PROGRESS ({vip_txt}):</b>\n"
        f"<code>{bar} {pct}%</code>\n\n"
        f"{pe('ARROW')} <b>YOUR REFERRAL LINK:</b>\n"
        f"<code>{ref_link}</code>\n"
        f"<i>Per verified referral: +{fmt_num(get_setting('ref_bonus', float))} Cr</i></blockquote>"
    )


def get_main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    active_cnt = len([j for j in vc_engine.active_jobs.values() if j["user_id"] == user_id])
    sched_cnt = len([j for j in vc_engine.scheduled_jobs.values() if j["user_id"] == user_id])
    total_live = active_cnt + sched_cnt

    rows = [
        [InlineKeyboardButton(text="🚀 JOIN / SCHEDULE VC NOW", callback_data="start_vc_flow", style="success")],
        [
            InlineKeyboardButton(text="👤 My Profile & VIP", callback_data="my_profile", style="primary"),
            InlineKeyboardButton(text="💳 Deposit (₹ QR)", callback_data="deposit_menu", style="success"),
        ],
        [
            InlineKeyboardButton(text="🎟️ Redeem Code", callback_data="redeem_menu", style="primary"),
            InlineKeyboardButton(text=f"🎛️ Live / Sched VC ({total_live})", callback_data="my_active_orders", style="primary"),
        ],
        [
            InlineKeyboardButton(text="📜 Order History", callback_data="my_vc_history", style="primary"),
            InlineKeyboardButton(text="🏆 Top Referrers", callback_data="ref_leaderboard", style="primary"),
        ],
        [InlineKeyboardButton(text="📖 Help & Guide", callback_data="help_menu", style="primary")],
        [
            InlineKeyboardButton(text=f"👑 Owner (@{get_owner_username()})", url=f"https://t.me/{get_owner_username()}", style="primary"),
            InlineKeyboardButton(text=f"🆔 Owner ID: {get_super_owner_id()}", callback_data="show_owner_info", style="primary"),
        ],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton(text="🛠️ ADMIN CONTROL ROOM", callback_data="admin_panel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# =====================================================================
# 12. START, VERIFY F-SUB, WELCOME SPLASH, HOME, PROFILE & HELP
# =====================================================================
@dp.callback_query(F.data == "show_owner_info")
async def show_owner_info_alert(callback: types.CallbackQuery):
    await safe_answer(
        callback,
        f"👑 Super Owner: @{get_owner_username()}\n🆔 ID: {get_super_owner_id()}",
        show_alert=True,
    )


@dp.callback_query(F.data == "verify_fsub")
async def verify_fsub_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    missing = await check_user_all_fsubs(user_id)
    if missing:
        return await safe_answer(
            callback,
            f"⚠️ Abhi bhi {len(missing)} Channel/Group join karna baki hai!",
            show_alert=True,
        )

    u = get_user(user_id)
    if not u:
        bonus = get_setting("welcome_bonus", float) if get_setting("bonus_enabled", int) == 1 else 0.0
        cursor.execute(
            """
            INSERT INTO users (user_id, username, first_name, credits, verified, joined_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (
                user_id,
                callback.from_user.username or "",
                callback.from_user.first_name or "User",
                bonus,
                full_date_ist(),
            ),
        )
        db_conn.commit()
        await process_referral_reward(callback.from_user)
    elif u["verified"] == 0:
        give_welcome = (
            get_setting("bonus_enabled", int) == 1
            and u["orders_count"] == 0
            and u["credits"] == 0.0
        )
        bonus = get_setting("welcome_bonus", float) if give_welcome else 0.0
        cursor.execute(
            "UPDATE users SET verified = 1, credits = credits + ? WHERE user_id = ?",
            (bonus, user_id),
        )
        db_conn.commit()
        await process_referral_reward(callback.from_user)

    await safe_answer(callback, "✅ Membership Verified!", show_alert=True)
    await send_welcome_splash(callback.message.chat.id, callback.from_user, delete_msg=callback.message)


@dp.message(CommandStart())
async def start_cmd_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await send_welcome_splash(message.chat.id, message.from_user)


@dp.message(Command("admin"))
async def admin_cmd_shortcut(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await message.reply("<blockquote>❌ Unauthorized!</blockquote>", parse_mode="HTML")
    await state.clear()
    sent = await message.answer("<blockquote>🛠️ Loading Admin Control Room...</blockquote>", parse_mode="HTML")
    fake_cb = types.CallbackQuery(
        id="0",
        from_user=message.from_user,
        chat_instance="0",
        message=sent,
        data="admin_panel",
    )
    await admin_panel_cb(fake_cb, state)


@dp.callback_query(F.data == "main_menu")
async def main_menu_cb(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_answer(callback)
    await safe_edit(
        callback.message,
        await build_home_text(callback.from_user),
        reply_markup=get_main_menu_kb(callback.from_user.id),
    )


@dp.callback_query(F.data == "my_profile")
async def my_profile_cb(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_answer(callback)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💳 Deposit Credits", callback_data="deposit_menu", style="success"),
                InlineKeyboardButton(text="🎟️ Redeem Code", callback_data="redeem_menu", style="primary"),
            ],
            [
                InlineKeyboardButton(text="📜 VC History", callback_data="my_vc_history", style="primary"),
                InlineKeyboardButton(text="💵 Payment History", callback_data="my_pay_history", style="primary"),
            ],
            [InlineKeyboardButton(text="🏠 Back to Home", callback_data="main_menu", style="danger")],
        ]
    )
    await safe_edit(callback.message, await build_my_profile_card(callback.from_user), reply_markup=kb)


@dp.callback_query(F.data == "my_vc_history")
async def my_vc_history_cb(callback: types.CallbackQuery):
    await safe_answer(callback)
    cursor.execute(
        "SELECT * FROM vc_history WHERE user_id = ? ORDER BY rowid DESC LIMIT 10",
        (callback.from_user.id,),
    )
    rows = cursor.fetchall()
    if not rows:
        return await safe_edit(
            callback.message,
            f"<blockquote>{pe('HISTORY')} <b>YOUR VC ORDER HISTORY</b>\n\nAbhi tak koi VC order nahi hai.</blockquote>",
            reply_markup=make_nav_kb("my_profile", "🔙 Back to Profile"),
        )

    lines = [f"<blockquote>{pe('HISTORY')} <b>LAST 10 VC ORDERS</b>\n"]
    for r in rows:
        fine_note = f" | {pe('FINE')} Fine: -{fmt_num(r['fine_deducted'])}Cr" if r["fine_deducted"] > 0 else ""
        lines.append(
            f"• <b>{r['job_id']}</b> [{r['status']}]\n"
            f"  🤖 <code>{r['qty']} IDs × {r['duration_mins']}m</code> | 💸 <code>{fmt_num(r['cost_credits'])} Cr</code>{fine_note}\n"
            f"  🕒 <i>{r['scheduled_ist']}</i>"
        )
    lines.append("</blockquote>")
    await safe_edit(
        callback.message,
        "\n".join(lines),
        reply_markup=make_nav_kb("my_profile", "🔙 Back to Profile"),
    )


@dp.callback_query(F.data == "my_pay_history")
async def my_pay_history_cb(callback: types.CallbackQuery):
    await safe_answer(callback)
    cursor.execute(
        "SELECT * FROM payments WHERE user_id = ? ORDER BY rowid DESC LIMIT 10",
        (callback.from_user.id,),
    )
    rows = cursor.fetchall()
    if not rows:
        return await safe_edit(
            callback.message,
            f"<blockquote>{pe('DIAMOND')} <b>YOUR PAYMENT HISTORY</b>\n\nAbhi tak koi deposit record nahi hai.</blockquote>",
            reply_markup=make_nav_kb("my_profile", "🔙 Back to Profile"),
        )

    lines = [f"<blockquote>{pe('DIAMOND')} <b>LAST 10 DEPOSITS</b>\n"]
    for r in rows:
        icon = "✅" if r["status"] == "paid" else ("⏳" if r["status"] == "pending" else "❌")
        lines.append(
            f"• {icon} <code>{r['order_id']}</code> — <b>₹{fmt_num(r['amount'])}/-</b> (+{fmt_num(r['credits'])} Cr)\n"
            f"  Status: <code>{r['status'].upper()}</code> | 🕒 <i>{r['created_at']}</i>"
        )
    lines.append("</blockquote>")
    await safe_edit(
        callback.message,
        "\n".join(lines),
        reply_markup=make_nav_kb("my_profile", "🔙 Back to Profile"),
    )


@dp.callback_query(F.data == "ref_leaderboard")
async def ref_leaderboard_cb(callback: types.CallbackQuery):
    await safe_answer(callback)
    cursor.execute(
        "SELECT user_id, username, first_name, referrals FROM users WHERE referrals > 0 ORDER BY referrals DESC LIMIT 10"
    )
    rows = cursor.fetchall()
    vip_target = max(1, get_setting("vip_ref_target", int))
    if not rows:
        return await safe_edit(
            callback.message,
            f"<blockquote>{pe('TROPHY')} <b>TOP REFERRERS LEADERBOARD</b>\n\nAbhi tak kisi ne referrals complete nahi kiye hain. Pehle ban kar VIP unlock karein!</blockquote>",
            reply_markup=make_nav_kb(),
        )

    medals = ["🥇", "🥈", "🥉"]
    lines = [f"<blockquote>{pe('TROPHY')} <b>TOP REFERRERS (VIP GOAL: {vip_target})</b>\n"]
    for idx, r in enumerate(rows):
        m = medals[idx] if idx < 3 else f"#{idx + 1}"
        name = f"@{esc_html(r['username'])}" if r["username"] else esc_html(r["first_name"] or str(r["user_id"]))
        badge = " 🏆VIP" if r["referrals"] >= vip_target else ""
        lines.append(f"{m} {name} — <code>{r['referrals']} Refs</code>{badge}")
    lines.append("</blockquote>")
    await safe_edit(callback.message, "\n".join(lines), reply_markup=make_nav_kb())


@dp.callback_query(F.data == "help_menu")
async def help_menu_cb(callback: types.CallbackQuery):
    await safe_answer(callback)
    fine_amt = fmt_num(get_setting("vc_request_fine", float))
    start_fee = fmt_num(get_setting("vc_start_fee", float))
    ref_bonus = fmt_num(get_setting("ref_bonus", float))
    vip_target = get_setting("vip_ref_target", int)
    text = (
        f"<blockquote>{pe('GUIDE')} <b>COMPLETE HELP &amp; USER GUIDE</b>\n\n"
        f"1️⃣ <b>Fast Multi-VC Joining &amp; Cancel:</b> Group/Channel link bhejein, IDs &amp; time choose karein. IDs fast parallel mode mein live progress bar ke saath join hongi — beech mein bhi cancel kar sakte hain!\n\n"
        f"2️⃣ <b>Dynamic Joining Mode:</b> <b>Dynamic Mode</b> ON karne par har 10 second mein ek random ID leave &amp; join karegi bilkul real members jaisa!\n\n"
        f"3️⃣ <b>Live +Time, +ID &amp; -ID Controls:</b> Chalte hue VC mein <b>Live Controls</b> khol kar extra time add karein ya live members badhayein/ghatayein.\n\n"
        f"4️⃣ <b>Smart Billing ({pe('MONEY')} Start Fee + Per-Min):</b> Har order start hone par fixed <code>{start_fee} Cr</code> fee lagti hai, aur uske baad har 1 minute mein jitne IDs live chal rahe honge sirf unhi ka credit katega.\n\n"
        f"5️⃣ <b>Direct Link Rule ({pe('FINE')} Fine):</b> Hamesha <b>Direct Join Link</b> dein. Join-Request link fail hone par <code>{fine_amt} Cr</code> fine katega.\n\n"
        f"6️⃣ <b>Instant UPI Deposit &amp; Referrals:</b> 3 sec mein auto-deposit + Photo/PDF invoice. Referral verify hone par <code>+{ref_bonus} Cr</code> aur <code>{vip_target} Refs</code> par VIP unlock!\n\n"
        f"{pe('CROWN')} <b>Support:</b> @{get_owner_username()}</blockquote>"
    )
    await safe_edit(callback.message, text, reply_markup=make_nav_kb())


# =====================================================================
# 13. LIVE VC CONTROL PANEL (MIC, DYNAMIC, +TIME, +ID, -ID, STOP)
# =====================================================================
def build_live_vc_control_view(job_id: str) -> tuple[str, InlineKeyboardMarkup] | tuple[None, None]:
    job = vc_engine.active_jobs.get(job_id)
    if not job:
        return None, None

    rem = max(0, int(job["end_time"] - time.time()))
    mic_muted = job.get("mic_muted", True)
    dyn_on = job.get("dynamic_mode", False)
    eligible_more = len(vc_engine.eligible_workers_for_chat(job["chat_id_num"]))
    per_min_now = round(len(job["workers"]) * get_cost_per_id_min(), 2)

    mic_disp = f"{pe('MIC_OFF')} OFF (Muted)" if mic_muted else f"{pe('MIC_ON')} ON (Unmuted)"
    dyn_disp = f"{pe('DYNAMIC')} ON (10s Real Cycle)" if dyn_on else "🔴 OFF (Static)"

    text = (
        f"<blockquote>{pe('ROCKET')} <b>LIVE VC CONTROL PANEL — <code>{job_id}</code></b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{pe('LINK')} <b>Target:</b> <code>{esc_html(job['title'])}</code>\n"
        f"🤖 <b>Live Members:</b> <code>{len(job['workers'])} IDs</code> "
        f"(Extra Available: <code>{eligible_more}</code>)\n"
        f"{pe('TIMER')} <b>Time Left:</b> <code>{rem // 60}m {rem % 60}s</code>\n"
        f"🎙️ <b>Members Mic:</b> {mic_disp}\n"
        f"🔄 <b>Dynamic Joining:</b> {dyn_disp}\n"
        f"{pe('COIN')} <b>Current Rate:</b> <code>{fmt_num(per_min_now)} Cr/min</code> | "
        f"<b>Billed So Far:</b> <code>{fmt_num(job['cost'])} Cr</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Use live controls below without stopping your VC:</i></blockquote>"
    )

    kb_rows = []

    # Row 1: Dynamic Joining toggle (Mic is globally controlled by Admin Panel)
    kb_rows.append([
        InlineKeyboardButton(
            text="🔄 Dynamic Joining: ON" if dyn_on else "🔄 Dynamic Joining: OFF",
            callback_data=f"vcdyn_{job_id}",
            style="success" if dyn_on else "primary",
        ),
    ])

    # Row 2: +ID (hidden automatically when no extra ID available!) and -ID
    member_row = []
    if eligible_more > 0:
        member_row.append(
            InlineKeyboardButton(text="➕ Add +1 ID", callback_data=f"vcaddid_{job_id}", style="success")
        )
    if len(job["workers"]) > 1:
        member_row.append(
            InlineKeyboardButton(text="➖ Remove -1 ID", callback_data=f"vcremid_{job_id}", style="danger")
        )
    if member_row:
        kb_rows.append(member_row)

    # Row 3: Live +Time buttons
    kb_rows.append([
        InlineKeyboardButton(text="⏱️ +5 Min", callback_data=f"vcext_{job_id}_5", style="success"),
        InlineKeyboardButton(text="⏱️ +15 Min", callback_data=f"vcext_{job_id}_15", style="success"),
        InlineKeyboardButton(text="⏱️ +30 Min", callback_data=f"vcext_{job_id}_30", style="success"),
    ])

    # Row 4: Refresh & Stop
    kb_rows.append([
        InlineKeyboardButton(text="🔄 Refresh", callback_data=f"managevc_{job_id}", style="primary"),
        InlineKeyboardButton(text="🛑 Stop VC Now", callback_data=f"stopvc_{job_id}", style="danger"),
    ])
    kb_rows.extend(get_nav_rows("my_active_orders", "🔙 All Active Orders"))
    return text, InlineKeyboardMarkup(inline_keyboard=kb_rows)


@dp.callback_query(F.data == "my_active_orders")
async def show_active_vc_orders(callback: types.CallbackQuery):
    await safe_answer(callback)
    uid = callback.from_user.id
    jobs = [j for j in vc_engine.active_jobs.values() if j["user_id"] == uid]
    scheds = [j for j in vc_engine.scheduled_jobs.values() if j["user_id"] == uid]

    if not jobs and not scheds:
        return await safe_edit(
            callback.message,
            "<blockquote>📊 Abhi aapka koi active ya scheduled VC order nahi chal raha hai.</blockquote>",
            reply_markup=make_nav_kb(),
        )

    lines = ["<blockquote>📊 <b>YOUR ACTIVE &amp; SCHEDULED VC ORDERS</b>\n"]
    kb_rows = []

    for j in jobs:
        rem = max(0, int(j["end_time"] - time.time()))
        mic_i = "🔇" if j.get("mic_muted", True) else "🎙️"
        dyn_i = "🔄" if j.get("dynamic_mode", False) else ""
        lines.append(
            f"🟢 <b>{j['job_id']}</b> {mic_i}{dyn_i} | <code>{esc_html(j['title'][:20])}</code>\n"
            f"   🤖 <code>{len(j['workers'])} IDs</code> | ⏳ <code>{rem // 60}m {rem % 60}s left</code> | 💸 <code>{fmt_num(j['cost'])} Cr</code>"
        )
        kb_rows.append([
            InlineKeyboardButton(text=f"🎛️ Live Controls ({j['job_id']})", callback_data=f"managevc_{j['job_id']}", style="success"),
            InlineKeyboardButton(text="🛑 Stop", callback_data=f"stopvc_{j['job_id']}", style="danger"),
        ])

    for s in scheds:
        lines.append(
            f"📅 <b>{s['job_id']}</b> | 🤖 <code>{s['qty']} IDs × {s['duration_mins']}m</code>\n"
            f"   🕒 <code>{s['run_at_ist']}</code>"
        )
        kb_rows.append([InlineKeyboardButton(text=f"❌ Cancel {s['job_id']}", callback_data=f"cancelsched_{s['job_id']}", style="danger")])

    lines.append("</blockquote>")
    kb_rows.append([InlineKeyboardButton(text="🔄 Refresh List", callback_data="my_active_orders", style="primary")])
    kb_rows.extend(get_nav_rows())
    await safe_edit(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@dp.callback_query(F.data.startswith("managevc_"))
async def manage_single_live_vc(callback: types.CallbackQuery):
    jid = callback.data.replace("managevc_", "")
    job = vc_engine.active_jobs.get(jid)
    if not job or (job["user_id"] != callback.from_user.id and not is_admin(callback.from_user.id)):
        return await safe_answer(callback, "⚠️ VC Order already completed!", show_alert=True)
    await safe_answer(callback)
    text, kb = build_live_vc_control_view(jid)
    if text:
        await safe_edit(callback.message, text, reply_markup=kb)





@dp.callback_query(F.data.startswith("vcdyn_"))
async def toggle_live_vc_dynamic(callback: types.CallbackQuery):
    jid = callback.data.replace("vcdyn_", "")
    job = vc_engine.active_jobs.get(jid)
    if not job or (job["user_id"] != callback.from_user.id and not is_admin(callback.from_user.id)):
        return await safe_answer(callback, "⚠️ Order expired!", show_alert=True)
    ok, is_on = vc_engine.toggle_dynamic_mode(jid)
    if ok:
        await safe_answer(
            callback,
            "🔄 Dynamic Joining ON! Har 10s mein random ID leave/join karegi." if is_on else "🔴 Dynamic Joining OFF!",
            show_alert=True,
        )
    text, kb = build_live_vc_control_view(jid)
    if text:
        await safe_edit(callback.message, text, reply_markup=kb)


@dp.callback_query(F.data.startswith("vcaddid_"))
async def live_add_vc_member(callback: types.CallbackQuery):
    jid = callback.data.replace("vcaddid_", "")
    job = vc_engine.active_jobs.get(jid)
    if not job or (job["user_id"] != callback.from_user.id and not is_admin(callback.from_user.id)):
        return await safe_answer(callback, "⚠️ Order expired!", show_alert=True)
    await safe_answer(callback, "➕ Adding +1 ID to live VC...")
    ok, msg = await vc_engine.add_member_to_job(jid)
    text, kb = build_live_vc_control_view(jid)
    if text:
        await safe_edit(callback.message, text, reply_markup=kb)
    if not ok:
        await callback.message.answer(f"<blockquote>⚠️ {esc_html(msg)}</blockquote>", parse_mode="HTML")


@dp.callback_query(F.data.startswith("vcremid_"))
async def live_remove_vc_member(callback: types.CallbackQuery):
    jid = callback.data.replace("vcremid_", "")
    job = vc_engine.active_jobs.get(jid)
    if not job or (job["user_id"] != callback.from_user.id and not is_admin(callback.from_user.id)):
        return await safe_answer(callback, "⚠️ Order expired!", show_alert=True)
    ok, msg = await vc_engine.remove_member_from_job(jid)
    await safe_answer(callback, f"✅ {msg}" if ok else f"⚠️ {msg}", show_alert=True)
    text, kb = build_live_vc_control_view(jid)
    if text:
        await safe_edit(callback.message, text, reply_markup=kb)


@dp.callback_query(F.data.startswith("vcext_"))
async def live_extend_vc_time(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    jid = f"{parts[1]}_{parts[2]}"
    extra_mins = int(parts[3])
    job = vc_engine.active_jobs.get(jid)
    if not job or (job["user_id"] != callback.from_user.id and not is_admin(callback.from_user.id)):
        return await safe_answer(callback, "⚠️ Order expired!", show_alert=True)
    ok, msg = await vc_engine.extend_job_time(jid, extra_mins)
    await safe_answer(callback, f"✅ {msg}" if ok else f"❌ {msg}", show_alert=True)
    text, kb = build_live_vc_control_view(jid)
    if text:
        await safe_edit(callback.message, text, reply_markup=kb)


@dp.callback_query(F.data.startswith("stopvc_"))
async def force_stop_vc_order(callback: types.CallbackQuery):
    jid = callback.data.replace("stopvc_", "")
    job = vc_engine.active_jobs.get(jid)
    if not job or (job["user_id"] != callback.from_user.id and not is_admin(callback.from_user.id)):
        return await safe_answer(callback, "⚠️ Order already finished!", show_alert=True)
    await vc_engine.stop_vc_job(jid, notify_user=False, auto_expired=False)
    await safe_answer(callback, "✅ All IDs left this VC safely!", show_alert=True)
    await show_active_vc_orders(callback)


@dp.callback_query(F.data.startswith("cancelsched_"))
async def cancel_scheduled_vc_order(callback: types.CallbackQuery):
    sid = callback.data.replace("cancelsched_", "")
    s = vc_engine.scheduled_jobs.pop(sid, None)
    if not s or (s["user_id"] != callback.from_user.id and not is_admin(callback.from_user.id)):
        return await safe_answer(callback, "⚠️ Already executed or cancelled!", show_alert=True)
    if s.get("task") and not s["task"].done():
        s["task"].cancel()
    cursor.execute("UPDATE vc_history SET status = 'CANCELLED' WHERE job_id = ?", (sid,))
    db_conn.commit()
    await safe_answer(callback, "✅ Scheduled VC Cancelled!", show_alert=True)
    await show_active_vc_orders(callback)


# =====================================================================
# 14. FAMGATEWAY INSTANT UPI DEPOSIT & RECEIPTS
# =====================================================================
def fulfill_payment_in_db(order_id: str, utr: str, sender_name: str = "Verified Payer") -> tuple[bool, dict]:
    cursor.execute("SELECT * FROM payments WHERE order_id = ?", (order_id,))
    pay = cursor.fetchone()
    if not pay or pay["status"] == "paid":
        return False, {}
    cursor.execute("UPDATE payments SET status = 'paid', utr = ? WHERE order_id = ?", (utr, order_id))
    cursor.execute(
        "UPDATE users SET credits = credits + ?, total_paid_inr = total_paid_inr + ? WHERE user_id = ?",
        (pay["credits"], pay["amount"], pay["user_id"]),
    )
    db_conn.commit()
    u = get_user(pay["user_id"])
    return True, {
        "user_id": pay["user_id"],
        "user_name": (u["first_name"] or u["username"] or "User") if u else sender_name,
        "amount": pay["amount"],
        "credits": pay["credits"],
        "new_balance": fmt_num(u["credits"]) if u else fmt_num(pay["credits"]),
        "chat_id": pay["chat_id"],
        "message_id": pay["message_id"],
        "utr": utr,
    }


@dp.callback_query(F.data == "deposit_menu")
async def show_deposit_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_answer(callback)
    rate = get_setting("rupee_to_credit", float)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"₹50 ({fmt_num(50 * rate)} Cr)", callback_data="quickdep_50", style="success"),
                InlineKeyboardButton(text=f"₹80 ({fmt_num(80 * rate)} Cr)", callback_data="quickdep_80", style="success"),
            ],
            [
                InlineKeyboardButton(text=f"₹100 ({fmt_num(100 * rate)} Cr)", callback_data="quickdep_100", style="success"),
                InlineKeyboardButton(text=f"₹200 ({fmt_num(200 * rate)} Cr)", callback_data="quickdep_200", style="primary"),
            ],
            [InlineKeyboardButton(text=f"🔥 ₹500 ({fmt_num(500 * rate)} Cr)", callback_data="quickdep_500", style="primary")],
            [InlineKeyboardButton(text="🔢 Custom Amount (Numpad)", callback_data="custom_dep_numpad", style="primary")],
            *get_nav_rows(),
        ]
    )
    await safe_edit(
        callback.message,
        f"<blockquote>{pe('DIAMOND')} <b>INSTANT ZERO-FEE UPI DEPOSIT</b>\n\n"
        f"💱 <b>Exchange Rate:</b> <code>₹1/- = {fmt_num(rate)} Credits</code>\n"
        f"⚡ <b>Auto-Verification:</b> 3 Seconds + Official PNG &amp; PDF Invoice\n\n"
        f"Select a pack below or enter a custom amount:</blockquote>",
        reply_markup=kb,
    )


async def generate_and_send_locked_qr(callback: types.CallbackQuery, amount: float):
    rate = get_setting("rupee_to_credit", float)
    credits_to_add = fmt_num(amount * rate)
    await safe_edit(callback.message, f"<blockquote>{pe('BOLT')} Generating ₹{fmt_num(amount)}/- Locked UPI QR...</blockquote>")

    try:
        order = await asyncio.to_thread(
            fg.create_order,
            amount=float(amount),
            customer_name=callback.from_user.first_name or f"User_{callback.from_user.id}",
        )
    except Exception as e:
        return await safe_edit(
            callback.message,
            f"<blockquote>❌ <b>FamGateway Error:</b>\n<code>{esc_html(str(e))}</code></blockquote>",
            reply_markup=make_nav_kb("deposit_menu", "🔙 Back to Deposit"),
        )

    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(order.upi_intent)
    qr.make(fit=True)
    bio = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(bio, format="PNG")

    try:
        await callback.message.delete()
    except Exception:
        pass

    sent = await callback.message.answer_photo(
        photo=BufferedInputFile(bio.getvalue(), filename=f"{order.order_id}.png"),
        caption=(
            f"<blockquote>{pe('QR')} <b>LOCKED UPI PAYMENT QR</b>\n\n"
            f"🆔 <b>Order ID:</b> <code>{order.order_id}</code>\n"
            f"💵 <b>Exact Amount:</b> <code>₹{fmt_num(amount)}/-</code>\n"
            f"🎁 <b>Credits to Receive:</b> <code>+{credits_to_add} Cr</code>\n"
            f"⏳ <b>Valid For:</b> <code>10 Minutes</code>\n\n"
            f"<i>Scan &amp; Pay using any UPI app. Credits auto-add in 3 seconds!</i></blockquote>"
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"📲 Pay ₹{fmt_num(amount)}/- Now", url=order.checkout_url, style="success")],
                *get_nav_rows("deposit_menu", "🔙 Back to Deposit"),
            ]
        ),
        parse_mode="HTML",
    )

    cursor.execute(
        """
        INSERT INTO payments (order_id, user_id, amount, credits, status, chat_id, message_id, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            order.order_id,
            callback.from_user.id,
            float(amount),
            float(credits_to_add),
            sent.chat.id,
            sent.message_id,
            full_date_ist(),
        ),
    )
    db_conn.commit()
    track_task(poll_famgateway_payment(order.order_id))


@dp.callback_query(F.data.startswith("quickdep_"))
async def handle_premade_deposit_click(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_answer(callback)
    await generate_and_send_locked_qr(callback, float(callback.data.replace("quickdep_", "")))


@dp.callback_query(F.data == "custom_dep_numpad")
async def start_custom_deposit_numpad(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    await state.set_state(DepositFlow.waiting_for_amount)
    await state.update_data(dep_amount="")
    await safe_edit(
        callback.message,
        "<blockquote>🔢 <b>CUSTOM DEPOSIT AMOUNT</b>\n\n💵 <b>Entered Amount:</b> <code>₹_/-</code></blockquote>",
        reply_markup=get_numpad("dep", "deposit_menu", "🔙 Back"),
    )


@dp.callback_query(DepositFlow.waiting_for_amount, F.data.startswith("dep_"))
async def handle_deposit_numpad(callback: types.CallbackQuery, state: FSMContext):
    act = callback.data.split("_")[1]
    val = (await state.get_data()).get("dep_amount", "")

    if act == "clear":
        val = ""
    elif act == "submit":
        if not val or int(val) < 1:
            return await safe_answer(callback, "⚠️ Minimum deposit ₹1/- hai!", show_alert=True)
        await state.clear()
        return await generate_and_send_locked_qr(callback, float(val))
    elif len(val) < 5:
        val += act

    await state.update_data(dep_amount=val)
    await safe_answer(callback)
    await safe_edit(
        callback.message,
        f"<blockquote>🔢 <b>CUSTOM DEPOSIT AMOUNT</b>\n\n💵 <b>Entered Amount:</b> <code>₹{val or '_'}/-</code></blockquote>",
        reply_markup=get_numpad("dep", "deposit_menu", "🔙 Back"),
    )


async def notify_payment_success(order_id: str, info: dict):
    png_b, pdf_b = await asyncio.to_thread(
        generate_custom_receipt_files,
        order_id,
        info["user_id"],
        info["user_name"],
        info["amount"],
        info["credits"],
        info["utr"],
        info["new_balance"],
    )
    try:
        await bot.delete_message(info["chat_id"], info["message_id"])
    except Exception:
        pass

    try:
        await bot.send_photo(
            info["chat_id"],
            BufferedInputFile(png_b, filename=f"Receipt_{order_id}.png"),
            caption=(
                f"<blockquote>{pe('CHECK')} <b>PAYMENT VERIFIED &amp; CREDITED!</b>\n\n"
                f"🆔 <b>Order:</b> <code>{order_id}</code>\n"
                f"💵 <b>Paid:</b> <code>₹{fmt_num(info['amount'])}/-</code>\n"
                f"🪙 <b>Added:</b> <code>+{fmt_num(info['credits'])} Cr</code>\n"
                f"💎 <b>New Balance:</b> <code>{info['new_balance']} Cr</code></blockquote>"
            ),
            reply_markup=make_nav_kb(),
            parse_mode="HTML",
        )
        await bot.send_document(
            info["chat_id"],
            BufferedInputFile(pdf_b, filename=f"Invoice_{order_id}.pdf"),
            caption=f"📄 Official PDF Invoice — <code>{order_id}</code>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await send_bot_log(
        "CREDIT_PURCHASE",
        pe("DIAMOND"),
        user=get_user(info["user_id"]),
        fields=[
            ("Order ID", f"<code>{order_id}</code>"),
            ("UTR", f"<code>{esc_html(info['utr'] or 'INSTANT')}</code>"),
            ("Paid", f"<code>₹{fmt_num(info['amount'])}/-</code>"),
            ("Credits", f"<code>+{fmt_num(info['credits'])} Cr</code>"),
        ],
        photo_file=BufferedInputFile(png_b, filename=f"Log_{order_id}.png"),
    )


async def poll_famgateway_payment(order_id: str):
    start_t = time.time()
    while time.time() - start_t < 600:
        await asyncio.sleep(3)
        cursor.execute("SELECT status FROM payments WHERE order_id = ?", (order_id,))
        row = cursor.fetchone()
        if not row or row["status"] != "pending":
            return
        try:
            st = await asyncio.to_thread(fg.get_status, order_id)
            if st.is_paid:
                ok, info = fulfill_payment_in_db(order_id, st.utr, st.sender_name or "UPI User")
                if ok:
                    await notify_payment_success(order_id, info)
                return
            if st.is_expired:
                cursor.execute("UPDATE payments SET status = 'expired' WHERE order_id = ?", (order_id,))
                db_conn.commit()
                return
        except Exception:
            pass


# =====================================================================
# 15. VC JOIN FLOW (MIC TOGGLE, DYNAMIC TOGGLE, FAST PROGRESS & CANCEL)
# =====================================================================
def build_step4_confirm_view(uid: int, data: dict) -> tuple[str, InlineKeyboardMarkup]:
    req_qty = int(data["qty_input"])
    time_mins = int(data["time_input"])
    mic_muted = get_global_mic_muted()
    dyn_mode = data.get("dynamic_mode", False)
    start_fee = 0.0 if is_admin(uid) else max(0.0, get_setting("vc_start_fee", float))
    per_min = 0.0 if is_admin(uid) else round(req_qty * get_cost_per_id_min(), 2)
    est_total = 0.0 if is_admin(uid) else round(start_fee + (per_min * time_mins), 2)

    cost_block = (
        "<code>FREE (Admin Unlimited)</code>"
        if is_admin(uid)
        else f"<code>{fmt_num(start_fee)} Cr Start Fee</code> + <code>{fmt_num(per_min)} Cr/min</code> (Est. <code>{fmt_num(est_total)} Cr</code>)"
    )
    mic_txt = f"{pe('MIC_OFF')} OFF (All Muted)" if mic_muted else f"{pe('MIC_ON')} ON (Unmuted)"
    dyn_txt = f"{pe('DYNAMIC')} ON (10s Real Cycle)" if dyn_mode else "🔴 OFF (Static)"

    text = (
        f"<blockquote>{pe('ROCKET')} <b>STEP 4/4: CONFIGURE &amp; LAUNCH VC</b>\n\n"
        f"{pe('LINK')} <b>Target:</b> <code>{esc_html(data.get('invite_link', ''))}</code>\n"
        f"🤖 <b>Order:</b> <code>{req_qty} IDs × {time_mins} Mins</code>\n"
        f"🎙️ <b>Members Mic:</b> {mic_txt}\n"
        f"🔄 <b>Dynamic Joining:</b> {dyn_txt}\n"
        f"💸 <b>Billing:</b> {cost_block}</blockquote>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Dynamic Joining: ON" if dyn_mode else "🔄 Dynamic Joining: OFF",
                    callback_data="pre_toggle_dyn",
                    style="success" if dyn_mode else "primary",
                ),
            ],
            [InlineKeyboardButton(text="⚡ Launch Fast Instant VC", callback_data="vcmode_instant", style="success")],
            [InlineKeyboardButton(text="📅 Schedule Future VC (IST)", callback_data="vcmode_schedule", style="primary")],
            *get_nav_rows("vc_back_to_step2", "🔙 Back"),
        ]
    )
    return text, kb


@dp.callback_query(F.data == "start_vc_flow")
async def vc_step1_link(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    if vc_engine.available_count() == 0:
        return await safe_edit(
            callback.message,
            f"<blockquote>{pe('ALERT')} Filhal koi VC ID online nahi hai! Admin se sampark karein.</blockquote>",
            reply_markup=make_nav_kb(),
        )
    await state.set_state(VCJoinFlow.waiting_for_link)
    fine_amt = fmt_num(get_setting("vc_request_fine", float))
    start_fee = fmt_num(get_setting("vc_start_fee", float))
    fine_warn = (
        f"Agar aapne <b>Join-Request wala link</b> diya aur request fail hua, "
        f"toh penalty ke roop mein <code>{fine_amt} Credits</code> ka <b>Fine</b> katega!"
        if fine_amt > 0
        else "Join-Request link dene par IDs VC join nahi kar payengi."
    )
    await safe_edit(
        callback.message,
        f"<blockquote>{pe('LINK')} <b>STEP 1/4: SEND ACTIVE VC GROUP / CHANNEL LINK</b>\n\n"
        f"🟢 <b>Online VC Pool:</b> <code>{vc_engine.available_count()} IDs Ready</code> (Fast Multi-VC)\n"
        f"{pe('MONEY')} <b>Starting Fee:</b> <code>{start_fee} Cr</code> (+ per-min usage billing)\n\n"
        f"{pe('ALERT')} <b>SAFF WARNING (DIRECT LINK ONLY):</b>\n"
        f"Hamesha <b>Direct Join Link</b> dein! {fine_warn}</blockquote>",
        reply_markup=make_nav_kb(),
    )


@dp.message(VCJoinFlow.waiting_for_link)
async def vc_step2_qty(message: types.Message, state: FSMContext):
    link = (message.text or "").strip()
    if not ("t.me/" in link or link.startswith("@")):
        return await message.reply(
            "<blockquote>❌ Valid Telegram group/channel link ya <code>@username</code> bhejein!</blockquote>",
            reply_markup=make_nav_kb(),
            parse_mode="HTML",
        )
    await state.update_data(invite_link=link, qty_input="", mic_muted=True, dynamic_mode=False)
    await state.set_state(VCJoinFlow.waiting_for_quantity)
    await message.reply(
        f"<blockquote>🔢 <b>STEP 2/4: SELECT NUMBER OF VC IDs</b>\n\n"
        f"{pe('LINK')} <b>Target:</b> <code>{esc_html(link)}</code>\n"
        f"🟢 <b>Max Available:</b> <code>{vc_engine.available_count()} IDs</code>\n"
        f"🤖 <b>Selected:</b> <code>_</code></blockquote>",
        reply_markup=get_numpad("qty", "start_vc_flow", "🔙 Back"),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "vc_back_to_step2")
async def vc_back_to_step2_cb(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    link = (await state.get_data()).get("invite_link", "")
    await state.update_data(qty_input="")
    await state.set_state(VCJoinFlow.waiting_for_quantity)
    await safe_edit(
        callback.message,
        f"<blockquote>🔢 <b>STEP 2/4: SELECT NUMBER OF VC IDs</b>\n\n"
        f"{pe('LINK')} <b>Target:</b> <code>{esc_html(link)}</code>\n"
        f"🟢 <b>Max Available:</b> <code>{vc_engine.available_count()} IDs</code>\n"
        f"🤖 <b>Selected:</b> <code>_</code></blockquote>",
        reply_markup=get_numpad("qty", "start_vc_flow", "🔙 Back"),
    )


@dp.callback_query(VCJoinFlow.waiting_for_quantity, F.data.startswith("qty_"))
async def vc_qty_numpad_handler(callback: types.CallbackQuery, state: FSMContext):
    act = callback.data.split("_")[1]
    data = await state.get_data()
    val = data.get("qty_input", "")

    if act == "clear":
        val = ""
    elif act == "submit":
        max_avail = vc_engine.available_count()
        if not val or int(val) < 1 or int(val) > max_avail:
            return await safe_answer(callback, f"⚠️ 1 se {max_avail} ke beech enter karein!", show_alert=True)
        await state.update_data(time_input="")
        await state.set_state(VCJoinFlow.waiting_for_time)
        await safe_answer(callback)
        return await safe_edit(
            callback.message,
            f"<blockquote>{pe('TIMER')} <b>STEP 3/4: SELECT VC DURATION (MINUTES)</b>\n\n"
            f"🤖 <b>Selected IDs:</b> <code>{val}</code>\n"
            f"⏱️ <b>Duration:</b> <code>_ mins</code></blockquote>",
            reply_markup=get_numpad("time", "vc_back_to_step2", "🔙 Back"),
        )
    elif len(val) < 3:
        val += act

    await state.update_data(qty_input=val)
    await safe_answer(callback)
    await safe_edit(
        callback.message,
        f"<blockquote>🔢 <b>STEP 2/4: SELECT NUMBER OF VC IDs</b>\n\n"
        f"{pe('LINK')} <b>Target:</b> <code>{esc_html(data.get('invite_link', ''))}</code>\n"
        f"🟢 <b>Max Available:</b> <code>{vc_engine.available_count()} IDs</code>\n"
        f"🤖 <b>Selected:</b> <code>{val or '_'}</code></blockquote>",
        reply_markup=get_numpad("qty", "start_vc_flow", "🔙 Back"),
    )


@dp.callback_query(VCJoinFlow.waiting_for_time, F.data.startswith("time_"))
async def vc_time_numpad_handler(callback: types.CallbackQuery, state: FSMContext):
    act = callback.data.split("_")[1]
    data = await state.get_data()
    val = data.get("time_input", "")

    if act == "clear":
        val = ""
    elif act == "submit":
        if not val or int(val) < 1:
            return await safe_answer(callback, "⚠️ Minimum 1 minute enter karein!", show_alert=True)

        req_qty = int(data["qty_input"])
        uid = callback.from_user.id
        if not is_admin(uid):
            start_fee = max(0.0, get_setting("vc_start_fee", float))
            min_needed = round(start_fee + (req_qty * get_cost_per_id_min()), 2)
            u = get_user(uid)
            bal = u["credits"] if u else 0.0
            if bal < min_needed:
                await safe_answer(callback)
                return await safe_edit(
                    callback.message,
                    f"<blockquote>❌ <b>INSUFFICIENT CREDITS!</b>\n\n"
                    f"Start Fee (<code>{fmt_num(start_fee)} Cr</code>) + 1st Minute ke liye minimum <code>{fmt_num(min_needed)} Cr</code> chahiye.\n"
                    f"🪙 <b>Your Balance:</b> <code>{fmt_num(bal)} Cr</code></blockquote>",
                    reply_markup=make_nav_kb("deposit_menu", "💳 Deposit Now"),
                )

        await state.update_data(time_input=val)
        await state.set_state(VCJoinFlow.waiting_for_launch_mode)
        await safe_answer(callback)
        new_data = await state.get_data()
        text, kb = build_step4_confirm_view(uid, new_data)
        return await safe_edit(callback.message, text, reply_markup=kb)
    elif len(val) < 4:
        val += act

    await state.update_data(time_input=val)
    await safe_answer(callback)
    await safe_edit(
        callback.message,
        f"<blockquote>{pe('TIMER')} <b>STEP 3/4: SELECT VC DURATION (MINUTES)</b>\n\n"
        f"🤖 <b>Selected IDs:</b> <code>{data.get('qty_input')}</code>\n"
        f"⏱️ <b>Duration:</b> <code>{val or '_'} mins</code></blockquote>",
        reply_markup=get_numpad("time", "vc_back_to_step2", "🔙 Back"),
    )





@dp.callback_query(VCJoinFlow.waiting_for_launch_mode, F.data == "pre_toggle_dyn")
async def pre_launch_toggle_dyn(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    new_dyn = not data.get("dynamic_mode", False)
    await state.update_data(dynamic_mode=new_dyn)
    await safe_answer(callback, "🔄 Dynamic Joining ON (10s Cycle)" if new_dyn else "🔴 Dynamic Joining OFF")
    text, kb = build_step4_confirm_view(callback.from_user.id, await state.get_data())
    await safe_edit(callback.message, text, reply_markup=kb)


@dp.callback_query(F.data.startswith("canceljoin_"))
async def cancel_mid_joining_cb(callback: types.CallbackQuery):
    token = callback.data.replace("canceljoin_", "")
    sess = vc_engine.joining_sessions.get(token)
    if not sess:
        return await safe_answer(callback, "⚠️ Joining already finished!", show_alert=True)
    if sess["user_id"] != callback.from_user.id and not is_admin(callback.from_user.id):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    sess["cancelled"] = True
    await safe_answer(callback, "🛑 Cancelling joining & removing already joined IDs...", show_alert=True)


@dp.callback_query(VCJoinFlow.waiting_for_launch_mode, F.data == "vcmode_instant")
async def execute_instant_vc_order(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    data = await state.get_data()
    req_qty = int(data["qty_input"])
    time_mins = int(data["time_input"])
    link = data["invite_link"]
    mic_muted = get_global_mic_muted()
    dyn_mode = data.get("dynamic_mode", False)
    uid = callback.from_user.id
    await state.clear()

    job_id, actual_joined, err_reason, was_cancelled = await vc_engine.dispatch_vc_order(
        uid,
        link,
        req_qty,
        time_mins,
        mic_muted=mic_muted,
        dynamic_mode=dyn_mode,
        progress_msg=callback.message,
    )

    if was_cancelled:
        return await safe_edit(
            callback.message,
            f"<blockquote>🛑 <b>JOINING CANCELLED!</b>\n\n"
            f"Jo IDs beech mein join hui thi sabko safely VC se hata diya gaya hai. Koi credit nahi kata!</blockquote>",
            reply_markup=make_nav_kb("start_vc_flow", "🚀 New VC Order"),
        )

    if actual_joined == 0:
        fine_amt = get_setting("vc_request_fine", float) if not is_admin(uid) else 0.0
        is_req_fail = any(
            k in (err_reason or "").lower() for k in ["request", "approve", "invite_request"]
        )
        if is_req_fail and fine_amt > 0:
            cursor.execute(
                "UPDATE users SET credits = MAX(0, credits - ?), fines_paid = fines_paid + ? WHERE user_id = ?",
                (fine_amt, fine_amt, uid),
            )
            db_conn.commit()
            await send_bot_log(
                "VC_REQUEST_FINE",
                pe("MINUS"),
                user=callback.from_user,
                fields=[
                    ("Target", f"<code>{esc_html(link)}</code>"),
                    ("Fine Deducted", f"<code>-{fmt_num(fine_amt)} Cr</code>"),
                ],
            )
            return await safe_edit(
                callback.message,
                f"<blockquote>{pe('ALERT')} <b>JOIN-REQUEST LINK FAILED — FINE APPLIED!</b>\n\n"
                f"Aapne Direct Join Link ke bajaye Join-Request link diya tha.\n"
                f"{pe('FINE')} <b>Fine Deducted:</b> <code>-{fmt_num(fine_amt)} Cr</code></blockquote>",
                reply_markup=make_nav_kb("start_vc_flow", "🔙 Try Direct Link"),
            )

        return await safe_edit(
            callback.message,
            f"<blockquote>❌ <b>VC Join Failed (No Credits Charged)</b>\n\n"
            f"Reason: <code>{esc_html(err_reason)}</code></blockquote>",
            reply_markup=make_nav_kb("start_vc_flow", "🔙 Try Again"),
        )

    # Open Live VC Control Panel directly!
    text, kb = build_live_vc_control_view(job_id)
    if text:
        await safe_edit(callback.message, text, reply_markup=kb)


@dp.callback_query(VCJoinFlow.waiting_for_launch_mode, F.data == "vcmode_schedule")
async def ask_schedule_ist_time(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    await state.set_state(VCJoinFlow.waiting_for_schedule_ist)
    await safe_edit(
        callback.message,
        f"<blockquote>📅 <b>SCHEDULE FUTURE VC (IST)</b>\n\n"
        f"🕒 <b>Current IST:</b> <code>{now_ist().strftime('%d-%m-%Y %H:%M')}</code>\n\n"
        f"Send start time in 24-hr format:\n"
        f"• Today/Tomorrow: <code>HH:MM</code> (e.g. <code>21:30</code>)\n"
        f"• Specific Date: <code>DD-MM-YYYY HH:MM</code></blockquote>",
        reply_markup=make_nav_kb("vc_back_to_step2", "🔙 Back"),
    )


@dp.message(VCJoinFlow.waiting_for_schedule_ist)
async def process_schedule_ist_input(message: types.Message, state: FSMContext):
    raw = (message.text or "").strip()
    now_dt = now_ist()
    try:
        if len(raw) <= 5 and ":" in raw:
            hh, mm = map(int, raw.split(":"))
            target_dt = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target_dt <= now_dt:
                target_dt += timedelta(days=1)
        else:
            target_dt = datetime.strptime(raw, "%d-%m-%Y %H:%M").replace(tzinfo=IST)
    except Exception:
        return await message.reply(
            "<blockquote>❌ Invalid format! Use <code>HH:MM</code> or <code>DD-MM-YYYY HH:MM</code>.</blockquote>",
            parse_mode="HTML",
        )

    delay = (target_dt - now_dt).total_seconds()
    if delay < 15:
        return await message.reply("<blockquote>⚠️ Kam se kam 15 second baad ka future IST time dalein!</blockquote>", parse_mode="HTML")

    data = await state.get_data()
    uid = message.from_user.id
    await state.clear()

    target_str = target_dt.strftime("%d-%m-%Y %I:%M %p IST")
    sid = vc_engine.schedule_future_vc_order(
        uid,
        data["invite_link"],
        int(data["qty_input"]),
        int(data["time_input"]),
        get_global_mic_muted(),
        data.get("dynamic_mode", False),
        delay,
        target_str,
    )
    await message.reply(
        f"<blockquote>{pe('CHECK')} <b>VC SCHEDULED SUCCESSFULLY!</b>\n\n"
        f"🆔 <b>Schedule ID:</b> <code>{sid}</code>\n"
        f"🕒 <b>Start Time:</b> <code>{target_str}</code>\n"
        f"<i>Starting fee aur per-min billing VC start hone par kategi.</i></blockquote>",
        reply_markup=make_nav_kb("my_active_orders", "📊 View Scheduled"),
        parse_mode="HTML",
    )


# =====================================================================
# 16. REDEEM PROMO CODE
# =====================================================================
@dp.callback_query(F.data == "redeem_menu")
async def redeem_menu_cb(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    await state.set_state(RedeemFlow.waiting_for_code)
    await safe_edit(
        callback.message,
        f"<blockquote>{pe('PROMO')} <b>REDEEM PROMO CODE</b>\n\nApna gift/promo code yahan send karein:</blockquote>",
        reply_markup=make_nav_kb(),
    )


@dp.message(RedeemFlow.waiting_for_code)
async def redeem_code_process(message: types.Message, state: FSMContext):
    code = (message.text or "").strip().upper()
    uid = message.from_user.id
    cursor.execute("SELECT * FROM promo_codes WHERE code = ?", (code,))
    p = cursor.fetchone()
    if not p or p["current_uses"] >= p["max_uses"]:
        return await message.reply("<blockquote>❌ Invalid ya Expired Promo Code!</blockquote>", reply_markup=make_nav_kb(), parse_mode="HTML")

    cursor.execute("SELECT 1 FROM promo_claims WHERE code = ? AND user_id = ?", (code, uid))
    if cursor.fetchone():
        return await message.reply("<blockquote>⚠️ Aap yeh promo code pehle hi claim kar chuke hain!</blockquote>", reply_markup=make_nav_kb(), parse_mode="HTML")

    cursor.execute("INSERT INTO promo_claims (code, user_id, claimed_at) VALUES (?, ?, ?)", (code, uid, full_date_ist()))
    cursor.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code = ?", (code,))
    cursor.execute("UPDATE users SET credits = credits + ? WHERE user_id = ?", (p["amount"], uid))
    db_conn.commit()
    await state.clear()

    await send_bot_log(
        "PROMO_CLAIMED",
        pe("STAR"),
        user=message.from_user,
        fields=[("Code", f"<code>{code}</code>"), ("Credits", f"<code>+{fmt_num(p['amount'])} Cr</code>")],
    )
    await message.reply(
        f"<blockquote>🎉 <b>PROMO REDEEMED!</b>\n🪙 <b>+{fmt_num(p['amount'])} Credits</b> aapke wallet mein add ho gaye hain!</blockquote>",
        reply_markup=make_nav_kb(),
        parse_mode="HTML",
    )

# =====================================================================
# 17. ROLE-BASED SUPER ADMIN CONTROL ROOM (POWER-FILTERED UI)
# =====================================================================
async def cleanup_pending_login(admin_id: int):
    info = pending_logins.pop(admin_id, None)
    if info and info.get("client"):
        try:
            await info["client"].disconnect()
        except Exception:
            pass


@dp.callback_query(F.data == "admin_panel")
async def admin_panel_cb(callback: types.CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not is_admin(uid):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    await safe_answer(callback)
    await cleanup_pending_login(uid)
    await state.clear()

    cursor.execute("SELECT COUNT(*) as c FROM users")
    u_cnt = cursor.fetchone()["c"]
    cursor.execute("SELECT SUM(amount) as r FROM payments WHERE status = 'paid'")
    rev = cursor.fetchone()["r"] or 0.0
    cursor.execute("SELECT COUNT(*) as c FROM vc_sessions")
    sess_total = cursor.fetchone()["c"]

    ref_st = "🟢 ON" if get_setting("referral_enabled", int) == 1 else "🔴 OFF"
    b_st = "🟢 ON" if get_setting("bonus_enabled", int) == 1 else "🔴 OFF"
    logo_st = "🟢 ON" if get_setting("brand_logo_enabled", int) == 1 else "🔴 OFF"
    start_fee = fmt_num(get_setting("vc_start_fee", float))
    role_badge = "👑 SUPER OWNER" if is_super_owner(uid) else f"👮 ADMIN ({len(get_admin_powers(uid))} Powers)"

    text = (
        f"<blockquote>{pe('ADMIN')} <b>ADMIN CONTROL ROOM ({role_badge})</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{pe('CROWN')} <b>Super Owner ID:</b> <code>{get_super_owner_id()}</code>\n"
        f"👥 <b>Total Users:</b> <code>{u_cnt}</code> | 💵 <b>Revenue:</b> <code>₹{fmt_num(rev)}/-</code>\n"
        f"📱 <b>VC Pool:</b> <code>{vc_engine.available_count()}/{sess_total} Online</code> "
        f"(<code>{vc_engine.total_active_streams()} Live VCs</code>)\n"
        f"💸 <b>Start Fee:</b> <code>{start_fee} Cr</code> | ⚖️ <b>Fine:</b> <code>{fmt_num(get_setting('vc_request_fine', float))} Cr</code> | 🎁 <b>Ref:</b> <code>{ref_st}</code>\n"
        f"📢 <b>F-Sub:</b> <code>{len(get_fsub_targets())}</code> | ⏰ <b>Backup:</b> <code>{get_setting('auto_backup_mins', int)}m</code> | 🖼️ <b>Logo:</b> <code>{logo_st}</code></blockquote>"
    )

    # Dynamically show ONLY buttons that this admin has power to control!
    kb_rows: list[list[InlineKeyboardButton]] = []

    if has_power(uid, "vc_ids"):
        mic_global_off = get_global_mic_muted()
        kb_rows.append([
            InlineKeyboardButton(text="📱 Login VC ID", callback_data="admin_phone_login", style="success"),
            InlineKeyboardButton(text="🗂️ Manage VC IDs", callback_data="admin_manage_ids", style="primary"),
        ])
        kb_rows.append([
            InlineKeyboardButton(text=f"🔴 Live VCs ({len(vc_engine.active_jobs)})", callback_data="admin_live_vcs", style="primary"),
            InlineKeyboardButton(text="🩺 Ping VC IDs", callback_data="admin_ping_sessions_now", style="primary"),
        ])
        kb_rows.append([
            InlineKeyboardButton(
                text="🔇 VC Mic: ALWAYS OFF" if mic_global_off else "🎙️ VC Mic: ALWAYS ON",
                callback_data="admin_toggle_global_mic",
                style="danger" if mic_global_off else "success",
            ),
        ])

    if has_power(uid, "fsub"):
        kb_rows.append([
            InlineKeyboardButton(text="➕ Add Channel", callback_data="admin_fsub_ch_menu", style="success"),
            InlineKeyboardButton(text="➕ Add Group", callback_data="admin_fsub_grp_menu", style="success"),
        ])
        kb_rows.append([
            InlineKeyboardButton(text="🗂️ Manage F-Sub", callback_data="admin_manage_fsub", style="primary"),
        ])

    if is_super_owner(uid):
        kb_rows.append([
            InlineKeyboardButton(text="👮 Admins & Powers", callback_data="admin_manage_admins", style="primary"),
        ])

    ref_fine_row = []
    if has_power(uid, "referral"):
        ref_fine_row.append(
            InlineKeyboardButton(text=f"🎁 Referral & VIP ({ref_st})", callback_data="admin_ref_menu", style="success")
        )
    if has_power(uid, "fine_setup"):
        ref_fine_row.append(
            InlineKeyboardButton(text="⚖️ Fine Setup", callback_data="admin_fine_setup", style="primary")
        )
    if ref_fine_row:
        kb_rows.append(ref_fine_row)

    user_cr_row = []
    if has_power(uid, "user_lookup"):
        user_cr_row.append(
            InlineKeyboardButton(text="🔍 User Lookup", callback_data="admin_user_lookup", style="success")
        )
    if has_power(uid, "credits"):
        user_cr_row.append(
            InlineKeyboardButton(text="💰 Add/Cut Credits", callback_data="admin_mod_credits", style="primary")
        )
    if user_cr_row:
        kb_rows.append(user_cr_row)

    if has_power(uid, "pricing"):
        kb_rows.append([
            InlineKeyboardButton(text="💱 Credit Price", callback_data="admin_set_credit_price", style="success"),
            InlineKeyboardButton(text="⚙️ VC Ratio", callback_data="admin_set_ratio", style="primary"),
        ])
        kb_rows.append([
            InlineKeyboardButton(text=f"💸 Start Fee ({start_fee} Cr)", callback_data="admin_set_start_fee", style="primary"),
            InlineKeyboardButton(text=f"🎁 Welcome Gift ({b_st})", callback_data="admin_edit_bonus_amt", style="success"),
        ])
        kb_rows.append([
            InlineKeyboardButton(text="🎟️ Promo Codes", callback_data="admin_promo_menu", style="primary"),
        ])

    if has_power(uid, "logs_backup"):
        kb_rows.append([
            InlineKeyboardButton(text="🖼️ Receipt Logo", callback_data="admin_brand_logo_menu", style="primary"),
            InlineKeyboardButton(text="📡 Set Log Channel", callback_data="admin_set_log_channel", style="success"),
        ])
        kb_rows.append([
            InlineKeyboardButton(text="💾 Get Full Memory", callback_data="admin_get_memory", style="success"),
            InlineKeyboardButton(text="♻️ Revive Memory", callback_data="admin_revive_memory", style="danger"),
        ])
        kb_rows.append([
            InlineKeyboardButton(text="⏰ Auto-Backup", callback_data="admin_auto_backup", style="primary"),
        ])

    misc_row = []
    if has_power(uid, "emojis"):
        misc_row.append(InlineKeyboardButton(text="🎨 Edit Emoji", callback_data="admin_edit_emojis_0", style="primary"))
    if has_power(uid, "broadcast"):
        misc_row.append(InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast", style="primary"))
    if misc_row:
        kb_rows.append(misc_row)

    export_health_row = []
    if has_power(uid, "user_lookup"):
        export_health_row.append(InlineKeyboardButton(text="📋 All Users (.txt)", callback_data="admin_all_users_txt", style="success"))
    if has_power(uid, "vc_ids"):
        export_health_row.append(InlineKeyboardButton(text="🩺 Health Interval", callback_data="admin_health_interval", style="primary"))
    if export_health_row:
        kb_rows.append(export_health_row)

    # Admin Guide is visible to every admin (filtered dynamically to their own powers!)
    kb_rows.append([InlineKeyboardButton(text="📘 My Admin Guide", callback_data="admin_guide", style="primary")])

    if is_super_owner(uid):
        kb_rows.append([
            InlineKeyboardButton(text="👑 Transfer Ownership", callback_data="admin_transfer_owner", style="danger"),
            InlineKeyboardButton(text="🗑️ Delete All Data", callback_data="admin_delete_all_data", style="danger"),
        ])

    kb_rows.append([InlineKeyboardButton(text="🏠 Back to Home", callback_data="main_menu", style="danger")])
    await safe_edit(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


# --- 17.0 GLOBAL VC MIC ALWAYS ON / OFF TOGGLE (ADMIN ONLY) ---
@dp.callback_query(F.data == "admin_toggle_global_mic")
async def admin_toggle_global_mic_cb(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "vc_ids"):
        return await safe_answer(callback, "❌ No VC IDs Power!", show_alert=True)
    new_muted = not get_global_mic_muted()
    set_setting("global_mic_muted", 1 if new_muted else 0)
    await vc_engine.set_all_jobs_mic_state(new_muted)
    await safe_answer(
        callback,
        "🔇 Global VC Mic set to ALWAYS OFF (All live & future VCs muted)!" if new_muted else "🎙️ Global VC Mic set to ALWAYS ON!",
        show_alert=True,
    )
    await admin_panel_cb(callback, state)


# --- 17.1 FIXED STARTING FEE SETUP ---
@dp.callback_query(F.data == "admin_set_start_fee")
async def admin_ask_start_fee(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "pricing"):
        return await safe_answer(callback, "❌ No Pricing Power!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_start_fee)
    await safe_edit(
        callback.message,
        f"<blockquote>{pe('MONEY')} <b>VC FIXED STARTING FEE SETUP</b>\n\n"
        f"Current Starting Fee: <code>{fmt_num(get_setting('vc_start_fee', float))} Credits</code>\n\n"
        f"Har VC order start hone par kitne fixed credits katenge? Naya number bhejein (<code>0</code> = No Start Fee):</blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_start_fee)
async def admin_save_start_fee(message: types.Message, state: FSMContext):
    try:
        val = max(0.0, float((message.text or "").strip()))
        set_setting("vc_start_fee", val)
        await state.clear()
        await message.reply(
            f"<blockquote>✅ VC Starting Fee updated to <code>{fmt_num(val)} Cr</code>!</blockquote>",
            reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
            parse_mode="HTML",
        )
    except Exception:
        await message.reply("<blockquote>❌ Valid number bhejein!</blockquote>", parse_mode="HTML")


# --- 17.2 DELETE ALL DATA (7872 NUMPAD + BACKUP RECOVERABLE) ---
@dp.callback_query(F.data == "admin_delete_all_data")
async def admin_ask_wipe_code(callback: types.CallbackQuery, state: FSMContext):
    if not is_super_owner(callback.from_user.id):
        return await safe_answer(callback, "❌ Sirf Super Owner data wipe kar sakta hai!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_wipe_code)
    await state.update_data(wipe_code="")
    await safe_edit(
        callback.message,
        f"<blockquote>{pe('ALERT')} <b>DANGER ZONE: DELETE ALL BOT DATA</b>\n\n"
        f"Isse Users, Credits, Payments, VC IDs, F-Sub, Promos, Admins aur History sab clear ho jayega.\n"
        f"<i>(Wipe se pehle Log Channel mein ek safety backup bheja jayega, aur aap kabhi bhi ♻️ Revive Memory se `.json` backup upload karke 100% recover kar sakte hain.)</i>\n\n"
        f"🔐 Confirm karne ke liye keypad se <b><code>7872</code></b> enter karein:\n"
        f"⌨️ <b>Entered Code:</b> <code>_</code></blockquote>",
        reply_markup=get_numpad("wipe", "admin_panel", "🔙 Cancel"),
    )


@dp.callback_query(AdminFlow.waiting_for_wipe_code, F.data.startswith("wipe_"))
async def admin_wipe_numpad(callback: types.CallbackQuery, state: FSMContext):
    if not is_super_owner(callback.from_user.id):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)

    act = callback.data.split("_")[1]
    val = (await state.get_data()).get("wipe_code", "")

    if act == "clear":
        val = ""
    elif act == "submit":
        if val != "7872":
            return await safe_answer(callback, "❌ Galat Code! Confirm karne ke liye 7872 dalein.", show_alert=True)

        await safe_answer(callback, "🗑️ Wiping all bot data...")
        await send_memory_backup_to_log_channel("PRE_WIPE_SAFETY_BACKUP")

        owner_id = get_super_owner_id()
        owner_uname = get_owner_username()
        log_cid = get_setting("log_channel_id", int)
        log_title = get_setting("log_channel_title", str)

        await vc_engine.shutdown_all()

        for tbl in BACKUP_TABLES:
            cursor.execute(f"DELETE FROM {tbl}")

        db_conn.commit()
        init_db()
        set_setting("super_owner_id", owner_id)
        set_setting("owner_username", owner_uname)
        if log_cid:
            set_setting("log_channel_id", log_cid)
            set_setting("log_channel_title", log_title)

        all_p = json.dumps([p[0] for p in ALL_ADMIN_POWERS])
        cursor.execute(
            "INSERT OR REPLACE INTO admins (user_id, powers, added_by, added_at) VALUES (?, ?, ?, ?)",
            (owner_id, all_p, owner_id, full_date_ist()),
        )
        db_conn.commit()
        await state.clear()

        return await safe_edit(
            callback.message,
            f"<blockquote>{pe('CHECK')} <b>ALL BOT DATA WIPED SUCCESSFULLY!</b>\n\n"
            f"Bot bilkul fresh state mein aa gaya hai (Super Owner <code>{owner_id}</code> safe hai).\n"
            f"♻️ Data wapas laane ke liye <b>Revive Memory</b> mein apni `.json` backup file bhejein.</blockquote>",
            reply_markup=make_nav_kb("admin_panel", "🔙 Back to Admin Panel"),
        )
    elif len(val) < 4:
        val += act

    await state.update_data(wipe_code=val)
    await safe_answer(callback)
    await safe_edit(
        callback.message,
        f"<blockquote>{pe('ALERT')} <b>DANGER ZONE: DELETE ALL BOT DATA</b>\n\n"
        f"🔐 Confirm karne ke liye keypad se <b><code>7872</code></b> enter karein:\n"
        f"⌨️ <b>Entered Code:</b> <code>{val or '_'}</code></blockquote>",
        reply_markup=get_numpad("wipe", "admin_panel", "🔙 Cancel"),
    )


# --- 17.3 REFERRAL & VIP SETTINGS ---
@dp.callback_query(F.data == "admin_ref_menu")
async def admin_ref_menu_cb(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "referral"):
        return await safe_answer(callback, "❌ No Referral Settings Power!", show_alert=True)
    await safe_answer(callback)
    await state.clear()

    on = get_setting("referral_enabled", int) == 1
    ref_bonus = fmt_num(get_setting("ref_bonus", float))
    vip_goal = get_setting("vip_ref_target", int)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Referral Program: {'🟢 ON' if on else '🔴 OFF'}",
                    callback_data="admin_toggle_ref",
                    style="success" if on else "danger",
                )
            ],
            [
                InlineKeyboardButton(text=f"💰 Per Ref ({ref_bonus} Cr)", callback_data="admin_set_ref_bonus", style="primary"),
                InlineKeyboardButton(text=f"🏆 VIP Goal ({vip_goal} Refs)", callback_data="admin_set_vip_target", style="primary"),
            ],
            *get_nav_rows("admin_panel", "🔙 Back to Admin Panel"),
        ]
    )
    await safe_edit(
        callback.message,
        f"<blockquote>{pe('BOLT')} <b>REFERRAL &amp; VIP CONTROL PANEL</b>\n\n"
        f"• <b>Status:</b> <code>{'ON' if on else 'OFF'}</code>\n"
        f"• <b>Per Verified Referral:</b> <code>+{ref_bonus} Credits</code>\n"
        f"• <b>VIP Unlock Target:</b> <code>{vip_goal} Referrals</code>\n\n"
        f"<i>Note: Referral tabhi count aur credit hoga jab naya user F-Sub verify kar lega.</i></blockquote>",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "admin_toggle_ref")
async def admin_toggle_ref_cb(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "referral"):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    set_setting("referral_enabled", 0 if get_setting("referral_enabled", int) == 1 else 1)
    await admin_ref_menu_cb(callback, state)


@dp.callback_query(F.data == "admin_set_ref_bonus")
async def admin_ask_ref_bonus(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "referral"):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_ref_bonus)
    await safe_edit(
        callback.message,
        f"<blockquote>💰 <b>SET PER-REFERRAL CREDIT</b>\n\nCurrent: <code>{fmt_num(get_setting('ref_bonus', float))} Cr</code>\nNaya amount bhejein:</blockquote>",
        reply_markup=make_nav_kb("admin_ref_menu", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_ref_bonus)
async def admin_save_ref_bonus(message: types.Message, state: FSMContext):
    try:
        val = max(0.0, float((message.text or "").strip()))
        set_setting("ref_bonus", val)
        await state.clear()
        await message.reply(
            f"<blockquote>✅ Per-referral bonus updated to <code>+{fmt_num(val)} Cr</code>!</blockquote>",
            reply_markup=make_nav_kb("admin_ref_menu", "🔙 Back"),
            parse_mode="HTML",
        )
    except Exception:
        await message.reply("<blockquote>❌ Valid number bhejein!</blockquote>", parse_mode="HTML")


@dp.callback_query(F.data == "admin_set_vip_target")
async def admin_ask_vip_target(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "referral"):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_vip_target)
    await safe_edit(
        callback.message,
        f"<blockquote>🏆 <b>SET VIP UNLOCK REFERRAL GOAL</b>\n\nCurrent: <code>{get_setting('vip_ref_target', int)} Referrals</code>\nNaya target number bhejein:</blockquote>",
        reply_markup=make_nav_kb("admin_ref_menu", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_vip_target)
async def admin_save_vip_target(message: types.Message, state: FSMContext):
    try:
        val = max(1, int((message.text or "").strip()))
        set_setting("vip_ref_target", val)
        await state.clear()
        await message.reply(
            f"<blockquote>✅ VIP unlock goal updated to <code>{val} Referrals</code>!</blockquote>",
            reply_markup=make_nav_kb("admin_ref_menu", "🔙 Back"),
            parse_mode="HTML",
        )
    except Exception:
        await message.reply("<blockquote>❌ Valid integer bhejein (minimum 1)!</blockquote>", parse_mode="HTML")


# --- 17.4 VC REQUEST-LINK FINE SETUP ---
@dp.callback_query(F.data == "admin_fine_setup")
async def admin_fine_setup_cb(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "fine_setup"):
        return await safe_answer(callback, "❌ No Fine Setup Power!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_fine_amount)
    await safe_edit(
        callback.message,
        f"<blockquote>{pe('FINE')} <b>VC JOIN-REQUEST FINE SETUP</b>\n\n"
        f"Current Fine: <code>{fmt_num(get_setting('vc_request_fine', float))} Credits</code>\n\n"
        f"Join-Request link fail hone par kitne credits fine katega? Number bhejein (<code>0</code> = OFF):</blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_fine_amount)
async def admin_save_fine(message: types.Message, state: FSMContext):
    try:
        val = max(0.0, float((message.text or "").strip()))
        set_setting("vc_request_fine", val)
        await state.clear()
        await message.reply(
            f"<blockquote>✅ Join-Request Fine updated to <code>{fmt_num(val)} Cr</code>!</blockquote>",
            reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
            parse_mode="HTML",
        )
    except Exception:
        await message.reply("<blockquote>❌ Valid number bhejein!</blockquote>", parse_mode="HTML")


# --- 17.5 COMPLETE PAGINATED EDIT EMOJI + PREMIUM CUSTOM EMOJI / STICKER FIX ---
EMOJIS_PER_PAGE = 10


@dp.callback_query(F.data.startswith("admin_edit_emojis"))
async def admin_edit_emojis_cb(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "emojis"):
        return await safe_answer(callback, "❌ No Edit Emoji Power!", show_alert=True)
    await safe_answer(callback)
    await state.clear()

    parts = callback.data.split("_")
    page = int(parts[-1]) if parts[-1].isdigit() else 0

    all_items = list(DEFAULT_EMOJIS.items())
    total_pages = max(1, (len(all_items) + EMOJIS_PER_PAGE - 1) // EMOJIS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    chunk = all_items[page * EMOJIS_PER_PAGE : (page + 1) * EMOJIS_PER_PAGE]

    rows = []
    for k, (_, loc) in chunk:
        rows.append([
            InlineKeyboardButton(
                text=f"✏️ {k} — {loc}",
                callback_data=f"editemoji_{k}_{page}",
                style="primary",
            )
        ])

    nav_p = []
    if page > 0:
        nav_p.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"admin_edit_emojis_{page - 1}", style="primary"))
    if page < total_pages - 1:
        nav_p.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"admin_edit_emojis_{page + 1}", style="primary"))
    if nav_p:
        rows.append(nav_p)

    rows.append([InlineKeyboardButton(text="♻️ Reset All Emojis to Default", callback_data="reset_all_emojis", style="danger")])
    rows.extend(get_nav_rows("admin_panel", "🔙 Back to Admin Panel"))

    await safe_edit(
        callback.message,
        f"<blockquote>🎨 <b>EDIT ALL BOT EMOJIS (Page {page + 1}/{total_pages})</b>\n\n"
        f"Total Editable Locations: <code>{len(all_items)}</code>\n"
        f"Kisi bhi button par click karke normal emoji ya <b>Telegram Premium Custom Emoji / Sticker</b> seedha bhej kar set karein:</blockquote>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@dp.callback_query(F.data == "reset_all_emojis")
async def admin_reset_all_emojis_cb(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "emojis"):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    for ek, (eval_default, _) in DEFAULT_EMOJIS.items():
        set_setting(f"emoji_{ek}", eval_default)
    await safe_answer(callback, "✅ All emojis reset to default!", show_alert=True)
    fake_cb = callback.model_copy(update={"data": "admin_edit_emojis_0"})
    await admin_edit_emojis_cb(fake_cb, state)


@dp.callback_query(F.data.startswith("editemoji_"))
async def admin_ask_new_emoji(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "emojis"):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    await safe_answer(callback)
    raw = callback.data.replace("editemoji_", "")
    # Split off trailing page index
    ekey, page_s = raw.rsplit("_", 1)
    loc_desc = DEFAULT_EMOJIS.get(ekey, ("✨", "Bot UI"))[1]
    await state.update_data(edit_emoji_key=ekey, edit_emoji_page=page_s)
    await state.set_state(AdminFlow.waiting_for_emoji_value)
    await safe_edit(
        callback.message,
        f"<blockquote>🎨 <b>CHANGE EMOJI: {ekey}</b>\n\n"
        f"📍 <b>Location:</b> {loc_desc}\n"
        f"🔹 <b>Current:</b> {pe(ekey)}\n\n"
        f"Ab naya <b>Telegram Premium Custom Emoji</b> (seedha emoji keyboard se), custom emoji sticker, ya normal emoji bhejein — bot automatically uski <code>custom_emoji_id</code> save karega!</blockquote>",
        reply_markup=make_nav_kb(f"admin_edit_emojis_{page_s}", "🔙 Back to Emoji List"),
    )


@dp.message(AdminFlow.waiting_for_emoji_value)
async def admin_save_new_emoji(message: types.Message, state: FSMContext):
    """
    Preserves Telegram Premium Custom Emojis & Custom Emoji Stickers by extracting
    custom_emoji_id from message.sticker or message.entities instead of falling back to plain text!
    """
    data = await state.get_data()
    ekey = data.get("edit_emoji_key")
    page_s = data.get("edit_emoji_page", "0")
    if not ekey:
        await state.clear()
        return

    new_val = ""
    # 1. Custom emoji sticker sent directly
    if message.sticker and getattr(message.sticker, "custom_emoji_id", None):
        fallback = message.sticker.emoji or "✨"
        new_val = f'<tg-emoji emoji-id="{message.sticker.custom_emoji_id}">{fallback}</tg-emoji>'
    # 2. Premium custom emoji inside message entities
    elif message.entities:
        custom_ent = next(
            (e for e in message.entities if e.type == "custom_emoji" and getattr(e, "custom_emoji_id", None)),
            None,
        )
        if custom_ent:
            html_val = (message.html_text or "").strip()
            if "<tg-emoji" in html_val:
                new_val = html_val
            else:
                fallback = (message.text or "✨").strip()
                new_val = f'<tg-emoji emoji-id="{custom_ent.custom_emoji_id}">{fallback}</tg-emoji>'
        else:
            new_val = (message.text or "").strip()
    # 3. Normal emoji or manually typed <tg-emoji> tag
    elif message.text:
        new_val = message.text.strip()

    if not new_val:
        return await message.reply(
            "<blockquote>❌ Valid emoji ya Telegram Premium custom emoji bhejein!</blockquote>",
            parse_mode="HTML",
        )

    set_setting(f"emoji_{ekey}", new_val)
    await state.clear()
    await message.reply(
        f"<blockquote>✅ Emoji <b>{ekey}</b> updated to {new_val}!\n"
        f"<i>(Saved with full Premium Custom Emoji ID support)</i></blockquote>",
        reply_markup=make_nav_kb(f"admin_edit_emojis_{page_s}", "🔙 Back to Emoji List"),
        parse_mode="HTML",
    )


# --- 17.6 DYNAMIC POWER-BASED ADMIN GUIDE ---
@dp.callback_query(F.data == "admin_guide")
async def admin_guide_cb(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if not is_admin(uid):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    await safe_answer(callback)

    sections = []
    if has_power(uid, "vc_ids"):
        sections.append(
            "📱 <b>VC IDs &amp; Live VCs:</b> <i>Login VC ID</i> se phone+OTP+2FA login karein. Ek ID ek saath alag-alag VCs join kar sakti hai. <i>Live VCs</i> se chalte orders monitor/stop karein aur <i>Ping VC IDs</i> se dead IDs remove karein."
        )
    if has_power(uid, "fsub"):
        sections.append(
            "📢 <b>Force-Subscribe (F-Sub):</b> Public/Private Channel ya Group add karein (Bot ko wahan Admin banayein). User jab tak join nahi karega tab tak bot locked rahega."
        )
    if has_power(uid, "credits"):
        sections.append(
            "💰 <b>Add / Cut Credits:</b> Format <code>@username 100</code> se add aur <code>12345 -50</code> se cut karein. Admins ke liye credits unlimited hain."
        )
    if has_power(uid, "pricing"):
        sections.append(
            "💱 <b>Rates, Start Fee &amp; Promos:</b> <i>Credit Price</i> (₹1 = Cr), <i>VC Ratio</i> (per-min rate), <i>Start Fee</i> (fixed starting charge), <i>Welcome Gift</i> aur <i>Promo Codes</i> manage karein."
        )
    if has_power(uid, "referral"):
        sections.append(
            "🎁 <b>Referral &amp; VIP:</b> Referral ON/OFF karein, per-ref credit aur VIP unlock goal set karein. Reward sirf F-Sub complete hone par milta hai."
        )
    if has_power(uid, "fine_setup"):
        sections.append(
            "⚖️ <b>Fine Setup:</b> Agar user Join-Request link de aur join fail ho toh kitne credits penalty kategi yahan set karein."
        )
    if has_power(uid, "emojis"):
        sections.append(
            "🎨 <b>Edit Emoji:</b> Har location ke button par click karke normal ya Telegram Premium custom emoji seedha bhej kar change karein."
        )
    if has_power(uid, "broadcast"):
        sections.append(
            "📢 <b>Broadcast:</b> Text ya Photo+Caption bhejein, phir <b>Premium Quote</b> ya <b>Simple Text</b> format select karke sabhi users ko bhejein (bina kisi extra Announcement heading ke)."
        )
    if has_power(uid, "logs_backup"):
        sections.append(
            "💾 <b>Logs, Logo &amp; Backup:</b> Log channel forward karke set karein, receipt logo upload karein, aur <i>Get Full Memory</i> / <i>Revive Memory</i> se JSON backup download/restore karein."
        )
    if has_power(uid, "user_lookup"):
        sections.append(
            "🔍 <b>User Lookup &amp; Export:</b> Kisi bhi user ka ID/@username bhej kar full report dekhein ya <i>All Users (.txt)</i> export karein."
        )
    if is_super_owner(uid):
        sections.append(
            "👑 <b>Super Owner Exclusives:</b> <i>Admins &amp; Powers</i> se admins ko granular powers dein, <i>Transfer Ownership</i> se naya Super Owner appoint karein, aur <i>Delete All Data</i> (code: <code>7872</code>) se fresh wipe karein."
        )

    numbered = "\n\n".join(f"{i + 1}. {sec}" for i, sec in enumerate(sections))
    text = f"<blockquote>{pe('GUIDE')} <b>YOUR PERSONALIZED ADMIN GUIDE</b>\n\n{numbered}</blockquote>"
    await safe_edit(callback.message, text, reply_markup=make_nav_kb("admin_panel", "🔙 Back to Admin Panel"))


# --- 17.7 TRANSFER OWNERSHIP ---
@dp.callback_query(F.data == "admin_transfer_owner")
async def admin_ask_transfer_owner(callback: types.CallbackQuery, state: FSMContext):
    if not is_super_owner(callback.from_user.id):
        return await safe_answer(callback, "❌ Sirf Super Owner ownership transfer kar sakta hai!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_transfer_owner)
    await safe_edit(
        callback.message,
        f"<blockquote>{pe('CROWN')} <b>TRANSFER SUPER OWNERSHIP</b>\n\n"
        f"Current Super Owner: <code>{get_super_owner_id()}</code>\n\n"
        f"Naye Super Owner ki <b>Numeric Chat ID</b> ya <b><code>@username</code></b> bhejein:</blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Cancel"),
    )


@dp.message(AdminFlow.waiting_for_transfer_owner)
async def admin_confirm_transfer_owner(message: types.Message, state: FSMContext):
    if not is_super_owner(message.from_user.id):
        return
    u_row = await resolve_target_user(message.text or "")
    if not u_row:
        return await message.reply("<blockquote>❌ User nahi mila! Sahi ID ya @username bhejein.</blockquote>", parse_mode="HTML")

    new_oid = u_row["user_id"]
    uname = u_row["username"] or u_row["first_name"] or str(new_oid)
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ Confirm Transfer to {new_oid}", callback_data=f"confirm_owner_{new_oid}", style="danger")],
            *get_nav_rows("admin_panel", "🔙 Cancel"),
        ]
    )
    await message.reply(
        f"<blockquote>⚠️ <b>FINAL CONFIRMATION</b>\n\n"
        f"Kya aap pakka Super Ownership <b>@{esc_html(uname)}</b> (<code>{new_oid}</code>) ko transfer karna chahte hain?</blockquote>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("confirm_owner_"))
async def admin_execute_transfer_owner(callback: types.CallbackQuery):
    if not is_super_owner(callback.from_user.id):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)

    new_oid = int(callback.data.replace("confirm_owner_", ""))
    u_row = get_user(new_oid)
    set_setting("super_owner_id", new_oid)
    if u_row and u_row["username"]:
        set_setting("owner_username", u_row["username"])

    all_p = json.dumps([p[0] for p in ALL_ADMIN_POWERS])
    cursor.execute(
        "INSERT OR REPLACE INTO admins (user_id, powers, added_by, added_at) VALUES (?, ?, ?, ?)",
        (new_oid, all_p, callback.from_user.id, full_date_ist()),
    )
    db_conn.commit()

    await send_bot_log(
        "OWNERSHIP_TRANSFERRED",
        pe("CROWN"),
        user=callback.from_user,
        fields=[("New Super Owner", f"<code>{new_oid}</code>")],
        extra_id_tags=[new_oid],
    )
    try:
        await bot.send_message(
            new_oid,
            f"<blockquote>{pe('CROWN')} <b>CONGRATULATIONS!</b>\nAapko is bot ka naya <b>Super Owner</b> appoint kiya gaya hai (Full Powers Unlocked)!</blockquote>",
            parse_mode="HTML",
        )
    except Exception:
        pass

    await safe_answer(callback, "👑 Ownership Transferred!", show_alert=True)
    await safe_edit(
        callback.message,
        f"<blockquote>{pe('CROWN')} <b>OWNERSHIP TRANSFERRED!</b>\nNaya Super Owner ID: <code>{new_oid}</code></blockquote>",
        reply_markup=make_nav_kb("main_menu", "🏠 Home"),
    )


# --- 17.8 GRANULAR ADMIN POWERS & MANAGE ADMINS ---
def build_admin_powers_kb(target_uid: int, selected: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for pkey, plbl in ALL_ADMIN_POWERS:
        mark = "✅" if pkey in selected else "❌"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {plbl}",
                callback_data=f"togpow_{target_uid}_{pkey}",
                style="success" if pkey in selected else "danger",
            )
        ])
    rows.append([
        InlineKeyboardButton(text="✅ Select All", callback_data=f"allpow_{target_uid}", style="success"),
        InlineKeyboardButton(text="💾 Save Powers", callback_data=f"savepow_{target_uid}", style="primary"),
    ])
    rows.extend(get_nav_rows("admin_manage_admins", "🔙 Back to Admins"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "admin_manage_admins")
async def admin_manage_admins_cb(callback: types.CallbackQuery, state: FSMContext):
    if not is_super_owner(callback.from_user.id):
        return await safe_answer(callback, "❌ Sirf Super Owner admins manage kar sakta hai!", show_alert=True)
    await safe_answer(callback)
    await state.clear()

    cursor.execute("SELECT * FROM admins")
    rows = cursor.fetchall()
    lines = ["<blockquote>👮 <b>BOT ADMINS &amp; GRANULAR POWERS</b>\n"]
    kb_rows = []
    for r in rows:
        uid = r["user_id"]
        if is_super_owner(uid):
            lines.append(f"• <code>{uid}</code> — 👑 <b>SUPER OWNER (ALL POWERS)</b>")
        else:
            p_cnt = len(json.loads(r["powers"] or "[]"))
            lines.append(f"• <code>{uid}</code> — 👮 Admin (<code>{p_cnt}/{len(ALL_ADMIN_POWERS)} Powers</code>)")
            kb_rows.append([
                InlineKeyboardButton(text=f"⚙️ Powers ({uid})", callback_data=f"editpow_{uid}", style="primary"),
                InlineKeyboardButton(text="🗑️ Remove", callback_data=f"deladmin_{uid}", style="danger"),
            ])
    lines.append("</blockquote>")

    kb_rows.append([InlineKeyboardButton(text="➕ Add New Admin (ID / @user)", callback_data="admin_add_new_admin", style="success")])
    kb_rows.extend(get_nav_rows("admin_panel", "🔙 Back to Admin Panel"))
    await safe_edit(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@dp.callback_query(F.data == "admin_add_new_admin")
async def admin_ask_new_admin(callback: types.CallbackQuery, state: FSMContext):
    if not is_super_owner(callback.from_user.id):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_new_admin)
    await safe_edit(
        callback.message,
        "<blockquote>👮 Naye Admin ki <b>Chat ID</b> ya <b><code>@username</code></b> bhejein:</blockquote>",
        reply_markup=make_nav_kb("admin_manage_admins", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_new_admin)
async def admin_save_new_admin(message: types.Message, state: FSMContext):
    if not is_super_owner(message.from_user.id):
        return
    u_row = await resolve_target_user(message.text or "")
    if not u_row:
        return await message.reply("<blockquote>❌ User nahi mila!</blockquote>", parse_mode="HTML")

    uid = u_row["user_id"]
    default_p = ["vc_ids", "user_lookup"]
    cursor.execute(
        "INSERT OR REPLACE INTO admins (user_id, powers, added_by, added_at) VALUES (?, ?, ?, ?)",
        (uid, json.dumps(default_p), message.from_user.id, full_date_ist()),
    )
    db_conn.commit()
    await state.clear()
    await message.reply(
        f"<blockquote>⚙️ <b>SELECT POWERS FOR ADMIN <code>{uid}</code></b>\n\n"
        f"Jo-jo powers deni hain unhe ON (✅) karein — unhe Admin Panel mein sirf wahi buttons dikhenge:</blockquote>",
        reply_markup=build_admin_powers_kb(uid, default_p),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("editpow_"))
async def admin_edit_powers_cb(callback: types.CallbackQuery):
    if not is_super_owner(callback.from_user.id):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    await safe_answer(callback)
    uid = int(callback.data.replace("editpow_", ""))
    await safe_edit(
        callback.message,
        f"<blockquote>⚙️ <b>EDIT POWERS FOR ADMIN <code>{uid}</code></b></blockquote>",
        reply_markup=build_admin_powers_kb(uid, get_admin_powers(uid)),
    )


@dp.callback_query(F.data.startswith("togpow_"))
async def admin_toggle_power_cb(callback: types.CallbackQuery):
    if not is_super_owner(callback.from_user.id):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    await safe_answer(callback)
    _, uid_s, pkey = callback.data.split("_", 2)
    uid = int(uid_s)
    cur_p = get_admin_powers(uid)
    if pkey in cur_p:
        cur_p.remove(pkey)
    else:
        cur_p.append(pkey)
    cursor.execute("UPDATE admins SET powers = ? WHERE user_id = ?", (json.dumps(cur_p), uid))
    db_conn.commit()
    await safe_edit(
        callback.message,
        f"<blockquote>⚙️ <b>EDIT POWERS FOR ADMIN <code>{uid}</code></b></blockquote>",
        reply_markup=build_admin_powers_kb(uid, cur_p),
    )


@dp.callback_query(F.data.startswith("allpow_"))
async def admin_select_all_powers_cb(callback: types.CallbackQuery):
    if not is_super_owner(callback.from_user.id):
        return
    await safe_answer(callback, "✅ All powers selected!")
    uid = int(callback.data.replace("allpow_", ""))
    all_p = [p[0] for p in ALL_ADMIN_POWERS]
    cursor.execute("UPDATE admins SET powers = ? WHERE user_id = ?", (json.dumps(all_p), uid))
    db_conn.commit()
    await safe_edit(
        callback.message,
        f"<blockquote>⚙️ <b>EDIT POWERS FOR ADMIN <code>{uid}</code></b></blockquote>",
        reply_markup=build_admin_powers_kb(uid, all_p),
    )


@dp.callback_query(F.data.startswith("savepow_"))
async def admin_save_power_cb(callback: types.CallbackQuery, state: FSMContext):
    await safe_answer(callback, "✅ Admin Powers Saved!", show_alert=True)
    await admin_manage_admins_cb(callback, state)


@dp.callback_query(F.data.startswith("deladmin_"))
async def admin_delete_admin(callback: types.CallbackQuery, state: FSMContext):
    if not is_super_owner(callback.from_user.id):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    uid = int(callback.data.replace("deladmin_", ""))
    if not is_super_owner(uid):
        cursor.execute("DELETE FROM admins WHERE user_id = ?", (uid,))
        db_conn.commit()
    await safe_answer(callback, "🗑️ Admin Removed!", show_alert=True)
    await admin_manage_admins_cb(callback, state)


# --- 17.9 LIVE VCs, F-SUB, CREDITS, LOOKUP, PROMO, PRICING, LOGO, MEMORY ---
@dp.callback_query(F.data == "admin_live_vcs")
async def admin_live_vcs_cb(callback: types.CallbackQuery):
    if not has_power(callback.from_user.id, "vc_ids"):
        return await safe_answer(callback, "❌ No VC IDs Power!", show_alert=True)
    await safe_answer(callback)
    jobs = list(vc_engine.active_jobs.values())
    if not jobs:
        return await safe_edit(
            callback.message,
            "<blockquote>🟢 Filhal koi live VC job active nahi hai.</blockquote>",
            reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
        )
    lines = ["<blockquote>🔴 <b>ALL LIVE VC ORDERS (MULTI-VC)</b>\n"]
    kb_rows = []
    for j in jobs:
        rem = max(0, int(j["end_time"] - time.time()))
        lines.append(
            f"• <b>{j['job_id']}</b> | User: <code>{j['user_id']}</code>\n"
            f"  🔗 <code>{esc_html(j['title'][:22])}</code> | 🤖 <code>{len(j['workers'])} IDs</code> | ⏳ <code>{rem//60}m {rem%60}s</code>"
        )
        kb_rows.append([
            InlineKeyboardButton(text=f"🎛️ Control {j['job_id']}", callback_data=f"managevc_{j['job_id']}", style="primary"),
            InlineKeyboardButton(text="🛑 Stop", callback_data=f"stopvc_{j['job_id']}", style="danger"),
        ])
    lines.append("</blockquote>")
    kb_rows.append([InlineKeyboardButton(text="🔄 Refresh", callback_data="admin_live_vcs", style="primary")])
    kb_rows.extend(get_nav_rows("admin_panel", "🔙 Back"))
    await safe_edit(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@dp.callback_query(F.data == "admin_fsub_ch_menu")
async def admin_fsub_ch_menu_cb(callback: types.CallbackQuery):
    if not has_power(callback.from_user.id, "fsub"):
        return await safe_answer(callback, "❌ No F-Sub Power!", show_alert=True)
    await safe_answer(callback)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Public Channel", callback_data="addfsub_channel_public", style="success")],
            [InlineKeyboardButton(text="🔒 Private Channel", callback_data="addfsub_channel_private", style="primary")],
            *get_nav_rows("admin_panel", "🔙 Back"),
        ]
    )
    await safe_edit(callback.message, "<blockquote>📢 <b>ADD F-SUB CHANNEL</b></blockquote>", reply_markup=kb)


@dp.callback_query(F.data == "admin_fsub_grp_menu")
async def admin_fsub_grp_menu_cb(callback: types.CallbackQuery):
    if not has_power(callback.from_user.id, "fsub"):
        return await safe_answer(callback, "❌ No F-Sub Power!", show_alert=True)
    await safe_answer(callback)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌐 Public Group", callback_data="addfsub_group_public", style="success")],
            [InlineKeyboardButton(text="🔒 Private Group", callback_data="addfsub_group_private", style="primary")],
            *get_nav_rows("admin_panel", "🔙 Back"),
        ]
    )
    await safe_edit(callback.message, "<blockquote>👥 <b>ADD F-SUB GROUP</b></blockquote>", reply_markup=kb)


@dp.callback_query(F.data.startswith("addfsub_"))
async def admin_ask_fsub_details(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "fsub"):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    await safe_answer(callback)
    _, ttype, vis = callback.data.split("_")
    await state.update_data(fsub_type=ttype, fsub_vis=vis)
    await state.set_state(AdminFlow.waiting_for_fsub_input)
    await safe_edit(
        callback.message,
        f"<blockquote>📢 <b>ADD {vis.upper()} {ttype.upper()}</b>\n\n"
        f"Send link / <code>@username</code>, OR Private Invite Link + <code>-100...</code> ID, OR forward a message from that chat:</blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_fsub_input)
async def admin_save_fsub_details(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ttype = data.get("fsub_type", "channel")
    vis = data.get("fsub_vis", "public")
    invite_url, chat_ident, title = "", "", f"{vis.title()} {ttype.title()}"

    f_chat = getattr(getattr(message, "forward_origin", None), "chat", None) or getattr(message, "forward_from_chat", None)
    if f_chat:
        chat_ident = str(f_chat.id)
        title = f_chat.title or title
        try:
            invite_url = await bot.export_chat_invite_link(f_chat.id)
        except Exception:
            if f_chat.username:
                invite_url = f"https://t.me/{f_chat.username}"
    else:
        for tok in (message.text or "").replace("\n", " ").split():
            if tok.startswith("-100") or (tok.lstrip("-").isdigit() and len(tok) > 6):
                chat_ident = tok
            elif "t.me/" in tok or tok.startswith("http"):
                invite_url = tok if tok.startswith("http") else "https://" + tok
                if not ("t.me/+" in tok or "joinchat/" in tok):
                    slug = tok.split("t.me/")[-1].split("/")[0].split("?")[0].strip()
                    if slug and not chat_ident:
                        chat_ident = "@" + slug
            elif tok.startswith("@"):
                chat_ident = tok
                if not invite_url:
                    invite_url = f"https://t.me/{tok.lstrip('@')}"

    if not chat_ident and not invite_url:
        return await message.reply("<blockquote>❌ Invalid link or ID!</blockquote>", parse_mode="HTML")

    try:
        c_key = int(chat_ident) if chat_ident.startswith("-100") else chat_ident
        c_info = await bot.get_chat(c_key)
        title = c_info.title or title
    except Exception:
        pass

    cursor.execute(
        "INSERT INTO fsub_targets (target_type, visibility, title, chat_identifier, invite_url, added_at) VALUES (?, ?, ?, ?, ?, ?)",
        (ttype, vis, title, chat_ident, invite_url, full_date_ist()),
    )
    db_conn.commit()
    await state.clear()
    await message.reply(
        f"<blockquote>✅ F-Sub Added: <b>{esc_html(title)}</b>!</blockquote>",
        reply_markup=make_nav_kb("admin_manage_fsub", "🔙 F-Sub List"),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "admin_manage_fsub")
async def admin_manage_fsub_cb(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "fsub"):
        return await safe_answer(callback, "❌ No F-Sub Power!", show_alert=True)
    await safe_answer(callback)
    await state.clear()
    targets = get_fsub_targets()
    if not targets:
        return await safe_edit(
            callback.message,
            "<blockquote>📢 Koi F-Sub target added nahi hai.</blockquote>",
            reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
        )
    lines, kb_rows = ["<blockquote>📢 <b>ACTIVE F-SUB TARGETS</b>\n"], []
    for t in targets:
        lines.append(f"• #{t['id']} [{t['visibility'].upper()} {t['target_type'].upper()}] <b>{esc_html(t['title'])}</b>")
        kb_rows.append([InlineKeyboardButton(text=f"🗑️ Remove #{t['id']} ({t['title'][:15]})", callback_data=f"delfsub_{t['id']}", style="danger")])
    lines.append("</blockquote>")
    kb_rows.extend(get_nav_rows("admin_panel", "🔙 Back"))
    await safe_edit(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@dp.callback_query(F.data.startswith("delfsub_"))
async def admin_delete_fsub(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "fsub"):
        return
    cursor.execute("DELETE FROM fsub_targets WHERE id = ?", (int(callback.data.replace("delfsub_", "")),))
    db_conn.commit()
    await safe_answer(callback, "🗑️ F-Sub Removed!", show_alert=True)
    await admin_manage_fsub_cb(callback, state)


@dp.callback_query(F.data == "admin_mod_credits")
async def admin_ask_credit_mod(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "credits"):
        return await safe_answer(callback, "❌ No Credits Power!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_credit_mod)
    await safe_edit(
        callback.message,
        "<blockquote>💰 <b>ADD / CUT USER CREDITS</b>\n\nFormat: <code>ID_OR_@USER AMOUNT</code>\nExamples: <code>@username 100</code> or <code>123456789 -50</code></blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_credit_mod)
async def admin_save_credit_mod(message: types.Message, state: FSMContext):
    try:
        p = (message.text or "").split()
        u = await resolve_target_user(p[0])
        delta = float(p[1])
        if not u:
            return await message.reply("<blockquote>❌ User not found!</blockquote>", parse_mode="HTML")
        cursor.execute("UPDATE users SET credits = MAX(0, credits + ?) WHERE user_id = ?", (delta, u["user_id"]))
        db_conn.commit()
        new_bal = fmt_num(get_user(u["user_id"])["credits"])
        await state.clear()
        try:
            action_word = "added to" if delta >= 0 else "deducted from"
            await bot.send_message(
                u["user_id"],
                f"<blockquote>{pe('COIN')} <b>WALLET UPDATED BY ADMIN</b>\n"
                f"<code>{'+' if delta >= 0 else ''}{fmt_num(delta)} Cr</code> {action_word} your wallet.\n"
                f"💎 <b>New Balance:</b> <code>{new_bal} Cr</code></blockquote>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        await send_bot_log(
            "ADMIN_CREDIT_MOD",
            pe("PLUS") if delta >= 0 else pe("MINUS"),
            user=get_user(u["user_id"]),
            fields=[("Change", f"<code>{delta} Cr</code>"), ("New Bal", f"<code>{new_bal} Cr</code>"), ("By Admin", f"<code>{message.from_user.id}</code>")],
        )
        await message.reply(
            f"<blockquote>✅ Updated! New Balance of <code>{u['user_id']}</code>: <code>{new_bal} Cr</code></blockquote>",
            reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
            parse_mode="HTML",
        )
    except Exception:
        await message.reply("<blockquote>❌ Invalid format! Use <code>@user 100</code> or <code>12345 -50</code>.</blockquote>", parse_mode="HTML")


@dp.callback_query(F.data == "admin_user_lookup")
async def admin_ask_user_lookup(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "user_lookup"):
        return await safe_answer(callback, "❌ No User Lookup Power!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_user_lookup)
    await safe_edit(
        callback.message,
        "<blockquote>🔍 Send User <b>Numeric ID</b> or <b><code>@username</code></b>:</blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_user_lookup)
async def admin_process_user_lookup(message: types.Message, state: FSMContext):
    u = await resolve_target_user(message.text or "")
    if not u:
        return await message.reply("<blockquote>❌ User not found!</blockquote>", parse_mode="HTML")
    await state.clear()
    vip_target = max(1, get_setting("vip_ref_target", int))
    bar, pct, is_vip = build_vip_bar(u["referrals"], vip_target)
    await message.reply(
        f"<blockquote>🔍 <b>DETAILED USER REPORT</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Name:</b> {esc_html(u['first_name'])} (@{esc_html(u['username'] or 'None')})\n"
        f"🆔 <b>User ID:</b> <code>{u['user_id']}</code>\n"
        f"🪙 <b>Balance:</b> <code>{fmt_num(u['credits'])} Cr</code>\n"
        f"💵 <b>Total Paid:</b> <code>₹{fmt_num(u['total_paid_inr'])}/-</code>\n"
        f"💸 <b>Credits Spent:</b> <code>{fmt_num(u['total_credits_spent'])} Cr</code>\n"
        f"⚖️ <b>Fines Paid:</b> <code>{fmt_num(u['fines_paid'])} Cr</code>\n"
        f"🐺 <b>VC Orders:</b> <code>{u['orders_count']}</code> (<code>{u['total_vc_minutes']} Mins</code>)\n"
        f"⚡ <b>Referrals:</b> <code>{u['referrals']}/{vip_target}</code> ({'🏆 VIP' if is_vip else f'{pct}%'})\n"
        f"<code>{bar}</code>\n"
        f"📅 <b>Joined:</b> <code>{u['joined_at']}</code></blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "admin_promo_menu")
async def admin_promo_menu_cb(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "pricing"):
        return await safe_answer(callback, "❌ No Pricing Power!", show_alert=True)
    await safe_answer(callback)
    await state.clear()
    cursor.execute("SELECT * FROM promo_codes")
    rows = cursor.fetchall()
    lines = ["<blockquote>🎟️ <b>PROMO CODES MANAGER</b>\n"]
    kb_rows = []
    if not rows:
        lines.append("<i>No active promo codes.</i>")
    for r in rows:
        lines.append(f"• <code>{r['code']}</code> — <b>{fmt_num(r['amount'])} Cr</b> ({r['current_uses']}/{r['max_uses']} used)")
        kb_rows.append([InlineKeyboardButton(text=f"🗑️ Delete {r['code']}", callback_data=f"delpromo_{r['code']}", style="danger")])
    lines.append("</blockquote>")
    kb_rows.append([InlineKeyboardButton(text="➕ Create New Promo Code", callback_data="admin_create_code", style="success")])
    kb_rows.extend(get_nav_rows("admin_panel", "🔙 Back"))
    await safe_edit(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@dp.callback_query(F.data.startswith("delpromo_"))
async def admin_delete_promo_cb(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "pricing"):
        return
    code = callback.data.replace("delpromo_", "")
    cursor.execute("DELETE FROM promo_codes WHERE code = ?", (code,))
    cursor.execute("DELETE FROM promo_claims WHERE code = ?", (code,))
    db_conn.commit()
    await safe_answer(callback, f"🗑️ Deleted {code}!", show_alert=True)
    await admin_promo_menu_cb(callback, state)


@dp.callback_query(F.data == "admin_create_code")
async def admin_ask_code(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "pricing"):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_code_creation)
    await safe_edit(
        callback.message,
        "<blockquote>🎟️ <b>CREATE PROMO CODE</b>\n\nFormat: <code>CODE CREDITS MAX_USES</code>\nExample: <code>HOLI50 50 100</code></blockquote>",
        reply_markup=make_nav_kb("admin_promo_menu", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_code_creation)
async def admin_save_code(message: types.Message, state: FSMContext):
    try:
        p = (message.text or "").split()
        code = p[0].upper()
        amt = float(p[1])
        max_u = int(p[2]) if len(p) > 2 else 1
        cursor.execute(
            "REPLACE INTO promo_codes (code, amount, max_uses, current_uses, created_at) VALUES (?, ?, ?, 0, ?)",
            (code, amt, max_u, full_date_ist()),
        )
        db_conn.commit()
        await state.clear()
        await message.reply(
            f"<blockquote>✅ Promo <code>{code}</code> (+{fmt_num(amt)} Cr, Max {max_u} uses) created!</blockquote>",
            reply_markup=make_nav_kb("admin_promo_menu", "🔙 Promo Manager"),
            parse_mode="HTML",
        )
    except Exception:
        await message.reply("<blockquote>❌ Invalid format! Use <code>CODE CREDITS MAX_USES</code>.</blockquote>", parse_mode="HTML")


@dp.callback_query(F.data == "admin_ping_sessions_now")
async def admin_ping_sessions_now_cb(callback: types.CallbackQuery):
    if not has_power(callback.from_user.id, "vc_ids"):
        return await safe_answer(callback, "❌ No VC IDs Power!", show_alert=True)
    await safe_answer(callback, "🩺 Pinging all VC worker IDs...")
    alive, dead = await vc_engine.ping_and_verify_all_workers()
    await callback.message.answer(
        f"<blockquote>🩺 <b>SESSION HEALTH CHECK</b>\n\n"
        f"🟢 <b>Alive &amp; Ready:</b> <code>{alive}</code>\n"
        f"🔴 <b>Dead Removed:</b> <code>{len(dead)}</code></blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "admin_health_interval")
async def admin_health_interval_cb(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "vc_ids"):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_health_interval)
    await safe_edit(
        callback.message,
        f"<blockquote>🩺 <b>AUTO HEALTH PING INTERVAL</b>\nCurrent: <code>{get_setting('health_ping_mins', int)} Mins</code>\nEnter minutes (min 5):</blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_health_interval)
async def admin_save_health_interval(message: types.Message, state: FSMContext):
    try:
        val = max(5, int((message.text or "").strip()))
        set_setting("health_ping_mins", val)
        await state.clear()
        await message.reply(f"<blockquote>✅ Health ping interval set to <code>{val} mins</code>!</blockquote>", reply_markup=make_nav_kb("admin_panel", "🔙 Back"), parse_mode="HTML")
    except Exception:
        await message.reply("<blockquote>❌ Valid minutes enter karein!</blockquote>", parse_mode="HTML")


@dp.callback_query(F.data == "admin_brand_logo_menu")
async def admin_brand_logo_menu_cb(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "logs_backup"):
        return await safe_answer(callback, "❌ No Logs/Backup Power!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_brand_logo)
    on = get_setting("brand_logo_enabled", int) == 1
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Logo on Receipt: {'🟢 ON' if on else '🔴 OFF'}", callback_data="admin_toggle_logo", style="success" if on else "danger")],
            *get_nav_rows("admin_panel", "🔙 Back"),
        ]
    )
    await safe_edit(
        callback.message,
        "<blockquote>🖼️ <b>BRAND LOGO FOR RECEIPTS</b>\n\nNaya logo set karne ke liye photo bhejein, ya niche button se ON/OFF karein:</blockquote>",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "admin_toggle_logo")
async def admin_toggle_logo_cb(callback: types.CallbackQuery, state: FSMContext):
    set_setting("brand_logo_enabled", 0 if get_setting("brand_logo_enabled", int) == 1 else 1)
    await admin_brand_logo_menu_cb(callback, state)


@dp.message(AdminFlow.waiting_for_brand_logo, F.photo | F.document)
async def admin_receive_brand_logo(message: types.Message, state: FSMContext):
    fid = message.photo[-1].file_id if message.photo else message.document.file_id
    bio = io.BytesIO()
    await bot.download_file((await bot.get_file(fid)).file_path, destination=bio)
    bio.seek(0)
    Image.open(bio).convert("RGBA").save(BRAND_LOGO_PATH, format="PNG")
    set_setting("brand_logo_enabled", 1)
    await state.clear()
    await message.reply("<blockquote>✅ Brand Logo Saved &amp; Enabled!</blockquote>", reply_markup=make_nav_kb("admin_panel", "🔙 Back"), parse_mode="HTML")


@dp.callback_query(F.data == "admin_set_log_channel")
async def admin_ask_log_forward(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "logs_backup"):
        return await safe_answer(callback, "❌ No Logs/Backup Power!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_log_forward)
    await safe_edit(
        callback.message,
        f"<blockquote>📡 <b>SET LOG CHANNEL</b>\nCurrent: <code>{esc_html(get_setting('log_channel_title', str))}</code>\n\nApne Log Channel se koi bhi message yahan Forward karein:</blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_log_forward)
async def admin_receive_log_forward(message: types.Message, state: FSMContext):
    f_chat = getattr(getattr(message, "forward_origin", None), "chat", None) or getattr(message, "forward_from_chat", None)
    if not f_chat:
        return await message.reply("<blockquote>❌ Channel se message forward karein!</blockquote>", parse_mode="HTML")
    set_setting("log_channel_id", f_chat.id)
    set_setting("log_channel_title", f_chat.title or str(f_chat.id))
    await state.clear()
    await message.reply(f"<blockquote>✅ Log Channel connected: <code>{esc_html(f_chat.title or str(f_chat.id))}</code></blockquote>", reply_markup=make_nav_kb("admin_panel", "🔙 Back"), parse_mode="HTML")


@dp.callback_query(F.data == "admin_get_memory")
async def admin_get_full_memory_cb(callback: types.CallbackQuery):
    if not has_power(callback.from_user.id, "logs_backup"):
        return await safe_answer(callback, "❌ No Backup Power!", show_alert=True)
    await safe_answer(callback, "📦 Exporting full memory...")
    raw_b, stats = export_full_memory_json()
    fname = f"VCBot_Memory_{now_ist().strftime('%Y%m%d_%H%M')}_IST.json"
    await callback.message.answer_document(
        BufferedInputFile(raw_b, filename=fname),
        caption=(
            f"<blockquote>💾 <b>FULL BOT MEMORY BACKUP</b>\n"
            f"👥 Users: <code>{stats['users']}</code> | 📱 VC IDs: <code>{stats['vc_sessions']}</code> | 💵 Payments: <code>{stats['payments']}</code></blockquote>"
        ),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "admin_revive_memory")
async def admin_ask_revive_json(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "logs_backup"):
        return await safe_answer(callback, "❌ No Backup Power!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_revive_json)
    await safe_edit(
        callback.message,
        "<blockquote>♻️ <b>REVIVE MEMORY FROM BACKUP</b>\n\nApni saved <code>.json</code> backup file yahan send karein:</blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_revive_json, F.document)
async def admin_process_revive_json(message: types.Message, state: FSMContext):
    try:
        bio = io.BytesIO()
        await bot.download_file((await bot.get_file(message.document.file_id)).file_path, destination=bio)
        counts = await restore_full_memory_json(bio.getvalue())
        await state.clear()
        summary = ", ".join([f"{k}: {v}" for k, v in counts.items()])
        await message.reply(
            f"<blockquote>🎉 <b>MEMORY REVIVED!</b>\nRestored: <code>{summary}</code>\n📱 Live VC IDs: <code>{vc_engine.available_count()}</code></blockquote>",
            reply_markup=make_nav_kb("admin_panel", "🔙 Admin Panel"),
            parse_mode="HTML",
        )
    except Exception as e:
        await message.reply(f"<blockquote>❌ Revive Error: <code>{esc_html(str(e))}</code></blockquote>", parse_mode="HTML")


@dp.callback_query(F.data == "admin_auto_backup")
async def admin_auto_backup_menu(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "logs_backup"):
        return await safe_answer(callback, "❌ No Backup Power!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_backup_interval)
    await safe_edit(
        callback.message,
        f"<blockquote>⏰ <b>AUTO-BACKUP INTERVAL</b>\nCurrent: <code>{get_setting('auto_backup_mins', int)} Mins</code>\nMinutes bhejein (<code>0</code> = OFF):</blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_backup_interval)
async def admin_custom_backup_mins(message: types.Message, state: FSMContext):
    try:
        val = max(0, int((message.text or "").strip()))
        set_setting("auto_backup_mins", val)
        await state.clear()
        await message.reply(f"<blockquote>✅ Auto-Backup set to <code>{val} mins</code>!</blockquote>", reply_markup=make_nav_kb("admin_panel", "🔙 Back"), parse_mode="HTML")
    except Exception:
        await message.reply("<blockquote>❌ Valid minutes enter karein!</blockquote>", parse_mode="HTML")


@dp.callback_query(F.data == "admin_all_users_txt")
async def admin_export_all_users_txt(callback: types.CallbackQuery):
    if not has_power(callback.from_user.id, "user_lookup"):
        return await safe_answer(callback, "❌ No Export Power!", show_alert=True)
    await safe_answer(callback, "📋 Exporting...")
    cursor.execute("SELECT * FROM users ORDER BY total_paid_inr DESC")
    rows = cursor.fetchall()
    lines = [
        f"ID: {u['user_id']} | @{u['username'] or 'None'} | Bal: {fmt_num(u['credits'])} Cr | Paid: Rs.{fmt_num(u['total_paid_inr'])} | Refs: {u['referrals']} | Orders: {u['orders_count']}"
        for u in rows
    ]
    await callback.message.answer_document(
        BufferedInputFile("\n".join(lines).encode("utf-8"), filename=f"All_Users_{now_ist().strftime('%Y%m%d')}.txt"),
        caption=f"📋 Total Users Exported: <code>{len(rows)}</code>",
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "admin_set_credit_price")
async def admin_ask_credit_price(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "pricing"):
        return await safe_answer(callback, "❌ No Pricing Power!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_credit_price)
    await safe_edit(
        callback.message,
        f"<blockquote>💱 <b>CREDIT EXCHANGE RATE</b>\nCurrent: <code>₹1/- = {fmt_num(get_setting('rupee_to_credit', float))} Cr</code>\nSend new Credits per ₹1/-:</blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_credit_price)
async def admin_save_credit_price(message: types.Message, state: FSMContext):
    try:
        val = float((message.text or "").strip())
        if val <= 0:
            raise ValueError("Must be positive")
        set_setting("rupee_to_credit", val)
        await state.clear()
        await message.reply(f"<blockquote>✅ Rate updated: <code>₹1/- = {fmt_num(val)} Cr</code>!</blockquote>", reply_markup=make_nav_kb("admin_panel", "🔙 Back"), parse_mode="HTML")
    except Exception:
        await message.reply("<blockquote>❌ Positive number bhejein!</blockquote>", parse_mode="HTML")


@dp.callback_query(F.data == "admin_set_ratio")
async def admin_ask_ratio(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "pricing"):
        return await safe_answer(callback, "❌ No Pricing Power!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_ratio)
    await safe_edit(
        callback.message,
        f"<blockquote>⚙️ <b>VC USAGE RATE RATIO (PER-MINUTE)</b>\nCurrent: <code>{get_setting('base_ids', int)} IDs × {get_setting('base_time', int)} Min = {fmt_num(get_setting('base_credits', float))} Cr</code>\n\nFormat: <code>IDs Mins Credits</code> (e.g. <code>1 1 5</code>):</blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_ratio)
async def admin_save_ratio(message: types.Message, state: FSMContext):
    try:
        p = (message.text or "").split()
        set_setting("base_ids", max(1, int(p[0])))
        set_setting("base_time", max(1, int(p[1])))
        set_setting("base_credits", max(0.0, float(p[2])))
        await state.clear()
        await message.reply("<blockquote>✅ VC Usage Ratio Updated!</blockquote>", reply_markup=make_nav_kb("admin_panel", "🔙 Back"), parse_mode="HTML")
    except Exception:
        await message.reply("<blockquote>❌ Invalid format! Use <code>IDs Mins Credits</code>.</blockquote>", parse_mode="HTML")


@dp.callback_query(F.data == "admin_edit_bonus_amt")
async def admin_ask_welcome_bonus_amt(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "pricing"):
        return await safe_answer(callback, "❌ No Pricing Power!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_welcome_bonus)
    on = get_setting("bonus_enabled", int) == 1
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Welcome Gift: {'🟢 ON' if on else '🔴 OFF'}", callback_data="admin_toggle_bonus", style="success" if on else "danger")],
            *get_nav_rows("admin_panel", "🔙 Back"),
        ]
    )
    await safe_edit(
        callback.message,
        f"<blockquote>🎁 <b>WELCOME GIFT</b>\nCurrent: <code>{fmt_num(get_setting('welcome_bonus', float))} Cr</code>\nSend new amount or toggle ON/OFF:</blockquote>",
        reply_markup=kb,
    )


@dp.callback_query(F.data == "admin_toggle_bonus")
async def admin_toggle_bonus_cb(callback: types.CallbackQuery, state: FSMContext):
    set_setting("bonus_enabled", 0 if get_setting("bonus_enabled", int) == 1 else 1)
    await admin_ask_welcome_bonus_amt(callback, state)


@dp.message(AdminFlow.waiting_for_welcome_bonus)
async def admin_save_welcome_bonus_amt(message: types.Message, state: FSMContext):
    try:
        val = max(0.0, float((message.text or "").strip()))
        set_setting("welcome_bonus", val)
        await state.clear()
        await message.reply(f"<blockquote>✅ Welcome Gift set to <code>{fmt_num(val)} Cr</code>!</blockquote>", reply_markup=make_nav_kb("admin_panel", "🔙 Back"), parse_mode="HTML")
    except Exception:
        await message.reply("<blockquote>❌ Valid number bhejein!</blockquote>", parse_mode="HTML")


# --- 17.10 UPGRADED BROADCAST (NO ANNOUNCEMENT HEADER + QUOTE VS SIMPLE TEXT CHOICE) ---
@dp.callback_query(F.data == "admin_broadcast")
async def admin_ask_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "broadcast"):
        return await safe_answer(callback, "❌ No Broadcast Power!", show_alert=True)
    await safe_answer(callback)
    await state.set_state(AdminFlow.waiting_for_broadcast)
    await safe_edit(
        callback.message,
        "<blockquote>📢 <b>BROADCAST MESSAGE</b>\n\n"
        "Apna broadcast text ya Photo (with/without caption) yahan bhejein.\n"
        "<i>Next step mein aap choose kar payenge ki ise <b>Premium Quote</b> mein bhejna hai ya <b>Simple Text</b> mein (upar koi extra 'Announcement' heading nahi aayegi).</i></blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_broadcast)
async def admin_choose_broadcast_style(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id if message.photo else None
    # Preserve premium custom emojis & formatting via html_text if available
    raw_html = (message.html_text or message.caption or message.text or "").strip()
    if not photo_id and not raw_html:
        return await message.reply("<blockquote>❌ Text ya Photo bhejein!</blockquote>", parse_mode="HTML")

    await state.update_data(bc_photo_id=photo_id, bc_html=raw_html)
    await state.set_state(AdminFlow.waiting_for_broadcast_style)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Send in Premium Quote", callback_data="bcstyle_quote", style="success")],
            [InlineKeyboardButton(text="📝 Send as Simple Text", callback_data="bcstyle_simple", style="primary")],
            *get_nav_rows("admin_panel", "❌ Cancel Broadcast"),
        ]
    )
    media_lbl = "🖼️ Photo + Caption" if photo_id else "📝 Text Message"
    await message.reply(
        f"<blockquote>📢 <b>SELECT BROADCAST FORMAT ({media_lbl})</b>\n\n"
        f"Preview:\n{raw_html[:300] or '(Photo only)'}\n\n"
        f"Format choose karein (koi extra heading nahi lagegi):</blockquote>",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.callback_query(AdminFlow.waiting_for_broadcast_style, F.data.startswith("bcstyle_"))
async def admin_execute_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "broadcast"):
        return await safe_answer(callback, "❌ Unauthorized!", show_alert=True)
    await safe_answer(callback, "📢 Starting broadcast...")

    style = callback.data.replace("bcstyle_", "")
    data = await state.get_data()
    photo_id = data.get("bc_photo_id")
    raw_html = data.get("bc_html", "")
    await state.clear()

    if raw_html:
        final_body = f"<blockquote>{raw_html}</blockquote>" if style == "quote" else raw_html
    else:
        final_body = ""

    cursor.execute("SELECT user_id FROM users")
    rows = cursor.fetchall()
    await safe_edit(callback.message, f"<blockquote>📢 Broadcasting to <code>{len(rows)}</code> users...</blockquote>")

    sent, failed = 0, 0
    for r in rows:
        try:
            if photo_id:
                await bot.send_photo(
                    r["user_id"],
                    photo=photo_id,
                    caption=final_body or None,
                    parse_mode="HTML" if final_body else None,
                )
            else:
                await bot.send_message(
                    r["user_id"],
                    final_body,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await safe_edit(
        callback.message,
        f"<blockquote>✅ <b>BROADCAST COMPLETE!</b>\n\n"
        f"Style: <code>{'Premium Quote' if style == 'quote' else 'Simple Text'}</code>\n"
        f"🟢 Sent: <code>{sent}</code> | 🔴 Failed/Blocked: <code>{failed}</code></blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back to Admin Panel"),
    )


# --- 17.11 VC WORKER PHONE + OTP + 2FA LOGIN & POOL MANAGER ---
@dp.callback_query(F.data == "admin_phone_login")
async def admin_start_phone_login(callback: types.CallbackQuery, state: FSMContext):
    if not has_power(callback.from_user.id, "vc_ids") or not VC_ENGINE_AVAILABLE:
        return await safe_answer(callback, "❌ Unauthorized or VC Engine missing!", show_alert=True)
    await safe_answer(callback)
    await cleanup_pending_login(callback.from_user.id)
    await state.set_state(AdminFlow.waiting_for_phone)
    await safe_edit(
        callback.message,
        "<blockquote>📱 <b>ADD NEW VC WORKER ID</b>\n\nInternational format mein Phone Number bhejein (e.g. <code>+919876543210</code>):</blockquote>",
        reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_phone)
async def admin_receive_phone(message: types.Message, state: FSMContext):
    phone = (message.text or "").strip().replace(" ", "")
    client = Client(f"login_temp_{int(time.time())}", api_id=TG_API_ID, api_hash=TG_API_HASH, in_memory=True)
    try:
        await client.connect()
        sent_code = await client.send_code(phone)
        pending_logins[message.from_user.id] = {
            "client": client,
            "phone": phone,
            "code_hash": sent_code.phone_code_hash,
        }
        await state.update_data(otp_input="")
        await state.set_state(AdminFlow.waiting_for_otp)
        await message.reply(
            f"<blockquote>📩 <b>ENTER OTP FOR <code>{esc_html(phone)}</code></b>\n\n🔑 <b>OTP:</b> <code>_</code></blockquote>",
            reply_markup=get_numpad("adm_otp", "admin_phone_login", "🔙 Back"),
            parse_mode="HTML",
        )
    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        await state.clear()
        await message.reply(f"<blockquote>❌ OTP Send Error: <code>{esc_html(str(e))}</code></blockquote>", parse_mode="HTML")


@dp.callback_query(AdminFlow.waiting_for_otp, F.data.startswith("adm_otp_"))
async def admin_handle_otp_numpad(callback: types.CallbackQuery, state: FSMContext):
    act = callback.data.split("_")[2]
    otp_val = (await state.get_data()).get("otp_input", "")
    info = pending_logins.get(callback.from_user.id)
    if not info:
        await state.clear()
        return await safe_answer(callback, "⚠️ Login session expired!", show_alert=True)

    if act == "clear":
        otp_val = ""
    elif act == "submit":
        try:
            await info["client"].sign_in(info["phone"], info["code_hash"], otp_val)
            return await finalize_new_vc_account(callback.message, callback.from_user.id, state)
        except SessionPasswordNeeded:
            await state.set_state(AdminFlow.waiting_for_2fa)
            await safe_answer(callback)
            return await safe_edit(
                callback.message,
                "<blockquote>🔐 <b>2FA PASSWORD REQUIRED</b>\nApna Telegram Cloud (2FA) password bhejein:</blockquote>",
                reply_markup=make_nav_kb("admin_phone_login", "🔙 Back"),
            )
        except Exception as e:
            await cleanup_pending_login(callback.from_user.id)
            await state.clear()
            return await safe_edit(
                callback.message,
                f"<blockquote>❌ Sign-in Error: <code>{esc_html(str(e))}</code></blockquote>",
                reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
            )
    elif len(otp_val) < 6:
        otp_val += act

    await state.update_data(otp_input=otp_val)
    await safe_answer(callback)
    await safe_edit(
        callback.message,
        f"<blockquote>📩 <b>ENTER OTP</b>\n\n🔑 <b>OTP:</b> <code>{otp_val or '_'}</code></blockquote>",
        reply_markup=get_numpad("adm_otp", "admin_phone_login", "🔙 Back"),
    )


@dp.message(AdminFlow.waiting_for_2fa)
async def admin_receive_2fa_password(message: types.Message, state: FSMContext):
    info = pending_logins.get(message.from_user.id)
    if not info:
        return await state.clear()
    try:
        await info["client"].check_password((message.text or "").strip())
        await finalize_new_vc_account(message, message.from_user.id, state)
    except Exception as e:
        await message.reply(f"<blockquote>❌ 2FA Error: <code>{esc_html(str(e))}</code></blockquote>", parse_mode="HTML")


async def finalize_new_vc_account(msg_obj: types.Message, admin_id: int, state: FSMContext):
    info = pending_logins.pop(admin_id, None)
    await state.clear()
    if not info:
        return
    client, phone = info["client"], info["phone"]
    try:
        me = await client.get_me()
        s_str = await client.export_session_string()
        label = f"{me.first_name or 'VC_ID'} ({phone[-4:]})"
        await client.disconnect()
        cursor.execute(
            "INSERT INTO vc_sessions (phone, session_string, label, added_at) VALUES (?, ?, ?, ?)",
            (phone, s_str, label, full_date_ist()),
        )
        db_conn.commit()
        new_id = cursor.lastrowid
        await vc_engine.start_single_worker(new_id, s_str, label, phone)
        await send_bot_log("VC_ID_ADDED", pe("PLUS"), fields=[("Account", f"<code>{esc_html(label)}</code>"), ("By Admin", f"<code>{admin_id}</code>")])
        await safe_edit(
            msg_obj,
            f"<blockquote>🎉 <b>VC WORKER ID ACTIVATED!</b>\n📱 Account: <code>{esc_html(label)}</code> (Multi-VC Ready)</blockquote>",
            reply_markup=make_nav_kb("admin_panel", "🔙 Admin Panel"),
        )
    except Exception as e:
        await safe_edit(
            msg_obj,
            f"<blockquote>❌ Activation Error: <code>{esc_html(str(e))}</code></blockquote>",
            reply_markup=make_nav_kb("admin_panel", "🔙 Admin Panel"),
        )


@dp.callback_query(F.data == "admin_manage_ids")
async def admin_manage_vc_ids(callback: types.CallbackQuery):
    if not has_power(callback.from_user.id, "vc_ids"):
        return await safe_answer(callback, "❌ No VC IDs Power!", show_alert=True)
    await safe_answer(callback)
    cursor.execute("SELECT id, label FROM vc_sessions ORDER BY id ASC")
    rows = cursor.fetchall()
    if not rows:
        return await safe_edit(
            callback.message,
            "<blockquote>🗂️ Koi VC Worker ID added nahi hai.</blockquote>",
            reply_markup=make_nav_kb("admin_panel", "🔙 Back"),
        )
    lines, kb_rows = ["<blockquote>🗂️ <b>MULTI-VC WORKER POOL</b>\n"], []
    for r in rows:
        w = next((x for x in vc_engine.workers if x["id"] == r["id"]), None)
        if w and len(w["active_chats"]) > 0:
            st_txt = f"🟢 Online ({len(w['active_chats'])} Active VCs)"
        elif w:
            st_txt = "🟢 Online (Idle)"
        else:
            st_txt = "⚪ Offline"
        lines.append(f"• #{r['id']} <code>{esc_html(r['label'])}</code> — {st_txt}")
        kb_rows.append([InlineKeyboardButton(text=f"🗑️ Remove {r['label']}", callback_data=f"delvcid_{r['id']}", style="danger")])
    lines.append("</blockquote>")
    kb_rows.extend(get_nav_rows("admin_panel", "🔙 Back"))
    await safe_edit(callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@dp.callback_query(F.data.startswith("delvcid_"))
async def admin_delete_vc_id(callback: types.CallbackQuery):
    if not has_power(callback.from_user.id, "vc_ids"):
        return
    await vc_engine.remove_worker(int(callback.data.replace("delvcid_", "")))
    await safe_answer(callback, "🗑️ VC ID Removed!", show_alert=True)
    await admin_manage_vc_ids(callback)


# =====================================================================
# 18. CATCH-ALL FALLBACK, FAMGATEWAY WEBHOOK SERVER & MAIN LIFECYCLE
# =====================================================================
@dp.message()
async def catch_all_to_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        await build_home_text(message.from_user),
        reply_markup=get_main_menu_kb(message.from_user.id),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def famgateway_webhook_handler(request: web.Request):
    sig = request.headers.get("X-FamGateway-Signature", "")
    raw = await request.read()
    expected = hmac.new(FAMGATEWAY_API_KEY.encode(), raw, hashlib.sha256).hexdigest()
    if not sig or not hmac.compare_digest(expected, sig):
        return web.json_response({"error": "Invalid signature"}, status=401)
    payload = await request.json()
    if payload.get("event") == "payment.success":
        oid = payload.get("order_id")
        ok, info = fulfill_payment_in_db(oid, payload.get("utr", ""), payload.get("sender_name", "UPI Payer"))
        if ok:
            track_task(notify_payment_success(oid, info))
    return web.json_response({"status": "ok"}, status=200)


async def start_webhook_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_post("/api/famgateway-webhook", famgateway_webhook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, WEBHOOK_HOST, WEBHOOK_PORT).start()
    logger.info(f"FamGateway Webhook listening on http://{WEBHOOK_HOST}:{WEBHOOK_PORT}/api/famgateway-webhook")
    return runner


async def main():
    global auto_backup_task, health_check_task
    await vc_engine.load_all_workers()
    runner = await start_webhook_server()
    auto_backup_task = track_task(auto_backup_loop())
    health_check_task = track_task(session_health_monitor_loop())
    logger.info(f"Paid VC Bot v4.0 LIVE (Fast Multi-VC + Dynamic + Mic) with {vc_engine.available_count()} REAL VC IDs!")
    try:
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        logger.info("Shutting down bot & leaving active VCs cleanly...")
        await vc_engine.shutdown_all()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
