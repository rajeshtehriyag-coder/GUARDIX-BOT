"""
GUARDIX BOT - premium all-in-one Telegram group management & security bot.
Single-file build: aiogram 3.x + MongoDB (motor). Deploy on Railway with Docker.
"""
import asyncio
import html
import logging
import os
import random
import re
import secrets
import sys
import time
import unicodedata
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (TelegramAPIError, TelegramBadRequest,
                                TelegramForbiddenError, TelegramRetryAfter)
from aiogram.filters import Command, CommandObject
from aiogram.types import (CallbackQuery, ChatJoinRequest, ChatMemberUpdated,
                           ChatPermissions, ErrorEvent, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("guardix")

# =====================================================================
# CONFIG (all from environment variables - nothing hardcoded)
# =====================================================================


def _req(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        print(f"[config] Required variable missing: {name}")
        sys.exit(1)
    return v


def _int(name: str, default: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"[config] {name} must be a number")
        sys.exit(1)


BOT_TOKEN = _req("BOT_TOKEN")
MONGO_DB_URI = _req("MONGO_DB_URI")
OWNER_ID = _int("OWNER_ID", 0)
if not OWNER_ID:
    print("[config] Required variable missing or invalid: OWNER_ID")
    sys.exit(1)
BOT_USERNAME = os.getenv("BOT_USERNAME", "GuardixManagerBot").strip().lstrip("@")
DATABASE_NAME = os.getenv("DATABASE_NAME", "guardix").strip() or "guardix"
SUPPORT_URL = os.getenv("SUPPORT_URL", "").strip()
LOG_GROUP_ID = _int("LOG_GROUP_ID", 0)
TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata").strip()
SUDO_ENV = {int(x) for x in re.split(r"[,\s]+", os.getenv("SUDO_IDS", "")) if x.strip().lstrip("-").isdigit()}

try:
    TZ = ZoneInfo(TIMEZONE)
except Exception:
    TZ = timezone.utc

UTC = timezone.utc
GROUP_TYPES = {"group", "supergroup"}

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML,
                                                  link_preview_is_disabled=True))
dp = Dispatcher()
mongo = AsyncIOMotorClient(MONGO_DB_URI, tz_aware=True, serverSelectionTimeoutMS=8000)
db = mongo[DATABASE_NAME]
BOT_ID = 0


def now() -> datetime:
    return datetime.now(UTC)


def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%d %b, %I:%M %p")


esc = html.escape

# =====================================================================
# FONTS + PREMIUM CARD UI
# =====================================================================
BAR = "✦━━━━━━━━━━━━━━━✦"
_FONTS = {  # (upper start, lower start, digit start)
    "sans": (0x1D5D4, 0x1D5EE, 0x1D7EC),
    "serif": (0x1D400, 0x1D41A, 0x1D7CE),
    "mono": (0x1D670, 0x1D68A, 0x1D7F6),
    "script": (0x1D4D0, 0x1D4EA, None),
}
_SMALL = dict(zip("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"))
NORMAL_FONT: set = set()  # chat ids that chose plain font


def fx(text: str, style: str = "sans") -> str:
    if style == "plain":
        return text
    if style == "small":
        return "".join(_SMALL.get(c, c) for c in text.lower())
    up, lo, dg = _FONTS.get(style, _FONTS["sans"])
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(up + ord(ch) - 65))
        elif "a" <= ch <= "z":
            out.append(chr(lo + ord(ch) - 97))
        elif dg and "0" <= ch <= "9":
            out.append(chr(dg + ord(ch) - 48))
        else:
            out.append(ch)
    return "".join(out)


def _st(cat: str, cid) -> str:
    return "plain" if cid in NORMAL_FONT else cat


def card(title: str, emoji: str, body: str, cat: str = "sans", cid=None) -> str:
    return f"{BAR}\n   {emoji} {fx(title.upper(), _st(cat, cid))}\n{BAR}\n\n{body}"


def kv(rows, cat: str = "sans", cid=None) -> str:
    s = _st(cat, cid)
    return "\n".join(f"{e} {fx(label, s)}: {val}" for e, label, val in rows)


def mention(uid: int, name: str = "") -> str:
    return f'<a href="tg://user?id={uid}">{esc((name or str(uid))[:40])}</a>'


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def close_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[btn("✖ Close", f"close:{uid}")]])


# =====================================================================
# BACKGROUND TASKS + SENDING HELPERS
# =====================================================================
_BG: set = set()


def spawn(coro):
    t = asyncio.create_task(coro)
    _BG.add(t)
    t.add_done_callback(_BG.discard)
    return t


async def _del_later(chat_id: int, mid: int, delay: float):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, mid)
    except TelegramAPIError:
        pass


async def send(chat_id: int, text: str, kb=None, ttl: float = None, thread: int = None):
    for attempt in range(2):
        try:
            msg = await bot.send_message(chat_id, text, reply_markup=kb, message_thread_id=thread)
            if ttl:
                spawn(_del_later(chat_id, msg.message_id, ttl))
            return msg
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramAPIError as e:
            log.warning("send failed (%s): %s", chat_id, str(e)[:120])
            return None
    return None


def _thread(m: Message):
    return m.message_thread_id if getattr(m, "is_topic_message", False) else None


async def reply(m: Message, text: str, kb=None, ttl: float = None):
    return await send(m.chat.id, text, kb, ttl, _thread(m))


async def err(m: Message, text: str, ttl: float = 12):
    await reply(m, card("ERROR", "❌", text, "sans", m.chat.id), ttl=ttl)


async def usage(m: Message, fmt: str):
    await err(m, f"Sahi format:\n<code>{esc(fmt)}</code>")


async def fail(m: Message, e: Exception):
    await err(m, f"Telegram ne mana kiya:\n<i>{esc(str(e)[:140])}</i>\n\nBot ko admin rights check karo.")


async def safe_delete(m: Message):
    try:
        await m.delete()
    except TelegramAPIError:
        pass


async def edit(cb: CallbackQuery, text: str, kb=None):
    try:
        await bot.edit_message_text(text, chat_id=cb.message.chat.id,
                                    message_id=cb.message.message_id, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning("edit failed: %s", str(e)[:120])
    except TelegramAPIError as e:
        log.warning("edit failed: %s", str(e)[:120])


# =====================================================================
# DATABASE HELPERS + SETTINGS
# =====================================================================
DEFAULTS = {"protect": True, "warn_limit": 3, "action": "mute", "cleanservice": True,
            "welcome_on": True, "welcome": None, "rules": None, "font": "fancy"}
_SCACHE: dict = {}
SUDO_DB: set = set()


async def get_settings(chat_id: int) -> dict:
    hit = _SCACHE.get(chat_id)
    if hit and time.monotonic() - hit[1] < 60:
        return hit[0]
    s = dict(DEFAULTS)
    try:
        doc = await db.groups.find_one({"_id": chat_id}) or {}
        for k in DEFAULTS:
            if k in doc:
                s[k] = doc[k]
    except Exception as e:  # DB outage: keep working with last known / defaults
        log.warning("settings fallback for %s: %s", chat_id, str(e)[:100])
        if hit:
            return hit[0]
    _SCACHE[chat_id] = (s, time.monotonic())
    return s


async def set_setting(chat_id: int, **kw):
    await db.groups.update_one({"_id": chat_id}, {"$set": kw}, upsert=True)
    _SCACHE.pop(chat_id, None)
    if "font" in kw:
        (NORMAL_FONT.add if kw["font"] == "normal" else NORMAL_FONT.discard)(chat_id)


async def log_event(kind: str, text: str, chat=None, actor=None, channel: bool = True):
    plain = re.sub(r"<[^>]+>", "", text)
    try:
        await db.events.insert_one({"kind": kind, "text": plain[:500], "chat_id": chat,
                                    "actor": actor, "ts": now()})
    except Exception as e:
        log.warning("event store failed: %s", str(e)[:100])
    if channel and LOG_GROUP_ID:
        await send(LOG_GROUP_ID, card(f"LOG · {kind}", "🛡", text, "sans"))


# =====================================================================
# ROLES + PERMISSIONS (numeric IDs only - never usernames)
# =====================================================================
def is_owner(uid: int) -> bool:
    return uid == OWNER_ID


def is_sudo(uid: int) -> bool:
    return uid == OWNER_ID or uid in SUDO_ENV or uid in SUDO_DB


_STATUS: dict = {}


async def get_status(chat_id: int, uid: int) -> str:
    key = (chat_id, uid)
    hit = _STATUS.get(key)
    if hit and time.monotonic() - hit[1] < 20:
        return hit[0]
    try:
        m = await bot.get_chat_member(chat_id, uid)
        st = str(getattr(m.status, "value", m.status))
    except TelegramAPIError:
        st = "unknown"
    if len(_STATUS) > 5000:
        _STATUS.clear()
    _STATUS[key] = (st, time.monotonic())
    return st


async def actor_is_admin(chat_id: int, uid: int) -> bool:
    if is_sudo(uid):
        return True
    try:
        m = await bot.get_chat_member(chat_id, uid)
        return str(getattr(m.status, "value", m.status)) in ("administrator", "creator")
    except TelegramAPIError:
        return False


async def need(m: Message, right: str = None) -> bool:
    """Server-side authorization: sudo/owner, group creator, or admin with the right."""
    if m.sender_chat and m.sender_chat.id == m.chat.id:  # anonymous admin (Telegram-verified)
        return True
    u = m.from_user
    if not u:
        return False
    if is_sudo(u.id):
        return True
    ok = False
    try:
        mem = await bot.get_chat_member(m.chat.id, u.id)
        st = str(getattr(mem.status, "value", mem.status))
        if st == "creator":
            ok = True
        elif st == "administrator":
            ok = True if right is None else bool(getattr(mem, right, False))
    except TelegramAPIError:
        ok = False
    if not ok:
        await err(m, "Ye command sirf admins ke liye hai\n(ya aapke paas ye admin right nahi hai).", ttl=8)
    return ok


async def guard(m: Message, uid: int) -> bool:
    """Protect bot, Owner, Sudo and (for non-sudo actors) group admins."""
    if uid == BOT_ID:
        await err(m, "Main khud par action nahi le sakta 🙂")
        return False
    if is_sudo(uid):
        await err(m, "Owner/Sudo par action nahi liya ja sakta 🛡")
        return False
    actor = m.from_user
    if not (actor and is_sudo(actor.id)):
        if await get_status(m.chat.id, uid) in ("administrator", "creator"):
            await err(m, "Admin par action sirf Owner/Sudo le sakte hain.")
            return False
    return True


_VIP: dict = {}


async def is_vip(cid: int, uid: int) -> bool:
    k = (cid, uid)
    h = _VIP.get(k)
    if h and time.monotonic() - h[1] < 60:
        return h[0]
    try:
        v = await db.vips.find_one({"chat_id": cid, "user_id": uid}) is not None
    except Exception:
        v = False
    _VIP[k] = (v, time.monotonic())
    return v


async def exempt(cid: int, uid: int, admins_ok: bool) -> bool:
    if is_sudo(uid) or await is_vip(cid, uid):
        return True
    if admins_ok and await get_status(cid, uid) in ("administrator", "creator"):
        return True
    return False


# =====================================================================
# TARGET RESOLUTION + MISC UTILS
# =====================================================================
async def name_of(cid: int, uid: int) -> str:
    try:
        d = await db.members.find_one({"chat_id": cid, "user_id": uid})
        if d and d.get("name"):
            return d["name"]
    except Exception:
        pass
    return str(uid)


async def resolve(m: Message, command: CommandObject):
    """Return (uid, name, rest). Order: reply -> text_mention -> numeric ID -> tracked @username."""
    args = (command.args or "").strip()
    r = m.reply_to_message
    if r and r.forum_topic_created:
        r = None
    if r and r.from_user and not r.sender_chat:
        return r.from_user.id, r.from_user.full_name, args
    for e in (m.entities or []):
        if e.type == "text_mention" and e.user:
            rest = args.split(None, 1)[1] if len(args.split(None, 1)) > 1 else ""
            return e.user.id, e.user.full_name, rest
    parts = args.split(None, 1)
    if not parts:
        return None, "", ""
    first, rest = parts[0], (parts[1] if len(parts) > 1 else "")
    if first.lstrip("-").isdigit():
        uid = int(first)
        return uid, await name_of(m.chat.id, uid), rest
    if first.startswith("@"):
        try:
            d = await db.members.find_one({"username": first[1:].lower()})
        except Exception:
            d = None
        if d:
            return d["user_id"], d.get("name") or first, rest
    return None, "", ""


def parse_dur(s: str):
    mt = re.fullmatch(r"(\d{1,6})([smhdw])", (s or "").lower())
    if not mt:
        return None
    n = int(mt[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[mt[2]]
    return n if 30 <= n <= 366 * 86400 else None


def actor_mention(m: Message) -> str:
    if m.from_user and not m.sender_chat:
        return mention(m.from_user.id, m.from_user.full_name)
    return "Anonymous Admin"


async def do_mute(chat_id: int, uid: int, until=None):
    await bot.restrict_chat_member(chat_id, uid, permissions=ChatPermissions(can_send_messages=False),
                                   until_date=until)
    _STATUS.pop((chat_id, uid), None)


async def do_unmute(chat_id: int, uid: int):
    try:
        perms = (await bot.get_chat(chat_id)).permissions
    except TelegramAPIError:
        perms = None
    if perms is None or not getattr(perms, "can_send_messages", None):
        perms = ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True,
                                can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                                can_send_voice_notes=True, can_send_polls=True,
                                can_send_other_messages=True, can_add_web_page_previews=True,
                                can_invite_users=True)
    await bot.restrict_chat_member(chat_id, uid, permissions=perms)
    _STATUS.pop((chat_id, uid), None)


# =====================================================================
# PROFANITY ENGINE (Hindi / Hinglish / English)
# =====================================================================
BAD_TOKEN = {  # matched as whole words
    "mc", "bc", "bsdk", "mkc", "bkl", "bhosda", "bhosdi", "lund", "lode", "loda", "lauda", "lawda",
    "lavda", "gand", "gandu", "gaandu", "chut", "randi", "fuck", "fucker", "fucking", "bitch",
    "bastard", "asshole", "pussy", "slut", "whore", "cunt", "bhosadike", "bhosdiwale", "randwa",
}
BAD_LONG = (  # long words: matched even with spaces/symbols removed (evasion)
    "madarchod", "maderchod", "madrchod", "bhenchod", "behenchod", "bhosdike", "bhosdika",
    "chutiya", "chutiye", "chutia", "motherfucker", "randibaaz", "gaandu", "betichod",
)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
                       "@": "a", "$": "s", "!": "i"})


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = re.sub(r"[\u0300-\u036f]", "", t).translate(_LEET)
    return re.sub(r"(.)\1{2,}", r"\1", t)


def compact(t: str) -> str:
    return re.sub(r"[\W\d_]", "", t)


def has_bad(text: str, custom=()) -> bool:
    t = norm(text)
    tokens = set(re.findall(r"[^\W\d_]+", t))
    if tokens & BAD_TOKEN:
        return True
    comp = compact(t)
    if any(w in comp for w in BAD_LONG):
        return True
    for w in custom:
        if w in tokens or (len(w) >= 4 and w in comp):
            return True
    return False


_BAD: dict = {}


async def get_custom_bad(cid: int) -> set:
    h = _BAD.get(cid)
    if h and time.monotonic() - h[1] < 120:
        return h[0]
    try:
        words = {d["word"] async for d in db.badwords.find({"chat_id": cid})}
    except Exception:
        words = h[0] if h else set()
    _BAD[cid] = (words, time.monotonic())
    return words


LINK_RE = re.compile(
    r"(https?://|www\.|t\.me/|telegram\.me/|telegram\.dog/|"
    r"\b[a-z0-9-]{2,}\.(com|in|net|org|me|io|xyz|ly|gg|co|link|site|online|shop|top|club|app|info|tk|ru|cc|live|store)\b)",
    re.I)


def has_link(m: Message, text: str) -> bool:
    for e in list(m.entities or []) + list(m.caption_entities or []):
        if e.type in ("url", "text_link"):
            return True
    return bool(LINK_RE.search(text))


# anti flood / spam (in-memory, per chat+user)
_FLOOD: dict = defaultdict(lambda: deque(maxlen=12))
_LAST: dict = {}


def spam_reason(m: Message, text: str):
    key = (m.chat.id, m.from_user.id)
    t = time.monotonic()
    dq = _FLOOD[key]
    dq.append(t)
    if len(dq) >= 6 and t - dq[-6] <= 8:
        dq.clear()
        return "Flood (bahut tez messages)"
    if text and len(text) >= 3:
        prev = _LAST.get(key)
        if prev and prev[0] == text and t - prev[2] <= 30:
            cnt = prev[1] + 1
            _LAST[key] = (text, cnt, prev[2])
            if cnt >= 4:
                _LAST.pop(key, None)
                return "Spam (same message baar baar)"
        else:
            _LAST[key] = (text, 1, t)
    ments = [e for e in (m.entities or []) if e.type in ("mention", "text_mention")]
    if len(ments) >= 6:
        return "Mass mention"
    if len(_LAST) > 5000:
        _LAST.clear()
    return None


# raid detection
_JOINS: dict = defaultdict(lambda: deque(maxlen=40))
RAID_UNTIL: dict = {}


def raid_tick(chat_id: int) -> bool:
    """Record a join; return True if a raid has just been detected."""
    t = time.monotonic()
    dq = _JOINS[chat_id]
    dq.append(t)
    recent = sum(1 for x in dq if t - x <= 30)
    if recent >= 8 and RAID_UNTIL.get(chat_id, 0) < t:
        RAID_UNTIL[chat_id] = t + 300
        return True
    return False


def raid_active(chat_id: int) -> bool:
    return RAID_UNTIL.get(chat_id, 0) > time.monotonic()


# =====================================================================
# ROUTERS
# =====================================================================
common = Router()   # works in DM + groups
owner = Router()    # DM-only owner/sudo commands
owner.message.filter(F.chat.type == "private")
grp = Router()      # group-only commands + protection
grp.message.filter(F.chat.type.in_(GROUP_TYPES))
grp.edited_message.filter(F.chat.type.in_(GROUP_TYPES))

GROUP_ONLY = ("ban", "kick", "mute", "unmute", "unban", "tmute", "tban", "warn", "warns",
              "resetwarns", "del", "purge", "pin", "unpin", "promote", "demote", "admins",
              "protect", "addbadword", "delbadword", "all", "tagall", "tagadmins", "cancel",
              "setwelcome", "setrules", "settings", "addvip", "delvip", "viplist", "rules")

_TRACK: dict = {}


async def track(m: Message):
    u = m.from_user
    if not u or u.is_bot or m.sender_chat:
        return
    t = time.monotonic()
    if m.chat.type == "private":
        if t - _TRACK.get(("u", u.id), 0) > 600:
            _TRACK[("u", u.id)] = t
            await db.users.update_one({"_id": u.id}, {"$set": {"name": u.full_name, "last": now()},
                                                      "$setOnInsert": {"first": now()}}, upsert=True)
        return
    if m.chat.type not in GROUP_TYPES:
        return
    if t - _TRACK.get(("g", m.chat.id), 0) > 600:
        _TRACK[("g", m.chat.id)] = t
        await db.groups.update_one({"_id": m.chat.id}, {"$set": {"title": m.chat.title, "active": True,
                                                                  "type": m.chat.type}}, upsert=True)
    if t - _TRACK.get((m.chat.id, u.id), 0) > 600:
        _TRACK[(m.chat.id, u.id)] = t
        await db.members.update_one(
            {"chat_id": m.chat.id, "user_id": u.id},
            {"$set": {"name": u.full_name, "username": (u.username or "").lower() or None,
                      "active": True, "last_seen": now()},
             "$setOnInsert": {"first_seen": now()}}, upsert=True)
    if len(_TRACK) > 20000:
        _TRACK.clear()


class TrackMW(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            await track(event)
        except Exception as e:
            log.warning("track failed: %s", str(e)[:100])
        return await handler(event, data)


# =====================================================================
# /start  /help  /id  + generic callbacks
# =====================================================================
def welcome_card(name_html: str, title: str, cid=None) -> str:
    body = (f"Namaste {name_html} 👋\nAap ab <b>{esc(title)}</b> mein\njoin ho gaye ho ✨\n\n"
            "📌 Rules padh lena (/rules)\n🛡 Group GUARDIX se protected hai")
    return card("Welcome", "👑", body, "sans", cid)


def start_kb(sudo: bool) -> InlineKeyboardMarkup:
    add = (f"https://t.me/{BOT_USERNAME}?startgroup=true&admin=delete_messages+restrict_members"
           "+invite_users+pin_messages+promote_members")
    rows = [[InlineKeyboardButton(text="➕ Group mein add karo", url=add)],
            [btn("📖 Help", "help:home")]]
    if SUPPORT_URL.startswith(("http://", "https://", "tg://")):
        rows[1].append(InlineKeyboardButton(text="💬 Support", url=SUPPORT_URL))
    if sudo:
        rows.append([btn("👑 Control Panel", "panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def start_text(u) -> str:
    body = (f"Namaste {mention(u.id, u.first_name)} 👋\n"
            "Main aapke group ko safe aur organized rakhta hoon.\n\n"
            f"✨ {fx('Features', 'sans')}\n"
            "• 🚫 Gaali, spam aur link protection\n"
            "• ✅ Auto human verification\n"
            "• 📢 Tagall aur 👮 admin tools\n"
            "• ⭐ VIP system\n\n"
            "👇 Neeche se option chuno")
    return card("GUARDIX BOT", "🛡", body, "sans")


@common.message(Command("start"))
async def c_start(m: Message, command: CommandObject):
    u = m.from_user
    if m.chat.type != "private":
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="🛡 Bot ko DM mein kholo", url=f"https://t.me/{BOT_USERNAME}?start=help")]])
        return await reply(m, card("GUARDIX BOT", "🛡", "Poora menu DM mein milega 👇", "sans", m.chat.id),
                           kb, ttl=20)
    arg = (command.args or "").strip()
    if arg.startswith("welcome_"):
        try:
            cid = int(arg.split("_", 1)[1])
            title = (await bot.get_chat(cid)).title or "Group"
            return await send(m.chat.id, welcome_card(mention(u.id, u.first_name), title))
        except (ValueError, TelegramAPIError):
            pass
    await send(m.chat.id, start_text(u), start_kb(is_sudo(u.id)))


HELP = {
    "mod": ("MODERATION", "🚔", "/ban /unban /kick\n/mute /unmute\n/tmute 10m  /tban 1d\n/warn /warns /resetwarns\n"
            "/del /purge /pin /unpin\n/promote /demote\n\nReply karke ya user ID ke saath chalao."),
    "prot": ("PROTECTION", "🛡", "/protect on|off\n/addbadword word\n/delbadword word\n\n"
             "Auto: gaali delete + warning, spam/flood, link delete, raid protection, "
             "anti-bot add, edit check.\nCaptcha hamesha auto chalu rehta hai."),
    "tag": ("TAGALL", "📢", "/all &lt;msg&gt;\n/tagall [msg]\n/tagadmins\n/cancel\n\n"
            "Sirf admins. Sirf wahi members tag hote hain jinhe bot ne dekha hai."),
    "setup": ("GROUP SETUP", "⚙️", "/setwelcome text  ({name} {group})\n/setwelcome reset\n/setrules text\n"
              "/rules  /admins  /id\n/settings - control panel"),
    "vip": ("VIP", "⭐", "/addvip  /delvip  (reply ya ID)\n/viplist\n\nVIP par gaali, link aur spam filter nahi lagta."),
    "owner": ("OWNER / SUDO", "👑", "DM mein:\n/broadcast text\n/logs\n/sudolist\n/addsudo id  (sirf Owner)\n/delsudo id  (sirf Owner)"),
}


def help_kb(sudo: bool, uid: int) -> InlineKeyboardMarkup:
    rows = [[btn("🚔 Moderation", "help:mod"), btn("🛡 Protection", "help:prot")],
            [btn("📢 Tagall", "help:tag"), btn("⚙️ Setup", "help:setup")],
            [btn("⭐ VIP", "help:vip")]]
    if sudo:
        rows[-1].append(btn("👑 Owner", "help:owner"))
    rows.append([btn("✖ Close", f"close:{uid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def help_home(cid=None) -> str:
    return card("HELP", "📖", "Category chuno 👇\n\nProtection auto chalti hai, bas <code>/protect on</code> rakho.",
                "sans", cid)


@common.message(Command("help"))
async def c_help(m: Message):
    cid = m.chat.id if m.chat.type != "private" else None
    await reply(m, help_home(cid), help_kb(is_sudo(m.from_user.id), m.from_user.id))


@common.callback_query(F.data.startswith("help:"))
async def cb_help(cb: CallbackQuery):
    page = cb.data.split(":", 1)[1]
    cid = cb.message.chat.id if cb.message.chat.type != "private" else None
    sudo = is_sudo(cb.from_user.id)
    kb = help_kb(sudo, cb.from_user.id)
    if page == "home":
        await edit(cb, help_home(cid), kb)
    elif page in HELP and (page != "owner" or sudo):
        t, e, body = HELP[page]
        back = InlineKeyboardMarkup(inline_keyboard=[[btn("⬅ Back", "help:home"),
                                                      btn("✖ Close", f"close:{cb.from_user.id}")]])
        await edit(cb, card(t, e, body, "sans", cid), back)
    await cb.answer()


@common.message(Command("id"))
async def c_id(m: Message):
    u = m.from_user
    cid = m.chat.id if m.chat.type != "private" else None
    rows = [("👤", "You", f"<code>{u.id}</code>")]
    if m.chat.type != "private":
        rows.append(("💬", "Chat", f"<code>{m.chat.id}</code>"))
    r = m.reply_to_message
    if r and r.from_user:
        rows.append(("🎯", "Replied", f"<code>{r.from_user.id}</code>"))
    await reply(m, card("IDs", "🆔", kv(rows, "sans", cid), "sans", cid), close_kb(u.id))


@common.callback_query(F.data.startswith("close:"))
async def cb_close(cb: CallbackQuery):
    uid = int(cb.data.split(":")[1])
    ok = cb.from_user.id == uid or is_sudo(cb.from_user.id)
    if not ok and cb.message.chat.type in GROUP_TYPES:
        ok = await actor_is_admin(cb.message.chat.id, cb.from_user.id)
    if not ok:
        return await cb.answer("Ye aapke liye nahi hai.", show_alert=True)
    try:
        await bot.delete_message(cb.message.chat.id, cb.message.message_id)
    except TelegramAPIError:
        pass
    await cb.answer()


@common.callback_query(F.data == "panel")
async def cb_panel(cb: CallbackQuery):
    if not is_sudo(cb.from_user.id):
        return await cb.answer("Sirf Owner/Sudo.", show_alert=True)
    try:
        users = await db.users.count_documents({})
        groups = await db.groups.count_documents({"active": True, "type": {"$in": list(GROUP_TYPES)}})
        members = await db.members.count_documents({"active": True})
    except Exception:
        users = groups = members = "?"
    body = kv([("👤", "Users", users), ("💬", "Groups", groups), ("👥", "Tracked members", members),
               ("🛡", "Sudo", len({OWNER_ID} | SUDO_ENV | SUDO_DB))], "sans")
    body += "\n\n/broadcast  /logs  /sudolist\n/addsudo  /delsudo (Owner)"
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("🔄 Refresh", "panel"), btn("⬅ Back", "home")]])
    await edit(cb, card("CONTROL PANEL", "👑", body, "sans"), kb)
    await cb.answer()


@common.callback_query(F.data == "home")
async def cb_home(cb: CallbackQuery):
    await edit(cb, start_text(cb.from_user), start_kb(is_sudo(cb.from_user.id)))
    await cb.answer()


# =====================================================================
# CAPTCHA via JOIN REQUEST (always automatic)
# =====================================================================
CAP_MAX = 3
CAP_BLOCK: dict = {}
APPROVED: dict = {}


def make_question():
    a, b = random.randint(1, 9), random.randint(1, 9)
    if a > b and random.random() < 0.4:
        q, ans = f"{a} − {b}", a - b
    else:
        q, ans = f"{a} + {b}", a + b
    opts = {ans}
    while len(opts) < 4:
        opts.add(max(0, ans + random.randint(-4, 4)))
    opts = list(opts)
    random.shuffle(opts)
    return q, ans, opts


async def send_captcha(uid: int, dm_chat: int, chat_id: int, title: str,
                       attempts: int = 0, cb: CallbackQuery = None):
    q, ans, opts = make_question()
    nonce = secrets.token_hex(4)
    await db.verif.insert_one({"nonce": nonce, "uid": uid, "chat_id": chat_id, "title": title,
                               "answer": ans, "attempts": attempts, "used": False,
                               "created": now(), "expires_at": now() + timedelta(minutes=10)})
    body = (f"Hi 👋\n<b>{esc(title)}</b> join karne ke liye\nek chhota sa sawal:\n\n"
            f"❓ <b>{q} = ?</b>\n\n🎯 Try {attempts + 1}/{CAP_MAX}  ·  ⏳ 10 min")
    text = card("Human Verification", "🤖", body, "sans")
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn(str(o), f"cap:{nonce}:{o}") for o in opts]])
    if cb:
        await edit(cb, text, kb)
    else:
        await send(dm_chat, text, kb)


@common.chat_join_request()
async def on_join_request(req: ChatJoinRequest):
    u = req.from_user
    if is_sudo(u.id):
        try:
            await req.approve()
        except TelegramAPIError:
            pass
        return
    if CAP_BLOCK.get((u.id, req.chat.id), 0) > time.monotonic():
        return
    try:
        await send_captcha(u.id, req.user_chat_id, req.chat.id, req.chat.title or "Group")
    except Exception as e:
        log.warning("captcha send failed: %s", str(e)[:120])


@common.callback_query(F.data.startswith("cap:"))
async def cb_captcha(cb: CallbackQuery):
    try:
        _, nonce, val = cb.data.split(":")
        val = int(val)
    except ValueError:
        return await cb.answer()
    s = await db.verif.find_one({"nonce": nonce})
    if not s or s["used"]:
        return await cb.answer("Ye sawal expire ho gaya.", show_alert=True)
    if cb.from_user.id != s["uid"]:
        return await cb.answer("Ye aapke liye nahi hai.", show_alert=True)
    if now() > s["expires_at"]:
        await db.verif.update_one({"nonce": nonce}, {"$set": {"used": True}})
        await edit(cb, card("Expired", "⌛", "Verification ka time khatam.\nRequest pending hai,\ndobara join request bhejo.", "sans"))
        return await cb.answer()
    claimed = await db.verif.update_one({"nonce": nonce, "used": False}, {"$set": {"used": True}})
    if claimed.modified_count == 0:  # replay protection
        return await cb.answer()
    chat_id, title = s["chat_id"], s["title"]
    if val == s["answer"]:
        try:
            await bot.approve_chat_join_request(chat_id, s["uid"])
            APPROVED[(chat_id, s["uid"])] = time.monotonic()
            body = f"🎉 Verify ho gaye!\nAapki request approve ho gayi,\n<b>{esc(title)}</b> mein aa jao ✨"
            await edit(cb, card("Verified", "✅", body, "sans"))
        except TelegramAPIError:
            await edit(cb, card("Verified", "✅", "Sahi jawab! Lekin request ab valid nahi hai\n(pehle hi handle ho chuki).", "sans"))
        return await cb.answer("Sahi jawab ✅")
    attempts = s["attempts"] + 1
    if attempts >= CAP_MAX:
        CAP_BLOCK[(s["uid"], chat_id)] = time.monotonic() + 300
        body = "Teen baar galat jawab.\nAapki request <b>pending</b> rahegi,\nadmin chahe to manually approve kar sakta hai."
        await edit(cb, card("Wrong Answer", "❌", body, "sans"))
        return await cb.answer("Galat jawab", show_alert=True)
    await cb.answer("❌ Galat jawab, dobara try karo")
    await send_captcha(s["uid"], cb.message.chat.id, chat_id, title, attempts, cb)


# =====================================================================
# BOT ADDED / REMOVED ALERT
# =====================================================================
@common.my_chat_member()
async def on_my_status(upd: ChatMemberUpdated):
    chat = upd.chat
    if chat.type not in GROUP_TYPES:
        return
    new = upd.new_chat_member
    st = str(getattr(new.status, "value", new.status))
    by = upd.from_user
    if st in ("member", "administrator"):
        try:
            await db.groups.update_one({"_id": chat.id}, {"$set": {"title": chat.title, "active": True,
                                                                    "type": chat.type}}, upsert=True)
        except Exception:
            pass
        missing = []
        if st == "administrator":
            for right, label in (("can_delete_messages", "Delete messages"), ("can_restrict_members", "Ban/Restrict users"),
                                 ("can_invite_users", "Invite users (join requests)"), ("can_pin_messages", "Pin messages"),
                                 ("can_promote_members", "Add admins")):
                if not getattr(new, right, False):
                    missing.append(label)
        text = kv([("💬", "Group", esc(chat.title or "?")), ("🆔", "ID", f"<code>{chat.id}</code>"),
                   ("👤", "Added by", mention(by.id, by.full_name) if by else "?"),
                   ("🔑", "Admin", "Yes ✅" if st == "administrator" else "No ❌")], "sans")
        if missing:
            text += "\n⚠️ Missing: " + ", ".join(missing)
        await log_event("BOT ADDED", text, chat.id, by.id if by else None)
        if st == "member" or missing:
            need_txt = ("Mujhe <b>admin</b> banao taaki main kaam kar sakun.\n\nZaroori rights:\n"
                        "• Delete messages\n• Ban users\n• Invite users\n• Pin messages"
                        if st == "member" else "Ye admin rights kam hain:\n• " + "\n• ".join(missing))
            await send(chat.id, card("Setup Needed", "⚠️", need_txt, "sans", chat.id))
    elif st in ("left", "kicked"):
        try:
            await db.groups.update_one({"_id": chat.id}, {"$set": {"active": False}})
        except Exception:
            pass
        await log_event("BOT REMOVED", kv([("💬", "Group", esc(chat.title or "?")),
                                           ("🆔", "ID", f"<code>{chat.id}</code>")], "sans"), chat.id)


# =====================================================================
# OWNER / SUDO COMMANDS (DM only)
# =====================================================================
BCAST: dict = {}


@owner.message(Command(*GROUP_ONLY))
async def c_group_only(m: Message):
    await send(m.chat.id, card("Group Command", "ℹ️", "Ye command group mein use karo.", "sans"))


@owner.message(Command("broadcast"))
async def c_broadcast(m: Message, command: CommandObject):
    if not is_sudo(m.from_user.id):
        return
    text = (command.args or "").strip()
    if not text:
        return await usage(m, "/broadcast <message>")
    try:
        groups = await db.groups.count_documents({"active": True, "type": {"$in": list(GROUP_TYPES)}})
        users = await db.users.count_documents({})
    except Exception:
        return await err(m, "Database abhi available nahi hai.")
    token = secrets.token_hex(4)
    BCAST[token] = {"uid": m.from_user.id, "text": text}
    body = f"{esc(text[:600])}\n\n{kv([('💬', 'Groups', groups), ('👤', 'Users', users)], 'sans')}\n\nBhejna hai?"
    kb = InlineKeyboardMarkup(inline_keyboard=[[btn("✅ Confirm", f"bc:ok:{token}"), btn("❌ Cancel", f"bc:no:{token}")]])
    await send(m.chat.id, card("Broadcast Preview", "📡", body, "sans"), kb)


@common.callback_query(F.data.startswith("bc:"))
async def cb_broadcast(cb: CallbackQuery):
    _, act, token = cb.data.split(":")
    job = BCAST.get(token)
    if not job or job["uid"] != cb.from_user.id or not is_sudo(cb.from_user.id):
        return await cb.answer("Expired ya aapke liye nahi.", show_alert=True)
    BCAST.pop(token, None)
    if act == "no":
        await edit(cb, card("Cancelled", "🛑", "Broadcast cancel.", "sans"))
        return await cb.answer()
    await edit(cb, card("Broadcasting", "📡", "Bhej raha hoon... ⏳", "sans"))
    await cb.answer()
    spawn(run_broadcast(cb.from_user.id, job["text"], cb.message.chat.id, cb.message.message_id))


async def run_broadcast(uid: int, text: str, chat: int, mid: int):
    ids = []
    try:
        ids += [d["_id"] async for d in db.groups.find({"active": True, "type": {"$in": list(GROUP_TYPES)}})]
        ids += [d["_id"] async for d in db.users.find({})]
    except Exception:
        pass
    ok = bad = 0
    msg = card("Announcement", "📡", esc(text), "sans")
    for cid in ids:
        r = await send(cid, msg)
        ok += 1 if r else 0
        bad += 0 if r else 1
        await asyncio.sleep(0.06)
    body = kv([("✅", "Sent", ok), ("❌", "Failed", bad)], "sans")
    try:
        await bot.edit_message_text(card("Broadcast Done", "📡", body, "sans"), chat_id=chat, message_id=mid)
    except TelegramAPIError:
        pass
    await log_event("BROADCAST", body, None, uid)


@owner.message(Command("logs"))
async def c_logs(m: Message):
    if not is_sudo(m.from_user.id):
        return
    try:
        docs = [d async for d in db.events.find({"kind": {"$ne": "FILTER"}}).sort("ts", DESCENDING).limit(15)]
    except Exception:
        return await err(m, "Database abhi available nahi hai.")
    if not docs:
        return await send(m.chat.id, card("Logs", "📜", "Abhi koi log nahi.", "sans"))
    lines = [f"<b>{esc(d['kind'])}</b> · {fmt_dt(d['ts'])}\n<i>{esc(d['text'].replace(chr(10), ' | ')[:160])}</i>" for d in docs]
    await send(m.chat.id, card("Recent Logs", "📜", "\n\n".join(lines), "sans"))


@owner.message(Command("sudolist"))
async def c_sudolist(m: Message):
    if not is_sudo(m.from_user.id):
        return
    lines = [f"👑 Owner: <code>{OWNER_ID}</code>"]
    lines += [f"🛡 Sudo (env): <code>{x}</code>" for x in sorted(SUDO_ENV - {OWNER_ID})]
    lines += [f"🛡 Sudo: <code>{x}</code>" for x in sorted(SUDO_DB - SUDO_ENV - {OWNER_ID})]
    await send(m.chat.id, card("Sudo List", "🛡", "\n".join(lines), "sans"))


@owner.message(Command("addsudo", "delsudo"))
async def c_sudo_edit(m: Message, command: CommandObject):
    if not is_owner(m.from_user.id):
        return await err(m, "Sirf Owner ye command chala sakta hai.")
    arg = (command.args or "").strip()
    if not arg.isdigit():
        return await usage(m, f"/{command.command} <numeric user id>")
    uid = int(arg)
    if command.command == "addsudo":
        if uid == OWNER_ID or is_sudo(uid):
            return await err(m, "Ye pehle se Owner/Sudo hai.")
        await db.sudos.update_one({"_id": uid}, {"$set": {"by": m.from_user.id, "ts": now()}}, upsert=True)
        SUDO_DB.add(uid)
        title, em = "Sudo Added", "✅"
    else:
        if uid in SUDO_ENV:
            return await err(m, "Ye Railway variable <code>SUDO_IDS</code> mein hai, wahin se hataiye.")
        await db.sudos.delete_one({"_id": uid})
        SUDO_DB.discard(uid)
        title, em = "Sudo Removed", "🗑"
    body = kv([("🆔", "User", f"<code>{uid}</code>")], "sans")
    await send(m.chat.id, card(title, em, body, "sans"))
    await log_event(title.upper(), body, None, m.from_user.id)


# =====================================================================
# MODERATION COMMANDS
# =====================================================================
MOD_META = {"ban": ("BAN DONE", "🚫"), "kick": ("KICKED", "👢"), "mute": ("MUTED", "🔇"),
            "unmute": ("UNMUTED", "🔊"), "unban": ("UNBANNED", "✅"),
            "tmute": ("TEMP MUTE", "⏳"), "tban": ("TEMP BAN", "⏳")}


@grp.message(Command(*MOD_META.keys()))
async def c_mod(m: Message, command: CommandObject):
    cmd = command.command.lower()
    if not await need(m, "can_restrict_members"):
        return
    uid, name, rest = await resolve(m, command)
    if not uid:
        return await usage(m, f"/{cmd} " + ("10m " if cmd in ("tmute", "tban") else "") + "(reply / user ID / @username)")
    until, dur, reason = None, None, rest
    if cmd in ("tmute", "tban"):
        parts = rest.split(None, 1)
        secs = parse_dur(parts[0]) if parts else None
        if not secs:
            return await usage(m, f"/{cmd} 10m [reason]   (s/m/h/d/w, min 30s)")
        until, dur, reason = now() + timedelta(seconds=secs), parts[0], (parts[1] if len(parts) > 1 else "")
    if cmd not in ("unban", "unmute") and not await guard(m, uid):
        return
    try:
        if cmd in ("ban", "tban"):
            await bot.ban_chat_member(m.chat.id, uid, until_date=until)
        elif cmd == "kick":
            await bot.ban_chat_member(m.chat.id, uid)
            await bot.unban_chat_member(m.chat.id, uid, only_if_banned=True)
        elif cmd in ("mute", "tmute"):
            await do_mute(m.chat.id, uid, until)
        elif cmd == "unmute":
            await do_unmute(m.chat.id, uid)
        elif cmd == "unban":
            await bot.unban_chat_member(m.chat.id, uid, only_if_banned=True)
    except TelegramAPIError as e:
        return await fail(m, e)
    _STATUS.pop((m.chat.id, uid), None)
    title, emoji = MOD_META[cmd]
    rows = [("👤", "User", mention(uid, name)), ("🆔", "ID", f"<code>{uid}</code>"), ("👮", "By", actor_mention(m))]
    if dur:
        rows.append(("⏱", "Time", dur))
    if reason:
        rows.append(("📝", "Reason", esc(reason[:200])))
    body = kv(rows, "sans", m.chat.id)
    await reply(m, card(title, emoji, body, "sans", m.chat.id), close_kb(m.from_user.id if m.from_user else 0))
    await log_event(cmd.upper(), kv(rows + [("💬", "Group", esc(m.chat.title or ""))], "sans"), m.chat.id,
                    m.from_user.id if m.from_user else None)


async def apply_warn(chat_id: int, uid: int, name: str, reason: str, by_html=None, thread=None):
    s = await get_settings(chat_id)
    t = now()
    await db.warns.insert_one({"chat_id": chat_id, "user_id": uid, "reason": reason[:200],
                               "ts": t, "expires_at": t + timedelta(days=7)})
    count = await db.warns.count_documents({"chat_id": chat_id, "user_id": uid, "expires_at": {"$gt": t}})
    limit = s["warn_limit"]
    rows = [("👤", "User", mention(uid, name)), ("📝", "Reason", esc(reason))]
    if by_html:
        rows.append(("👮", "By", by_html))
    if count >= limit:
        await db.warns.delete_many({"chat_id": chat_id, "user_id": uid})
        try:
            if s["action"] == "ban":
                await bot.ban_chat_member(chat_id, uid)
                title, em, what = "BANNED", "🚫", "Banned"
            else:
                await do_mute(chat_id, uid, t + timedelta(hours=24))
                title, em, what = "MUTED 24H", "🔇", "Muted (24h)"
            rows.append(("⚖️", "Action", f"{what} - warning limit {limit}/{limit}"))
            await send(chat_id, card(title, em, kv(rows, "sans", chat_id), "sans", chat_id), ttl=60, thread=thread)
            await log_event("AUTO " + title, kv(rows, "sans"), chat_id, uid)
        except TelegramAPIError as e:
            log.info("warn action failed: %s", str(e)[:100])
    else:
        rows.append(("⚠️", "Warning", f"{count}/{limit}"))
        await send(chat_id, card(f"WARNING {count}/{limit}", "⚠️", kv(rows, "sans", chat_id) +
                                 "\n\nAgli baar dhyan rakho 🙏", "sans", chat_id), ttl=30, thread=thread)
    return count


@grp.message(Command("warn"))
async def c_warn(m: Message, command: CommandObject):
    if not await need(m, "can_restrict_members"):
        return
    uid, name, reason = await resolve(m, command)
    if not uid:
        return await usage(m, "/warn [reason]  (reply / user ID)")
    if not await guard(m, uid):
        return
    try:
        await apply_warn(m.chat.id, uid, name, reason or "Admin warning", actor_mention(m), _thread(m))
    except Exception as e:
        await err(m, f"Warn save nahi ho paya: {esc(str(e)[:80])}")


@grp.message(Command("warns", "resetwarns"))
async def c_warns(m: Message, command: CommandObject):
    if not await need(m, "can_restrict_members"):
        return
    uid, name, _ = await resolve(m, command)
    if not uid:
        return await usage(m, f"/{command.command}  (reply / user ID)")
    s = await get_settings(m.chat.id)
    if command.command == "resetwarns":
        await db.warns.delete_many({"chat_id": m.chat.id, "user_id": uid})
        return await reply(m, card("WARNS RESET", "♻️", kv([("👤", "User", mention(uid, name))], "sans", m.chat.id),
                                   "sans", m.chat.id), close_kb(m.from_user.id))
    docs = [d async for d in db.warns.find({"chat_id": m.chat.id, "user_id": uid, "expires_at": {"$gt": now()}}).sort("ts", ASCENDING)]
    lines = [f"{i}. {esc(d['reason'])} <i>({fmt_dt(d['ts'])})</i>" for i, d in enumerate(docs, 1)] or ["Koi active warning nahi ✅"]
    body = kv([("👤", "User", mention(uid, name)), ("⚠️", "Warns", f"{len(docs)}/{s['warn_limit']}")], "sans", m.chat.id)
    await reply(m, card("WARNINGS", "⚠️", body + "\n\n" + "\n".join(lines), "sans", m.chat.id), close_kb(m.from_user.id))


@grp.message(Command("del"))
async def c_del(m: Message):
    if not await need(m, "can_delete_messages"):
        return
    r = m.reply_to_message
    if not r:
        return await usage(m, "/del  (kisi message par reply karo)")
    for x in (r, m):
        await safe_delete(x)


@grp.message(Command("purge"))
async def c_purge(m: Message):
    if not await need(m, "can_delete_messages"):
        return
    r = m.reply_to_message
    if not r:
        return await usage(m, "/purge  (jahan se delete karna hai us message par reply karo)")
    ids = list(range(r.message_id, m.message_id + 1))[-200:]
    for i in range(0, len(ids), 100):
        try:
            await bot.delete_messages(m.chat.id, ids[i:i + 100])
        except TelegramAPIError:
            pass
    await send(m.chat.id, card("PURGED", "🧹", kv([("🗑", "Messages", len(ids))], "sans", m.chat.id), "sans", m.chat.id),
               ttl=6, thread=_thread(m))


@grp.message(Command("pin", "unpin"))
async def c_pin(m: Message, command: CommandObject):
    if not await need(m, "can_pin_messages"):
        return
    r = m.reply_to_message
    try:
        if command.command == "pin":
            if not r:
                return await usage(m, "/pin [quiet]  (message par reply)")
            await bot.pin_chat_message(m.chat.id, r.message_id,
                                       disable_notification=(command.args or "").strip().lower() == "quiet")
            em, t = "📌", "PINNED"
        else:
            await bot.unpin_chat_message(m.chat.id, message_id=r.message_id if r else None)
            em, t = "📍", "UNPINNED"
    except TelegramAPIError as e:
        return await fail(m, e)
    await reply(m, card(t, em, kv([("👮", "By", actor_mention(m))], "sans", m.chat.id), "sans", m.chat.id), ttl=10)


@grp.message(Command("promote", "demote"))
async def c_promote(m: Message, command: CommandObject):
    if not await need(m, "can_promote_members"):
        return
    uid, name, _ = await resolve(m, command)
    if not uid:
        return await usage(m, f"/{command.command}  (reply / user ID)")
    if uid == BOT_ID or (command.command == "demote" and is_sudo(uid)):
        return await err(m, "Is user par ye action nahi ho sakta.")
    up = command.command == "promote"
    try:
        await bot.promote_chat_member(m.chat.id, uid, can_manage_chat=up, can_delete_messages=up,
                                      can_restrict_members=up, can_pin_messages=up, can_invite_users=up,
                                      can_manage_video_chats=up, can_promote_members=False,
                                      can_change_info=False)
    except TelegramAPIError as e:
        return await fail(m, e)
    _STATUS.pop((m.chat.id, uid), None)
    rows = [("👤", "User", mention(uid, name)), ("👮", "By", actor_mention(m))]
    await reply(m, card("PROMOTED" if up else "DEMOTED", "⬆️" if up else "⬇️", kv(rows, "sans", m.chat.id),
                        "sans", m.chat.id), close_kb(m.from_user.id))
    await log_event("PROMOTE" if up else "DEMOTE", kv(rows, "sans"), m.chat.id, m.from_user.id)


# ---- /admins (paginated, live from Telegram) ----
async def admins_view(chat_id: int, page: int, uid: int):
    chat = await bot.get_chat(chat_id)
    adm = await bot.get_chat_administrators(chat_id)
    owner_ = [a for a in adm if str(getattr(a.status, "value", a.status)) == "creator"]
    others = [a for a in adm if a not in owner_]
    per = 10
    pages = max(1, (len(others) + per - 1) // per)
    page = min(max(page, 0), pages - 1)

    def line(a):
        u = a.user
        tag = " 👑 Bot Owner" if is_owner(u.id) else (" 🛡 Sudo" if is_sudo(u.id) else "")
        title = f" · <i>{esc(a.custom_title)}</i>" if getattr(a, "custom_title", None) else ""
        bot_tag = " 🤖" if u.is_bot else ""
        return f"• {mention(u.id, u.full_name)}{bot_tag}{title}{tag}"

    s = "small" if chat_id not in NORMAL_FONT else "plain"
    body = f"💬 <b>{esc(chat.title or '')}</b>\n\n"
    body += f"👑 {fx('owner', s)}: " + (line(owner_[0])[2:] if owner_ else "Anonymous / unknown") + "\n\n"
    body += f"⭐ {fx('admins', s)} ({len(others)}):\n" + ("\n".join(line(a) for a in others[page * per:(page + 1) * per]) or "—")
    nav = []
    if page > 0:
        nav.append(btn("⬅", f"adm:{chat_id}:{page - 1}"))
    nav.append(btn(f"{page + 1}/{pages}", f"adm:{chat_id}:{page}"))
    if page < pages - 1:
        nav.append(btn("➡", f"adm:{chat_id}:{page + 1}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[nav, [btn("🔄 Refresh", f"adm:{chat_id}:{page}"), btn("✖ Close", f"close:{uid}")]])
    return card("Admin List", "👮", body, "small", chat_id), kb


@grp.message(Command("admins"))
async def c_admins(m: Message):
    try:
        text, kb = await admins_view(m.chat.id, 0, m.from_user.id)
    except TelegramAPIError:
        return await err(m, "Admin list abhi nahi mil paayi.")
    await reply(m, text, kb)


@common.callback_query(F.data.startswith("adm:"))
async def cb_admins(cb: CallbackQuery):
    _, cid, page = cb.data.split(":")
    try:
        text, kb = await admins_view(int(cid), int(page), cb.from_user.id)
    except TelegramAPIError:
        return await cb.answer("Admin list nahi mil paayi.", show_alert=True)
    await edit(cb, text, kb)
    await cb.answer("Updated")


# =====================================================================
# PROTECTION COMMANDS
# =====================================================================
def protect_card(on: bool, cid) -> str:
    mark = "✅" if on else "❌"
    s = _st("serif", cid)
    body = "\n".join(f"{mark} {fx(x, s)}" for x in ("Gaali filter", "Spam aur flood", "Link protection", "Raid protection"))
    body += "\n✅ " + fx("Auto captcha (hamesha on)", s)
    return card("Protection " + ("ON" if on else "OFF"), "🛡", body, "serif", cid)


@grp.message(Command("protect"))
async def c_protect(m: Message, command: CommandObject):
    if not await need(m):
        return
    arg = (command.args or "").strip().lower()
    if arg not in ("on", "off"):
        s = await get_settings(m.chat.id)
        return await reply(m, protect_card(s["protect"], m.chat.id) + "\n\n<code>/protect on|off</code>", close_kb(m.from_user.id))
    await set_setting(m.chat.id, protect=(arg == "on"))
    await reply(m, protect_card(arg == "on", m.chat.id), close_kb(m.from_user.id))
    await log_event("PROTECT " + arg.upper(), kv([("💬", "Group", esc(m.chat.title or "")), ("👮", "By", actor_mention(m))], "sans"),
                    m.chat.id, m.from_user.id)


@grp.message(Command("addbadword", "delbadword"))
async def c_badword(m: Message, command: CommandObject):
    if not await need(m):
        return
    words = {compact(norm(w)) for w in re.split(r"[,\s]+", (command.args or "")) if w.strip()}
    words = {w for w in words if len(w) >= 2}
    await safe_delete(m)  # the command itself contains the word
    if not words:
        return await usage(m, f"/{command.command} word1 word2 ...")
    add = command.command == "addbadword"
    for w in words:
        if add:
            await db.badwords.update_one({"chat_id": m.chat.id, "word": w}, {"$set": {"ts": now()}}, upsert=True)
        else:
            await db.badwords.delete_one({"chat_id": m.chat.id, "word": w})
    _BAD.pop(m.chat.id, None)
    body = kv([("🔤", "Words", len(words)), ("👮", "By", actor_mention(m))], "serif", m.chat.id)
    await send(m.chat.id, card("Filter " + ("Updated" if add else "Removed"), "🛡", body, "serif", m.chat.id), ttl=10)


# =====================================================================
# TAGALL
# =====================================================================
TAG_TASKS: dict = {}
TAG_LAST: dict = {}
DEFAULT_TAGS = ["Sab log online aao! 🔥", "Group mein halchal macha do 😎", "Soye hue log uth jao ⏰",
                "Ek zaroori announcement hai 📢", "Sab ek baar hazri lagao ✋", "Chalo sab active ho jao 🚀"]


async def collect_tracked(chat_id: int):
    people = {}
    try:
        async for d in db.members.find({"chat_id": chat_id, "active": True}).sort("last_seen", DESCENDING).limit(300):
            people[d["user_id"]] = d.get("name") or str(d["user_id"])
    except Exception as e:
        log.warning("tracked fetch failed: %s", str(e)[:100])
    try:
        for a in await bot.get_chat_administrators(chat_id):
            if not a.user.is_bot:
                people.setdefault(a.user.id, a.user.full_name)
    except TelegramAPIError:
        pass
    people.pop(BOT_ID, None)
    return list(people.items())[:300]


async def run_tag(chat_id: int, text: str, people, thread=None):
    try:
        head = card("Tag All", "📢", f"{text}\n\n👥 {len(people)} members", "mono", chat_id)
        await send(chat_id, head, thread=thread)
        for i in range(0, len(people), 5):
            await send(chat_id, "👥 " + " ".join(mention(u, n[:15]) for u, n in people[i:i + 5]), thread=thread)
            await asyncio.sleep(2.5)
        await send(chat_id, card("Tag Done", "✅", "Tagging poori ho gayi.", "mono", chat_id), ttl=20, thread=thread)
    except asyncio.CancelledError:
        await send(chat_id, card("Tag Stopped", "🛑", "Tagging rok di gayi.", "mono", chat_id), ttl=15, thread=thread)
        raise


@grp.message(Command("all", "tagall"))
async def c_tag(m: Message, command: CommandObject):
    if not await need(m):
        return
    cid = m.chat.id
    t = TAG_TASKS.get(cid)
    if t and not t.done():
        return await err(m, "Tagging pehle se chal rahi hai. /cancel se roko.")
    text = (command.args or "").strip()
    if not text:
        if command.command == "all":
            return await usage(m, "/all <message>")
        text = random.choice(DEFAULT_TAGS)
    if not is_sudo(m.from_user.id if m.from_user else 0):
        left = 600 - (time.monotonic() - TAG_LAST.get(cid, -1e9))
        if left > 0:
            return await err(m, f"Cooldown: {int(left // 60) + 1} minute baad dobara chalao.")
    people = await collect_tracked(cid)
    if not people:
        return await err(m, "Abhi koi tracked member nahi.\nMembers tab judte hain jab wo message karte hain.")
    TAG_LAST[cid] = time.monotonic()
    TAG_TASKS[cid] = spawn(run_tag(cid, esc(text[:500]), people, _thread(m)))


@grp.message(Command("tagadmins"))
async def c_tagadmins(m: Message):
    if not await need(m):
        return
    try:
        adm = [a.user for a in await bot.get_chat_administrators(m.chat.id) if not a.user.is_bot]
    except TelegramAPIError as e:
        return await fail(m, e)
    await reply(m, card("Tag Admins", "📢", "Admins ko bulaya ja raha hai 👇", "mono", m.chat.id))
    for i in range(0, len(adm), 5):
        await send(m.chat.id, "👮 " + " ".join(mention(u.id, u.full_name[:15]) for u in adm[i:i + 5]), thread=_thread(m))
        await asyncio.sleep(1.5)


@grp.message(Command("cancel"))
async def c_cancel(m: Message):
    if not await need(m):
        return
    t = TAG_TASKS.get(m.chat.id)
    if t and not t.done():
        t.cancel()
    else:
        await err(m, "Koi tagging chal nahi rahi.", ttl=6)


# =====================================================================
# WELCOME / RULES / VIP
# =====================================================================
def fill(text: str, uid: int, name: str, title: str) -> str:
    return esc(text).replace("{name}", mention(uid, name)).replace("{group}", f"<b>{esc(title)}</b>")


@grp.message(Command("setwelcome"))
async def c_setwelcome(m: Message, command: CommandObject):
    if not await need(m):
        return
    arg = (command.args or "").strip()
    if not arg:
        return await usage(m, "/setwelcome <text>   ({name} {group} use kar sakte ho)\n/setwelcome reset")
    await set_setting(m.chat.id, welcome=None if arg.lower() == "reset" else arg[:800])
    await reply(m, card("Welcome Set", "👋", "Default welcome wapas laga diya ✅" if arg.lower() == "reset" else "Naya welcome save ho gaya ✅",
                        "sans", m.chat.id), ttl=10)


@grp.message(Command("setrules"))
async def c_setrules(m: Message, command: CommandObject):
    if not await need(m):
        return
    arg = (command.args or "").strip()
    if not arg:
        return await usage(m, "/setrules <rules text>")
    await set_setting(m.chat.id, rules=arg[:1500])
    await reply(m, card("Rules Set", "📜", "Rules save ho gaye ✅", "sans", m.chat.id), ttl=10)


@grp.message(Command("rules"))
async def c_rules(m: Message):
    s = await get_settings(m.chat.id)
    body = esc(s["rules"]) if s["rules"] else ("1. Sabki izzat karo\n2. Gaali aur spam nahi\n3. Bina permission link nahi\n"
                                               "4. Admin ki baat maano")
    await reply(m, card("Group Rules", "📜", body, "sans", m.chat.id), close_kb(m.from_user.id))


@grp.message(Command("addvip", "delvip"))
async def c_vip(m: Message, command: CommandObject):
    if not await need(m):
        return
    uid, name, _ = await resolve(m, command)
    if not uid:
        return await usage(m, f"/{command.command}  (reply / user ID)")
    add = command.command == "addvip"
    if add:
        await db.vips.update_one({"chat_id": m.chat.id, "user_id": uid},
                                 {"$set": {"name": name, "by": m.from_user.id, "ts": now()}}, upsert=True)
    else:
        await db.vips.delete_one({"chat_id": m.chat.id, "user_id": uid})
    _VIP.pop((m.chat.id, uid), None)
    body = (f"{mention(uid, name)} ab VIP hai ✨\nFilter aur warning ab inpe nahi lagegi." if add
            else f"{mention(uid, name)} ka VIP status hata diya gaya.")
    await reply(m, card("VIP Member" if add else "VIP Removed", "⭐", body, "script", m.chat.id), close_kb(m.from_user.id))
    await log_event("VIP ADD" if add else "VIP DEL", kv([("👤", "User", mention(uid, name)),
                                                         ("💬", "Group", esc(m.chat.title or ""))], "sans"), m.chat.id, m.from_user.id)


@grp.message(Command("viplist"))
async def c_viplist(m: Message):
    docs = [d async for d in db.vips.find({"chat_id": m.chat.id}).limit(50)]
    body = "\n".join(f"{i}. {mention(d['user_id'], d.get('name'))}" for i, d in enumerate(docs, 1)) or "Abhi koi VIP nahi."
    await reply(m, card("VIP Members", "⭐", body, "script", m.chat.id), close_kb(m.from_user.id))


# =====================================================================
# SETTINGS PANEL
# =====================================================================
def settings_view(cid: int, title: str, s: dict, uid: int):
    onoff = lambda v: "ON ✅" if v else "OFF ❌"
    body = kv([("💬", "Group", esc(title or "")), ("🛡", "Protection", onoff(s["protect"])),
               ("⚠️", "Warn limit", s["warn_limit"]), ("⚡", "Limit action", s["action"].upper()),
               ("🧹", "Clean service msgs", onoff(s["cleanservice"])), ("👋", "Welcome", onoff(s["welcome_on"])),
               ("🔤", "Font", "Normal" if s["font"] == "normal" else "Fancy")], "sans", cid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn(f"🛡 Protection: {onoff(s['protect'])}", f"st:{cid}:protect")],
        [btn(f"⚠️ Warn limit: {s['warn_limit']}", f"st:{cid}:warn_limit"),
         btn(f"⚡ Action: {s['action'].upper()}", f"st:{cid}:action")],
        [btn(f"🧹 Clean service: {onoff(s['cleanservice'])}", f"st:{cid}:cleanservice")],
        [btn(f"👋 Welcome: {onoff(s['welcome_on'])}", f"st:{cid}:welcome_on")],
        [btn(f"🔤 Font: {'Normal' if s['font'] == 'normal' else 'Fancy'}", f"st:{cid}:font")],
        [btn("🔄 Refresh", f"st:{cid}:refresh"), btn("✖ Close", f"close:{uid}")]])
    return card("Settings", "⚙️", body, "sans", cid), kb


@grp.message(Command("settings"))
async def c_settings(m: Message):
    if not await need(m):
        return
    s = await get_settings(m.chat.id)
    text, kb = settings_view(m.chat.id, m.chat.title, s, m.from_user.id)
    await reply(m, text, kb)


@common.callback_query(F.data.startswith("st:"))
async def cb_settings(cb: CallbackQuery):
    _, cid, key = cb.data.split(":", 2)
    cid = int(cid)
    if not await actor_is_admin(cid, cb.from_user.id):
        return await cb.answer("Sirf admins ke liye.", show_alert=True)
    s = await get_settings(cid)
    try:
        if key == "protect":
            await set_setting(cid, protect=not s["protect"])
        elif key == "warn_limit":
            await set_setting(cid, warn_limit=s["warn_limit"] + 1 if s["warn_limit"] < 5 else 2)
        elif key == "action":
            await set_setting(cid, action="ban" if s["action"] == "mute" else "mute")
        elif key in ("cleanservice", "welcome_on"):
            await set_setting(cid, **{key: not s[key]})
        elif key == "font":
            await set_setting(cid, font="fancy" if s["font"] == "normal" else "normal")
    except Exception as e:
        return await cb.answer("Database error, thodi der baad try karo.", show_alert=True)
    s = await get_settings(cid)
    text, kb = settings_view(cid, cb.message.chat.title, s, cb.from_user.id)
    await edit(cb, text, kb)
    await cb.answer("Saved ✅")


# =====================================================================
# JOIN / LEAVE HANDLING (welcome, anti-bot, raid, service cleanup)
# =====================================================================
@grp.message(F.new_chat_members)
async def on_new_members(m: Message):
    cid = m.chat.id
    s = await get_settings(cid)
    adder = m.from_user
    for u in m.new_chat_members:
        if u.id == BOT_ID:
            continue
        if u.is_bot:
            if s["protect"] and adder and adder.id != u.id and not await exempt(cid, adder.id, True):
                try:
                    await bot.ban_chat_member(cid, u.id)
                    await bot.unban_chat_member(cid, u.id, only_if_banned=True)
                    await send(cid, card("Bot Removed", "🤖", f"{mention(adder.id, adder.full_name)} ne bot add kiya tha.\n"
                                         "Sirf admins bot add kar sakte hain.", "serif", cid), ttl=20)
                except TelegramAPIError:
                    pass
            continue
        try:
            await db.members.update_one({"chat_id": cid, "user_id": u.id},
                                        {"$set": {"name": u.full_name, "username": (u.username or "").lower() or None,
                                                  "active": True, "last_seen": now()},
                                         "$setOnInsert": {"first_seen": now()}}, upsert=True)
        except Exception:
            pass
        if s["protect"]:
            if raid_tick(cid):
                await log_event("RAID DETECTED", kv([("💬", "Group", esc(m.chat.title or "")),
                                                    ("ℹ️", "Info", "8+ joins in 30s - new members restricted for 5 min")], "sans"), cid)
                await send(cid, card("Raid Alert", "🚨", "Bahut saare naye members ek saath aaye.\nNaye members 10 min ke liye restrict kiye ja rahe hain.",
                                     "serif", cid), ttl=60)
            if raid_active(cid) and not is_sudo(u.id):
                try:
                    await do_mute(cid, u.id, now() + timedelta(minutes=10))
                except TelegramAPIError:
                    pass
                continue
        if not s["welcome_on"]:
            continue
        via_request = time.monotonic() - APPROVED.pop((cid, u.id), -1e9) < 180
        start_btn = None
        if not via_request:  # direct join: DM welcome, fall back to a Start button
            try:
                await bot.send_message(u.id, welcome_card(mention(u.id, u.first_name), m.chat.title or "Group"))
            except (TelegramForbiddenError, TelegramBadRequest):
                start_btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                    text="🚀 Start Bot", url=f"https://t.me/{BOT_USERNAME}?start=welcome_{cid}")]])
            except TelegramAPIError:
                pass
        if s["welcome"]:
            text = card("Welcome", "👑", fill(s["welcome"], u.id, u.full_name, m.chat.title or ""), "sans", cid)
        else:
            text = welcome_card(mention(u.id, u.first_name), m.chat.title or "Group", cid)
        await send(cid, text, start_btn, ttl=120, thread=_thread(m))
    if s["cleanservice"]:
        await safe_delete(m)


@grp.message(F.left_chat_member)
async def on_left(m: Message):
    u = m.left_chat_member
    try:
        await db.members.update_one({"chat_id": m.chat.id, "user_id": u.id}, {"$set": {"active": False}})
    except Exception:
        pass
    if (await get_settings(m.chat.id))["cleanservice"]:
        await safe_delete(m)


# =====================================================================
# AUTO PROTECTION (gaali / links / spam) - runs on new + edited messages
# =====================================================================
CONTENT_OK = {"text", "photo", "video", "document", "sticker", "animation", "voice", "audio",
              "video_note", "poll", "contact", "location"}


@grp.message()
@grp.edited_message()
async def protect(m: Message):
    u = m.from_user
    if not u or u.is_bot or m.sender_chat:
        return
    if str(getattr(m.content_type, "value", m.content_type)) not in CONTENT_OK:
        return
    cid = m.chat.id
    s = await get_settings(cid)
    if not s["protect"] or is_sudo(u.id):
        return
    text = m.text or m.caption or ""
    thread = _thread(m)
    if text:
        if has_bad(text, await get_custom_bad(cid)):
            if await exempt(cid, u.id, admins_ok=False):
                return
            await safe_delete(m)
            try:
                await apply_warn(cid, u.id, u.full_name, "Gaali / abusive language", None, thread)
                await log_event("FILTER", "abusive language deleted", cid, u.id, channel=False)
            except Exception as e:
                log.warning("warn failed: %s", str(e)[:100])
            return
        if has_link(m, text):
            if await exempt(cid, u.id, admins_ok=True):
                return
            await safe_delete(m)
            await send(cid, card("Link Removed", "🔗", f"{mention(u.id, u.full_name)}, bina permission link allowed nahi hai.",
                                 "serif", cid), ttl=8, thread=thread)
            return
    if m.edit_date:
        return
    reason = spam_reason(m, text)
    if reason:
        if await exempt(cid, u.id, admins_ok=True):
            return
        await safe_delete(m)
        try:
            await do_mute(cid, u.id, now() + timedelta(minutes=5))
            rows = [("👤", "User", mention(u.id, u.full_name)), ("📝", "Reason", reason), ("⏱", "Mute", "5 min")]
            await send(cid, card("Spam Muted", "🔇", kv(rows, "serif", cid), "serif", cid), ttl=20, thread=thread)
            await log_event("AUTO SPAM MUTE", kv(rows, "sans"), cid, u.id, channel=False)
        except TelegramAPIError:
            pass


# =====================================================================
# STARTUP
# =====================================================================
dp.include_router(common)
dp.include_router(owner)
dp.include_router(grp)
dp.message.outer_middleware(TrackMW())


@dp.errors()
async def on_error(event: ErrorEvent):
    log.error("Handler error: %s", repr(event.exception)[:300])
    return True


async def main():
    global BOT_ID, BOT_USERNAME
    try:
        await db.command("ping")
    except Exception as e:
        log.error("MongoDB connect nahi hua: %s", str(e)[:150])
        sys.exit(1)
    try:
        await db.members.create_index([("chat_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
        await db.members.create_index("username")
        await db.warns.create_index("expires_at", expireAfterSeconds=0)
        await db.warns.create_index([("chat_id", ASCENDING), ("user_id", ASCENDING)])
        await db.vips.create_index([("chat_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
        await db.verif.create_index("nonce", unique=True)
        await db.verif.create_index("created", expireAfterSeconds=3600)
        await db.events.create_index("ts", expireAfterSeconds=60 * 60 * 24 * 30)
        await db.badwords.create_index([("chat_id", ASCENDING), ("word", ASCENDING)], unique=True)
        SUDO_DB.update([d["_id"] async for d in db.sudos.find({})])
        NORMAL_FONT.update([d["_id"] async for d in db.groups.find({"font": "normal"})])
    except Exception as e:
        log.warning("startup db setup issue: %s", str(e)[:150])
    me = await bot.get_me()
    BOT_ID, BOT_USERNAME = me.id, me.username or BOT_USERNAME
    log.info("Starting @%s (id %s)", BOT_USERNAME, BOT_ID)
    await log_event("ONLINE", kv([("🤖", "Bot", f"@{BOT_USERNAME}"), ("🕒", "Time", fmt_dt(now()))], "sans"))
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        mongo.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
