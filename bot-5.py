#!/usr/bin/env python3
"""GUARDIX - premium Telegram group security & management bot (single file)."""
import asyncio
import html
import logging
import math
import os
import re
import random
import secrets
import sys
import time
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psutil
from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import (TelegramAPIError, TelegramBadRequest,
                                TelegramForbiddenError, TelegramRetryAfter)
from aiogram.filters import Filter
from aiogram.types import (BotCommand, CallbackQuery, ChatJoinRequest,
                           ChatMemberUpdated, ChatPermissions,
                           InlineKeyboardButton as Btn,
                           InlineKeyboardMarkup as Kb, Message)
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import PyMongoError

# ════════════════════════════ CONFIG ════════════════════════════
def env(k, d=""):
    return (os.environ.get(k) or d).strip()


def env_int(k, d=0):
    try:
        return int(env(k, str(d)))
    except ValueError:
        return d


BOT_TOKEN = env("BOT_TOKEN")
BOT_USERNAME = env("BOT_USERNAME").lstrip("@")
OWNER_ID = env_int("OWNER_ID")
SUDO = {int(x) for x in re.split(r"[,\s]+", env("SUDO_IDS")) if x.lstrip("-").isdigit()}
LOG_GROUP_ID = env_int("LOG_GROUP_ID")
SUPPORT_URL = env("SUPPORT_URL")
MONGO_URI = env("MONGO_DB_URI")
DB_NAME = env("DATABASE_NAME", "guardix")
try:
    TZ = ZoneInfo(env("TIMEZONE", "Asia/Kolkata"))
except Exception:
    TZ = timezone.utc

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
log = logging.getLogger("guardix")

if not BOT_TOKEN:
    sys.exit("BOT_TOKEN env var is missing")
if not MONGO_URI:
    sys.exit("MONGO_DB_URI env var is missing")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(
    parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
dp = Dispatcher()
mongo = AsyncIOMotorClient(MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=5000)
db = mongo[DB_NAME]

START_TS = time.time()
BOT_ID = 0
ANON_ID = 1087968824
GBANS: set = set()
TASKS: set = set()

# ════════════════════════════ UTILS ════════════════════════════
def utcnow():
    return datetime.now(timezone.utc)


def local(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


def fmt_dt(dt):
    return local(dt).strftime("%d %b %Y, %I:%M %p")


def esc(s):
    return html.escape(str(s), quote=False)


def short(s, n=40):
    s = str(s)
    return s if len(s) <= n else s[:n - 1] + "…"


def mention(uid, name):
    return f'<a href="tg://user?id={uid}">{esc(short(name or "User"))}</a>'


def pbar(cur, total, w=10):
    total = max(total, 1)
    f = max(0, min(w, round(w * cur / total)))
    return "▰" * f + "▱" * (w - f)


def fmt_td(sec):
    sec = int(sec)
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    mi, s = divmod(r, 60)
    return " ".join(x for x in (f"{d}d" if d else "", f"{h}h" if h else "",
                                f"{mi}m" if mi else "", f"{s}s" if not (d or h or mi) else "") if x)


class LRU:
    def __init__(self, n):
        self.n, self.d = n, OrderedDict()

    def get(self, k, default=None):
        if k in self.d:
            self.d.move_to_end(k)
            return self.d[k]
        return default

    def set(self, k, v):
        self.d[k] = v
        self.d.move_to_end(k)
        while len(self.d) > self.n:
            self.d.popitem(last=False)

    def pop(self, k):
        return self.d.pop(k, None)

    def __contains__(self, k):
        return k in self.d


async def sdb(aw, default=None):
    """Run a DB awaitable; on outage return default instead of crashing."""
    try:
        return await aw
    except PyMongoError as e:
        log.warning("DB error: %s", e)
        return default


def spawn(coro):
    t = asyncio.create_task(coro)
    TASKS.add(t)
    t.add_done_callback(TASKS.discard)
    return t


# ════════════════════════════ FONTS / THEMES / CARDS ════════════════════════════
def _fm(up, lo, dg=None):
    t = {}
    for i in range(26):
        t[65 + i] = chr(up + i)
        t[97 + i] = chr(lo + i)
    if dg:
        for i in range(10):
            t[48 + i] = chr(dg + i)
    return t


_SC = "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"
_small = {}
for _i in range(26):
    _small[65 + _i] = _SC[_i]
    _small[97 + _i] = _SC[_i]
FONT_MAPS = {
    "sans": _fm(0x1D5D4, 0x1D5EE, 0x1D7EC),
    "serif": _fm(0x1D400, 0x1D41A, 0x1D7CE),
    "mono": _fm(0x1D670, 0x1D68A, 0x1D7F6),
    "script": _fm(0x1D4D0, 0x1D4EA),
    "small": _small,
}
FONT_NAMES = ["sans", "serif", "mono", "script", "small", "plain", "auto"]
THEMES = {"gold": "✦━━━━━━━━━━━━━━━✦", "neon": "◈═══════════════◈", "dark": "▪▬▬▬▬▬▬▬▬▬▬▬▬▬▪"}

DEFAULTS = dict(font="auto", theme="gold", protect=True, warn_limit=3, action="mute",
                welcome_on=True, welcome=None, rules=None, cleanservice=True,
                sticker_on=True, sticker_captcha=True, reports_on=True,
                link_approval=True, locked=False)


def pick_font(font):
    if font == "auto":
        return random.choice(list(FONT_MAPS) + ["plain"])
    return font


def fancy(text, font):
    m = FONT_MAPS.get(font)
    return text.translate(m) if m else text


def card(emoji, title, rows=(), gs=None, extra=None):
    gs = gs or DEFAULTS
    font = pick_font(gs.get("font", "auto"))
    bar = THEMES.get(gs.get("theme"), THEMES["gold"])

    def F_(s):
        return fancy(s, font)
    out = [bar, f"   {emoji} {F_(title)}", bar, ""]
    for r in rows:
        if isinstance(r, str):
            out.append(r)
        else:
            e, label, val = r
            out.append(f"┃ {e} {F_(label)}: {val}")
    if extra:
        out += ["", extra]
    out += ["", f"{bar[0]} {F_('GUARDIX')}"]
    return "\n".join(out)


def kb(*rows):
    return Kb(inline_keyboard=[list(r) for r in rows if r])


def b(text, data=None, url=None):
    return Btn(text=text, callback_data=data, url=url)


def bot_url(payload=""):
    return f"https://t.me/{BOT_USERNAME}" + (f"?start={payload}" if payload else "")


def add_url():
    return (f"https://t.me/{BOT_USERNAME}?startgroup=true&admin=delete_messages"
            "+restrict_members+pin_messages+invite_users+change_info+promote_members")


def support_url():
    return SUPPORT_URL if SUPPORT_URL.startswith("http") else bot_url()


def main_kb():
    return kb([b("📖 Help", "hp:main"), b("➕ Add to Group", url=add_url())],
              [b("💬 Support", url=support_url()), b("🎛 Control Panel", "pn:home")])


# ════════════════════════════ SETTINGS CACHE ════════════════════════════
GS_CACHE = LRU(2000)


async def get_gs(chat_id):
    hit = GS_CACHE.get(chat_id)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        doc = await db.groups.find_one({"chat_id": chat_id})
    except PyMongoError as e:
        log.warning("DB error (settings): %s", e)
        return hit[1] if hit else dict(DEFAULTS)
    gs = {**DEFAULTS, **(doc or {})}
    GS_CACHE.set(chat_id, (time.time(), gs))
    return gs


async def set_gs(chat_id, **kw):
    await sdb(db.groups.update_one({"chat_id": chat_id}, {"$set": kw}, upsert=True))
    GS_CACHE.pop(chat_id)


# ════════════════════════════ TELEGRAM HELPERS ════════════════════════════
def is_group(m):
    return m.chat.type in ("group", "supergroup")


def is_private(m):
    return m.chat.type == "private"


async def send(chat_id, text, **kw):
    for _ in range(2):
        try:
            return await bot.send_message(chat_id, text, **kw)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
        except TelegramAPIError as e:
            log.debug("send failed %s: %s", chat_id, e)
            return None
    return None


async def try_delete(chat_id, msg_id):
    try:
        await bot.delete_message(chat_id, msg_id)
        return True
    except TelegramAPIError:
        return False


async def _del_later(chat_id, msg_id, secs):
    await asyncio.sleep(secs)
    await try_delete(chat_id, msg_id)


async def schedule_delete(chat_id, msg_id, secs):
    if not msg_id:
        return
    if secs <= 60:
        spawn(_del_later(chat_id, msg_id, secs))
        return
    try:
        now = utcnow()
        await db.events.insert_one({"type": "sched_del", "chat_id": chat_id, "msg_id": msg_id,
                                    "due": now + timedelta(seconds=secs), "created": now})
    except PyMongoError:
        spawn(_del_later(chat_id, msg_id, secs))


async def say(m, emoji, title, rows=(), kb_=None, gs=None, delete_after=None, extra=None):
    if gs is None:
        gs = await get_gs(m.chat.id) if is_group(m) else DEFAULTS
    kw = {}
    if getattr(m, "is_topic_message", None) and m.message_thread_id:
        kw["message_thread_id"] = m.message_thread_id
    msg = await send(m.chat.id, card(emoji, title, rows, gs, extra), reply_markup=kb_, **kw)
    if msg and delete_after:
        await schedule_delete(m.chat.id, msg.message_id, delete_after)
    return msg


async def edit(msg, text, kb_=None):
    try:
        await msg.edit_text(text, reply_markup=kb_)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            log.debug("edit failed: %s", e)
    except TelegramAPIError as e:
        log.debug("edit failed: %s", e)


async def profile_photo(uid):
    try:
        p = await bot.get_user_profile_photos(uid, limit=1)
        if p.total_count:
            return p.photos[0][-1].file_id
    except TelegramAPIError:
        pass
    return None


async def send_card(chat_id, photo, text, kb_=None):
    if photo:
        try:
            return await bot.send_photo(chat_id, photo, caption=text, reply_markup=kb_)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
        except TelegramAPIError:
            pass
    return await send(chat_id, text, reply_markup=kb_)


async def fail(m, e, gs=None):
    msg = getattr(e, "message", None) or str(e)
    low = msg.lower()
    if "rights" in low or "administrator" in low or "not enough" in low:
        why = "Bot ko admin + required rights chahiye."
    elif "not found" in low or "participant" in low:
        why = "User group me nahi mila."
    else:
        why = esc(short(msg, 120))
    await say(m, "❌", "FAILED", [("⚠️", "Reason", why)], gs=gs, delete_after=15)


async def log_ev(typ, text, chat_id=None):
    await sdb(db.events.insert_one({"type": typ, "text": short(text, 300), "chat_id": chat_id,
                                    "created": utcnow()}))


async def alert(text):
    for cid in {LOG_GROUP_ID, OWNER_ID} - {0}:
        await send(cid, text)


# ════════════════════════════ PERMISSIONS ════════════════════════════
MEM_CACHE = LRU(5000)
VIP_CACHE = LRU(500)


def is_sudo(uid):
    return uid == OWNER_ID or uid in SUDO


async def get_member(chat_id, uid, fresh=False):
    k = (chat_id, uid)
    hit = MEM_CACHE.get(k)
    if hit and not fresh and time.time() - hit[0] < 30:
        return hit[1]
    try:
        mem = await bot.get_chat_member(chat_id, uid)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 0.5)
        return hit[1] if hit else None
    except TelegramAPIError:
        return None
    MEM_CACHE.set(k, (time.time(), mem))
    return mem


async def is_admin(chat_id, uid):
    if is_sudo(uid) or uid == ANON_ID:
        return True
    mem = await get_member(chat_id, uid)
    return bool(mem and mem.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR))


async def has_right(chat_id, uid, right):
    if is_sudo(uid) or uid == ANON_ID:
        return True
    mem = await get_member(chat_id, uid)
    if not mem:
        return False
    if mem.status == ChatMemberStatus.CREATOR:
        return True
    return mem.status == ChatMemberStatus.ADMINISTRATOR and bool(getattr(mem, right, False))


async def is_vip(chat_id, uid):
    hit = VIP_CACHE.get(chat_id)
    if not hit or time.time() - hit[0] > 60:
        docs = await sdb(db.vips.find({"chat_id": chat_id}, {"user_id": 1}).to_list(1000), None)
        if docs is None:
            return uid in (hit[1] if hit else set())
        hit = (time.time(), {d["user_id"] for d in docs})
        VIP_CACHE.set(chat_id, hit)
    return uid in hit[1]


async def exempt(chat_id, uid):
    return is_sudo(uid) or await is_admin(chat_id, uid) or await is_vip(chat_id, uid)


async def guard(chat_id, actor_id, tid):
    """Return error text if actor may not act on target."""
    if tid == BOT_ID:
        return "Bot khud par action nahi kar sakta."
    if is_sudo(tid) and not (actor_id == OWNER_ID and tid != OWNER_ID):
        return "Owner/Sudo protected hain."
    mem = await get_member(chat_id, tid)
    if mem and mem.status == ChatMemberStatus.CREATOR:
        return "Group owner par action nahi ho sakta."
    if mem and mem.status == ChatMemberStatus.ADMINISTRATOR and not is_sudo(actor_id):
        return "Admin par admin action allowed nahi (sirf Owner/Sudo)."
    return None


# ════════════════════════════ COMMAND FILTER (/ . !) ════════════════════════════
CMD_RE = re.compile(r"^[/.!]([A-Za-z0-9_]+)(?:@([A-Za-z0-9_]+))?(?:\s+(.*))?$", re.S)


class Cmd(Filter):
    def __init__(self, *names):
        self.names = set(names)

    async def __call__(self, message: Message):
        t = message.text
        if not t or t[0] not in "/.!":
            return False
        mm = CMD_RE.match(t)
        if not mm:
            return False
        name, at, rest = mm.group(1).lower(), mm.group(2), (mm.group(3) or "").strip()
        if name not in self.names:
            return False
        if at and at.lower() != BOT_USERNAME.lower():
            return False
        return {"args": rest, "cmd": name}


# ════════════════════════════ TARGET RESOLUTION ════════════════════════════
class T:
    __slots__ = ("id", "name")

    def __init__(self, uid, name):
        self.id, self.name = uid, name or "User"


def mt(t):
    return mention(t.id, t.name)


def actor_of(m):
    u = m.from_user
    return mention(u.id, u.full_name) if u.id != ANON_ID else "Admin"


async def resolve_target(m, args):
    r = m.reply_to_message
    if r and r.from_user and not r.forum_topic_created:
        return T(r.from_user.id, r.from_user.full_name), args
    parts = args.split(None, 1)
    rest = parts[1] if len(parts) > 1 else ""
    for e in (m.entities or []):
        if e.type == "text_mention" and e.user:
            return T(e.user.id, e.user.full_name), rest
    if not parts:
        return None, args
    tok = parts[0]
    if re.fullmatch(r"-?\d{5,15}", tok):
        uid = int(tok)
        name = "User"
        d = await sdb(db.members.find_one({"user_id": uid}, {"name": 1}))
        if d:
            name = d.get("name", name)
        return T(uid, name), rest
    if tok.startswith("@") and len(tok) > 2:
        d = await sdb(db.members.find_one({"un": tok[1:].lower()}))
        if d:
            return T(d["user_id"], d.get("name")), rest
    return None, args


async def gate(m, right, need_target=True, args="", skip_guard=False):
    """Common moderation gate -> (target, rest, gs) or None (already replied)."""
    if not is_group(m):
        await say(m, "⚠️", "GROUP ONLY", [("💬", "Info", "Ye command sirf group me chalta hai.")])
        return None
    uid = m.from_user.id
    gs = await get_gs(m.chat.id)
    if right and not await has_right(m.chat.id, uid, right):
        await say(m, "🚫", "NO PERMISSION", [("🔐", "Need", "Admin right: " + right.replace("can_", "").replace("_", " "))],
                  gs=gs, delete_after=10)
        return None
    if not need_target:
        return None, args, gs
    t, rest = await resolve_target(m, args)
    if not t:
        await say(m, "⚠️", "TARGET NEEDED", [("💡", "Use", "Reply to user, ya ID / @username do.")],
                  gs=gs, delete_after=12)
        return None
    if not skip_guard:
        err = await guard(m.chat.id, uid, t.id)
        if err:
            await say(m, "🛡", "BLOCKED", [("⚠️", "Reason", err)], gs=gs, delete_after=12)
            return None
    return t, rest, gs


DUR_RE = re.compile(r"^(\d+)([smhdw])$", re.I)
UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def split_time(rest):
    parts = rest.split(None, 1)
    if parts:
        mm = DUR_RE.match(parts[0])
        if mm:
            secs = int(mm[1]) * UNIT[mm[2].lower()]
            return (utcnow() + timedelta(seconds=secs), parts[1] if len(parts) > 1 else "",
                    parts[0].lower(), secs)
    return None, rest, "Permanent", 0


FULL = ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True,
                       can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                       can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                       can_add_web_page_previews=True, can_invite_users=True)
NOPERM = ChatPermissions(can_send_messages=False, can_send_audios=False, can_send_documents=False,
                         can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
                         can_send_voice_notes=False, can_send_polls=False,
                         can_send_other_messages=False, can_add_web_page_previews=False,
                         can_invite_users=False)


async def mute_user(chat_id, uid, secs):
    until = utcnow() + timedelta(seconds=secs) if secs else None
    try:
        await bot.restrict_chat_member(chat_id, uid, NOPERM, until_date=until)
    except TelegramAPIError as e:
        log.info("mute failed %s/%s: %s", chat_id, uid, e)
        return False
    await sdb(db.mutes.update_one({"chat_id": chat_id, "user_id": uid},
                                  {"$set": {"until": until, "ts": utcnow()}}, upsert=True))
    return True


async def unmute_user(chat_id, uid):
    try:
        ch = await bot.get_chat(chat_id)
        perms = ch.permissions or FULL
    except TelegramAPIError:
        perms = FULL
    await bot.restrict_chat_member(chat_id, uid, perms)
    await sdb(db.mutes.delete_one({"chat_id": chat_id, "user_id": uid}))


async def kick_user(chat_id, uid):
    await bot.ban_chat_member(chat_id, uid)
    await bot.unban_chat_member(chat_id, uid, only_if_banned=True)


# ════════════════════════════ WARNS ════════════════════════════
WARN_MEM = LRU(2000)


async def add_warn(chat_id, uid, reason, by):
    try:
        await db.warns.insert_one({"chat_id": chat_id, "user_id": uid, "reason": short(reason, 100),
                                   "by": by, "created": utcnow()})
        return await db.warns.count_documents({"chat_id": chat_id, "user_id": uid})
    except PyMongoError as e:
        log.warning("DB error (warn): %s", e)
        n = (WARN_MEM.get((chat_id, uid)) or 0) + 1
        WARN_MEM.set((chat_id, uid), n)
        return n


async def apply_limit(chat_id, uid, count, gs):
    """If warn limit reached run configured action; returns action label or None."""
    if count < gs["warn_limit"]:
        return None
    act = gs["action"]
    try:
        if act == "ban":
            await bot.ban_chat_member(chat_id, uid)
        elif act == "kick":
            await kick_user(chat_id, uid)
        else:
            await mute_user(chat_id, uid, 86400)
    except TelegramAPIError as e:
        log.info("limit action failed: %s", e)
        return None
    await sdb(db.warns.delete_many({"chat_id": chat_id, "user_id": uid}))
    WARN_MEM.pop((chat_id, uid))
    return act.upper()


# ════════════════════════════ TRACKING (messages / members / users) ════════════════════════════
MSG_BUF: list = []
MEMBER_NAMES = LRU(20000)
USER_SEEN = LRU(20000)
GROUP_SEEN = LRU(5000)


async def touch_user(u):
    key = u.id
    name = short(u.full_name, 64)
    if USER_SEEN.get(key) == name:
        return
    USER_SEEN.set(key, name)
    await sdb(db.users.update_one({"user_id": u.id}, {"$set": {
        "name": name, "username": (u.username or "").lower(), "blocked": False, "ts": utcnow()},
        "$setOnInsert": {"joined": utcnow()}}, upsert=True))


async def touch_member(chat, u):
    name, un, key = short(u.full_name, 64), (u.username or "").lower(), (chat.id, u.id)
    if chat.id not in GROUP_SEEN:
        GROUP_SEEN.set(chat.id, 1)
        await sdb(db.groups.update_one({"chat_id": chat.id}, {"$set": {
            "title": short(chat.title or "", 80), "active": True}}, upsert=True))
    if MEMBER_NAMES.get(key) == (name, un):
        return
    MEMBER_NAMES.set(key, (name, un))
    now = utcnow()
    try:
        before = await db.members.find_one_and_update(
            {"chat_id": chat.id, "user_id": u.id},
            {"$set": {"name": name, "un": un, "last": now}, "$setOnInsert": {"joined": now}},
            upsert=True, return_document=ReturnDocument.BEFORE)
        if before is None or before.get("name") != name:
            await db.members.update_one({"chat_id": chat.id, "user_id": u.id}, {"$push": {
                "names": {"$each": [{"n": name, "t": now}], "$slice": -5}}})
    except PyMongoError as e:
        log.warning("DB error (member): %s", e)


def has_content(m):
    return bool(m.text or m.caption or m.sticker or m.photo or m.video or m.document or m.voice
                or m.audio or m.animation or m.video_note or m.poll)


async def track(m):
    u = m.from_user
    if not u or u.is_bot:
        return
    if m.chat.type == "private":
        await touch_user(u)
        return
    if not is_group(m) or m.sender_chat:
        return
    await touch_member(m.chat, u)
    if has_content(m) and len(MSG_BUF) < 20000:
        MSG_BUF.append({"chat_id": m.chat.id, "user_id": u.id, "name": short(u.full_name, 40),
                        "ts": utcnow()})


class Track(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            await track(event)
        except Exception:
            log.exception("track failed")
        return await handler(event, data)


# ════════════════════════════ HELP DATA ════════════════════════════
HELP = {
    "pub": ("🌐", "PUBLIC", ["/start – VIP lounge", "/help – help menu", "/id – IDs", "/me – your profile"]),
    "mem": ("👥", "MEMBERS", ["/info – VIP profile", "/rules – group rules", "/admins – admin list",
                              "/viplist – VIP members", "/report [reason] or @admin", "/kickme – leave group",
                              "/top – today's top 3", "/stats – today's summary"]),
    "mod": ("🔨", "MODERATION", ["/ban [time] [reason]", "/unban", "/kick", "/mute [time] [reason]", "/unmute",
                                 "/warn [reason]", "/warns", "/resetwarns", "/del", "/purge (max 200)",
                                 "/pin [quiet]", "/unpin", "/lock", "/unlock", "/promote 1|2|3", "/demote",
                                 "Time: 10m 2h 1d 1w"]),
    "prot": ("🛡", "PROTECTION", ["/protect on|off", "/addbadword w1 w2", "/delbadword w"]),
    "setup": ("⚙️", "SETUP", ["/setwelcome <text> ({name} {group})", "/setrules <text>", "/settings – panel",
                              "/announce [text]", "/addvip · /delvip · /viplist", "/zombies [1-4] (group owner)"]),
    "own": ("👑", "OWNER / SUDO", ["/broadcast · /gcast · /dmpromo (DM)", "/callpm [count] [text]",
                                   "/logs · /sudolist · /stats · /uptime", "/addsudo · /delsudo (owner)",
                                   "/gban · /ungban · /leavechat (owner)", "/addevent · /events · /event id",
                                   "/delevent id · /clearevents (owner)"]),
}


def help_kb(uid):
    rows = [[b("🌐 Public", "hp:pub"), b("👥 Members", "hp:mem")],
            [b("🔨 Moderation", "hp:mod"), b("🛡 Protection", "hp:prot")],
            [b("⚙️ Setup", "hp:setup")]]
    if is_sudo(uid):
        rows[-1].append(b("👑 Owner", "hp:own"))
    return Kb(inline_keyboard=rows)


# ════════════════════════════ PUBLIC ════════════════════════════
@dp.message(Cmd("start"))
async def c_start(m: Message, args: str):
    if is_group(m):
        await say(m, "👑", "VIP LOUNGE", [("💬", "Info", "Full experience ke liye DM me start karo.")],
                  kb(([b("🚀 Open in DM", url=bot_url("group"))])), delete_after=60)
        return
    if not is_private(m):
        return
    if args.startswith("vf_"):
        doc = await sdb(db.verif.find_one({"_id": args[3:], "user_id": m.from_user.id}))
        if not doc:
            await say(m, "⌛", "EXPIRED", [("💡", "Info", "Ye verification expire ho gaya. Group me dobara try karo.")])
            return
        msg = await send_captcha(m.from_user.id, doc)
        if msg:
            await schedule_delete(m.chat.id, msg.message_id, 600)
        return
    u = m.from_user
    rows = [("👤", "User", mention(u.id, u.full_name)), ("🆔", "ID", f"<code>{u.id}</code>"),
            ("🛡", "Status", "Verified ✅" if args == "verify" else "VIP Lounge Active"),
            "", "┃ ✨ Silent security for your groups",
            "┃ 🚫 Gaali · Spam · Raid · Bots – sab free"]
    photo = await profile_photo(u.id)
    await send_card(m.chat.id, photo, card("👑", "VIP LOUNGE", rows), main_kb())


@dp.message(Cmd("help"))
async def c_help(m: Message):
    await say(m, "📖", "HELP MENU", [("💡", "Tip", "Prefix: <code>/</code> <code>.</code> <code>!</code> sab chalte hain"),
                                    "", "┃ Category chuno 👇"], help_kb(m.from_user.id))


@dp.callback_query(F.data.startswith("hp:"))
async def cb_help(cb: CallbackQuery):
    cat = cb.data[3:]
    if cat == "main":
        await edit(cb.message, card("📖", "HELP MENU", ["┃ Category chuno 👇"]), help_kb(cb.from_user.id))
    elif cat in HELP:
        if cat == "own" and not is_sudo(cb.from_user.id):
            return await cb.answer("Not allowed", show_alert=True)
        e, t, lines = HELP[cat]
        await edit(cb.message, card(e, t, [f"┃ {esc(x)}" for x in lines]), kb([b("⬅️ Back", "hp:main")]))
    await cb.answer()


@dp.message(Cmd("id"))
async def c_id(m: Message):
    rows = [("👤", "Your ID", f"<code>{m.from_user.id}</code>"), ("💬", "Chat ID", f"<code>{m.chat.id}</code>")]
    r = m.reply_to_message
    if r and r.from_user:
        rows.append(("↩️", "Replied ID", f"<code>{r.from_user.id}</code>"))
    await say(m, "🆔", "ID INFO", rows)


@dp.message(Cmd("me"))
async def c_me(m: Message):
    u = m.from_user
    n = await sdb(db.members.count_documents({"user_id": u.id}), 0)
    rows = [("👤", "Name", mention(u.id, u.full_name)), ("🆔", "ID", f"<code>{u.id}</code>"),
            ("🔖", "Username", f"@{u.username}" if u.username else "—"), ("👥", "Groups", n)]
    photo = await profile_photo(u.id)
    gs = await get_gs(m.chat.id) if is_group(m) else DEFAULTS
    await send_card(m.chat.id, photo, card("🪪", "MY PROFILE", rows, gs))


# ════════════════════════════ GROUP MEMBER COMMANDS ════════════════════════════
@dp.message(Cmd("info"))
async def c_info(m: Message, args: str):
    if not is_group(m):
        return await say(m, "⚠️", "GROUP ONLY", [("💬", "Info", "Ye command group me use karo.")])
    gs = await get_gs(m.chat.id)
    t, _ = await resolve_target(m, args)
    if not t:
        t = T(m.from_user.id, m.from_user.full_name)
    if is_sudo(t.id):
        role = "👑 Bot Owner" if t.id == OWNER_ID else "⚡ Sudo"
    else:
        mem = await get_member(m.chat.id, t.id)
        if mem and mem.status == ChatMemberStatus.CREATOR:
            role = "👑 Group Owner"
        elif mem and mem.status == ChatMemberStatus.ADMINISTRATOR:
            role = "👮 Admin"
        elif await is_vip(m.chat.id, t.id):
            role = "💎 VIP"
        else:
            role = "👤 Member"
    since = utcnow() - timedelta(days=7)
    warns = await sdb(db.warns.count_documents({"chat_id": m.chat.id, "user_id": t.id,
                                                "created": {"$gte": since}}), 0)
    d = await sdb(db.members.find_one({"chat_id": m.chat.id, "user_id": t.id})) or {}
    rows = [("👤", "Name", mt(t)), ("🆔", "ID", f"<code>{t.id}</code>"), ("🎖", "Role", role),
            ("⚠️", "Warns", f"{warns}/{gs['warn_limit']}  {pbar(warns, gs['warn_limit'], 6)}")]
    if d.get("un"):
        rows.append(("🔖", "Username", f"@{esc(d['un'])}"))
    if d.get("joined"):
        rows.append(("📅", "First seen", fmt_dt(d["joined"])))
    names = d.get("names", [])[:-1][-3:]
    if names:
        rows.append(("📝", "Old names", esc(" → ".join(x["n"] for x in names))))
    photo = await profile_photo(t.id)
    await send_card(m.chat.id, photo, card("💎", "VIP PROFILE", rows, gs),
                    kb([b("📩 Verify in DM", url=bot_url("verify"))]))


@dp.message(Cmd("rules"))
async def c_rules(m: Message):
    if not is_group(m):
        return
    gs = await get_gs(m.chat.id)
    text = esc(gs["rules"]) if gs.get("rules") else "Abhi rules set nahi hain. Admin: /setrules"
    await say(m, "📜", "GROUP RULES", [text], gs=gs)


async def admins_page(chat_id, page, gs):
    adm = [a for a in await bot.get_chat_administrators(chat_id) if not a.user.is_bot]
    adm.sort(key=lambda a: a.status != ChatMemberStatus.CREATOR)
    per = 10
    pages = max(1, math.ceil(len(adm) / per))
    page = max(0, min(page, pages - 1))
    lines = []
    for a in adm[page * per:(page + 1) * per]:
        crown = "👑" if a.status == ChatMemberStatus.CREATOR else "👮"
        title = getattr(a, "custom_title", None)
        lines.append(f"┃ {crown} {mention(a.user.id, a.user.full_name)}" + (f" — {esc(title)}" if title else ""))
    nav = []
    if page > 0:
        nav.append(b("⬅️", f"ad:{chat_id}:{page - 1}"))
    nav.append(b(f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(b("➡️", f"ad:{chat_id}:{page + 1}"))
    return card("👮", f"ADMINS ({len(adm)})", lines or ["┃ —"], gs), kb(nav)


@dp.message(Cmd("admins"))
async def c_admins(m: Message):
    if not is_group(m):
        return
    gs = await get_gs(m.chat.id)
    try:
        text, k = await admins_page(m.chat.id, 0, gs)
    except TelegramAPIError as e:
        return await fail(m, e, gs)
    await send(m.chat.id, text, reply_markup=k)


@dp.callback_query(F.data.startswith("ad:"))
async def cb_admins(cb: CallbackQuery):
    _, cid, page = cb.data.split(":")
    try:
        text, k = await admins_page(int(cid), int(page), await get_gs(int(cid)))
        await edit(cb.message, text, k)
    except TelegramAPIError:
        pass
    await cb.answer()


@dp.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery):
    await cb.answer()


@dp.message(Cmd("viplist"))
async def c_viplist(m: Message):
    if not is_group(m):
        return
    docs = await sdb(db.vips.find({"chat_id": m.chat.id}).to_list(50), [])
    lines = [f"┃ 💎 {mention(d['user_id'], d.get('name'))}" for d in docs] or ["┃ Koi VIP nahi hai."]
    await say(m, "💎", f"VIP LIST ({len(docs)})", lines)


REPORT_CD = LRU(2000)


async def do_report(m: Message, reason: str):
    r = m.reply_to_message
    if not r or not r.from_user:
        return await say(m, "⚠️", "REPORT", [("💡", "Use", "Kisi message par reply karke report karo.")], delete_after=10)
    chat, me_ = m.chat.id, m.from_user
    if r.from_user.id == me_.id:
        return await say(m, "⚠️", "REPORT", [("💡", "Info", "Khud ko report nahi kar sakte.")], delete_after=10)
    if await is_admin(chat, r.from_user.id):
        return await say(m, "⚠️", "REPORT", [("💡", "Info", "Admins ko report nahi kar sakte.")], delete_after=10)
    if time.time() - (REPORT_CD.get((chat, me_.id)) or 0) < 30:
        return
    REPORT_CD.set((chat, me_.id), time.time())
    gs = await get_gs(chat)
    try:
        adm = [a for a in await bot.get_chat_administrators(chat) if not a.user.is_bot][:10]
    except TelegramAPIError:
        adm = []
    ping = "".join(f'<a href="tg://user?id={a.user.id}">\u200b</a>' for a in adm)
    link = ""
    if m.chat.username:
        link = f"https://t.me/{m.chat.username}/{r.message_id}"
    elif str(chat).startswith("-100"):
        link = f"https://t.me/c/{str(chat)[4:]}/{r.message_id}"
    rows = [("🙋", "Reporter", mention(me_.id, me_.full_name)), ("👤", "Reported", mention(r.from_user.id, r.from_user.full_name)),
            ("📝", "Reason", esc(short(reason or "No reason", 150)))]
    if link:
        rows.append(("🔗", "Message", f'<a href="{link}">Jump</a>'))
    k = kb([b("🗑 Delete", f"rp:d:{chat}:{r.message_id}:{r.from_user.id}"),
            b("🔨 Ban", f"rp:b:{chat}:{r.message_id}:{r.from_user.id}")],
           [b("✅ Resolve", f"rp:x:{chat}:{r.message_id}:{r.from_user.id}")])
    msg = await send(chat, card("🚨", "REPORT", rows, gs) + ping, reply_markup=k)
    if msg:
        await schedule_delete(chat, msg.message_id, 3600)
    await try_delete(chat, m.message_id)


@dp.message(Cmd("report"))
async def c_report(m: Message, args: str):
    if is_group(m):
        await do_report(m, args)


@dp.callback_query(F.data.startswith("rp:"))
async def cb_report(cb: CallbackQuery):
    _, act, cid, mid, uid = cb.data.split(":")
    cid, mid, uid = int(cid), int(mid), int(uid)
    if not await is_admin(cid, cb.from_user.id):
        return await cb.answer("Sirf admins.", show_alert=True)
    if act == "d":
        await try_delete(cid, mid)
        await cb.answer("Message deleted")
    elif act == "b":
        if not await has_right(cid, cb.from_user.id, "can_restrict_members") or await guard(cid, cb.from_user.id, uid):
            return await cb.answer("Not allowed.", show_alert=True)
        try:
            await bot.ban_chat_member(cid, uid)
            await try_delete(cid, mid)
            await cb.answer("User banned")
        except TelegramAPIError as e:
            return await cb.answer(short(str(e), 150), show_alert=True)
    else:
        await cb.answer("Resolved")
    await try_delete(cb.message.chat.id, cb.message.message_id)


@dp.message(Cmd("kickme"))
async def c_kickme(m: Message):
    if not is_group(m):
        return
    await say(m, "🚪", "LEAVE GROUP?", [("❓", "Confirm", "Kya tum sach me group chhodna chahte ho?")],
              kb([b("✅ Yes, kick me", f"km:y:{m.chat.id}:{m.from_user.id}"), b("❌ No", f"km:n:{m.chat.id}:{m.from_user.id}")]),
              delete_after=60)


@dp.callback_query(F.data.startswith("km:"))
async def cb_kickme(cb: CallbackQuery):
    _, ans, cid, uid = cb.data.split(":")
    if cb.from_user.id != int(uid):
        return await cb.answer("Ye tumhare liye nahi hai.", show_alert=True)
    if ans == "y":
        try:
            await kick_user(int(cid), int(uid))
            await edit(cb.message, card("🚪", "KICKED", [("👤", "User", mention(cb.from_user.id, cb.from_user.full_name))]))
        except TelegramAPIError:
            return await cb.answer("Admins ko kick nahi kar sakta.", show_alert=True)
    else:
        await try_delete(cb.message.chat.id, cb.message.message_id)
    await cb.answer()


# ════════════════════════════ STATS ════════════════════════════
MEDALS = ["🥇", "🥈", "🥉"]


async def top_users(chat_id, since, limit):
    pipe = [{"$match": {"chat_id": chat_id, "ts": {"$gte": since}}},
            {"$group": {"_id": "$user_id", "n": {"$sum": 1}, "name": {"$last": "$name"}}},
            {"$sort": {"n": -1}}, {"$limit": limit}]
    return await sdb(db.messages.aggregate(pipe).to_list(limit), [])


async def chat_totals(chat_id, since):
    total = await sdb(db.messages.count_documents({"chat_id": chat_id, "ts": {"$gte": since}}), 0)
    r = await sdb(db.messages.aggregate([{"$match": {"chat_id": chat_id, "ts": {"$gte": since}}},
                                         {"$group": {"_id": "$user_id"}}, {"$count": "n"}]).to_list(1), [])
    return total, (r[0]["n"] if r else 0)


def top_rows(docs):
    return [f"┃ {MEDALS[i] if i < 3 else '▫️'} {mention(d['_id'], d.get('name'))} — <b>{d['n']}</b>" for i, d in enumerate(docs)] or ["┃ Abhi data nahi hai."]


@dp.message(Cmd("top"))
async def c_top(m: Message):
    if not is_group(m):
        return
    docs = await top_users(m.chat.id, utcnow() - timedelta(hours=24), 3)
    await say(m, "🏆", "TOP 3 TODAY", top_rows(docs))


@dp.message(Cmd("stats"))
async def c_stats(m: Message):
    if is_group(m):
        since = utcnow() - timedelta(hours=24)
        total, active = await chat_totals(m.chat.id, since)
        docs = await top_users(m.chat.id, since, 3)
        rows = [("💬", "Messages", total), ("👥", "Active users", active), ""] + top_rows(docs)
        return await say(m, "📊", "TODAY'S SUMMARY", rows,
                         kb([b("🏆 Full Leaderboard", f"lb:{m.chat.id}:0")]))
    if is_private(m) and is_sudo(m.from_user.id):
        users = await sdb(db.users.count_documents({}), 0)
        groups = await sdb(db.groups.count_documents({"active": {"$ne": False}}), 0)
        mem = await sdb(db.members.count_documents({}), 0)
        ram = psutil.Process().memory_info().rss / 1048576
        await say(m, "📊", "BOT STATS", [("👤", "DM users", users), ("👥", "Groups", groups), ("🧑‍🤝‍🧑", "Tracked members", mem),
                                        ("🚫", "GBans", len(GBANS)), ("⚡", "Sudo", len(SUDO)),
                                        ("⏱", "Uptime", fmt_td(time.time() - START_TS)), ("🧠", "RAM", f"{ram:.0f} MB")])


@dp.callback_query(F.data.startswith("lb:"))
async def cb_lb(cb: CallbackQuery):
    _, cid, page = cb.data.split(":")
    cid, page = int(cid), int(page)
    docs = await top_users(cid, utcnow() - timedelta(hours=24), 50)
    pages = max(1, math.ceil(len(docs) / 10))
    page = max(0, min(page, pages - 1))
    lines = [f"┃ {MEDALS[i] if i < 3 else f'<b>{i + 1}.</b>'} {mention(d['_id'], d.get('name'))} — {d['n']}"
             for i, d in enumerate(docs) if page * 10 <= i < (page + 1) * 10] or ["┃ Abhi data nahi hai."]
    nav = []
    if page > 0:
        nav.append(b("⬅️", f"lb:{cid}:{page - 1}"))
    nav.append(b(f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(b("➡️", f"lb:{cid}:{page + 1}"))
    await edit(cb.message, card("🏆", "LEADERBOARD", lines, await get_gs(cid)), kb(nav))
    await cb.answer()


# ════════════════════════════ MODERATION ════════════════════════════
def undo_kb(kind, chat_id, uid):
    return kb([b("↩️ Undo", f"ud:{kind}:{chat_id}:{uid}")])


@dp.message(Cmd("ban"))
async def c_ban(m: Message, args: str):
    g = await gate(m, "can_restrict_members", args=args)
    if not g:
        return
    t, rest, gs = g
    until, reason, label, _ = split_time(rest)
    try:
        await bot.ban_chat_member(m.chat.id, t.id, until_date=until)
    except TelegramAPIError as e:
        return await fail(m, e, gs)
    await say(m, "🔴", "BAN DONE", [("👤", "User", mt(t)), ("🆔", "ID", f"<code>{t.id}</code>"), ("👮", "By", actor_of(m)),
                                    ("⏳", "Duration", label), ("📝", "Reason", esc(reason or "—"))],
              undo_kb("b", m.chat.id, t.id), gs)
    await log_ev("ban", f"{t.id} banned in {m.chat.id} by {m.from_user.id}", m.chat.id)


@dp.message(Cmd("unban"))
async def c_unban(m: Message, args: str):
    g = await gate(m, "can_restrict_members", args=args, skip_guard=True)
    if not g:
        return
    t, _, gs = g
    try:
        await bot.unban_chat_member(m.chat.id, t.id, only_if_banned=True)
    except TelegramAPIError as e:
        return await fail(m, e, gs)
    await say(m, "🟢", "UNBAN DONE", [("👤", "User", mt(t)), ("🆔", "ID", f"<code>{t.id}</code>"), ("👮", "By", actor_of(m))], gs=gs)


@dp.message(Cmd("kick"))
async def c_kick(m: Message, args: str):
    g = await gate(m, "can_restrict_members", args=args)
    if not g:
        return
    t, _, gs = g
    try:
        await kick_user(m.chat.id, t.id)
    except TelegramAPIError as e:
        return await fail(m, e, gs)
    await say(m, "👢", "KICK DONE", [("👤", "User", mt(t)), ("🆔", "ID", f"<code>{t.id}</code>"), ("👮", "By", actor_of(m))], gs=gs)


@dp.message(Cmd("mute"))
async def c_mute(m: Message, args: str):
    g = await gate(m, "can_restrict_members", args=args)
    if not g:
        return
    t, rest, gs = g
    _, reason, label, secs = split_time(rest)
    if not await mute_user(m.chat.id, t.id, secs):
        return await say(m, "❌", "FAILED", [("⚠️", "Reason", "Bot ko Ban Users right chahiye.")], gs=gs, delete_after=15)
    await say(m, "🔇", "MUTE DONE", [("👤", "User", mt(t)), ("🆔", "ID", f"<code>{t.id}</code>"), ("👮", "By", actor_of(m)),
                                     ("⏳", "Duration", label), ("📝", "Reason", esc(reason or "—"))],
              undo_kb("m", m.chat.id, t.id), gs)


@dp.message(Cmd("unmute"))
async def c_unmute(m: Message, args: str):
    g = await gate(m, "can_restrict_members", args=args, skip_guard=True)
    if not g:
        return
    t, _, gs = g
    try:
        await unmute_user(m.chat.id, t.id)
    except TelegramAPIError as e:
        return await fail(m, e, gs)
    await say(m, "🔊", "UNMUTE DONE", [("👤", "User", mt(t)), ("🆔", "ID", f"<code>{t.id}</code>"), ("👮", "By", actor_of(m))], gs=gs)


@dp.message(Cmd("warn"))
async def c_warn(m: Message, args: str):
    g = await gate(m, "can_restrict_members", args=args)
    if not g:
        return
    t, reason, gs = g
    count = await add_warn(m.chat.id, t.id, reason or "No reason", m.from_user.id)
    acted = await apply_limit(m.chat.id, t.id, count, gs)
    rows = [("👤", "User", mt(t)), ("🆔", "ID", f"<code>{t.id}</code>"), ("👮", "By", actor_of(m)),
            ("⚠️", "Warns", f"{min(count, gs['warn_limit'])}/{gs['warn_limit']}  {pbar(count, gs['warn_limit'], 6)}"),
            ("📝", "Reason", esc(reason or "—"))]
    if acted:
        rows.append(("⚡", "Limit reached", f"<b>{acted}</b> applied"))
    await say(m, "⚠️", "WARNING", rows, None if acted else undo_kb("w", m.chat.id, t.id), gs)


@dp.message(Cmd("warns"))
async def c_warns(m: Message, args: str):
    g = await gate(m, "can_restrict_members", args=args, skip_guard=True)
    if not g:
        return
    t, _, gs = g
    docs = await sdb(db.warns.find({"chat_id": m.chat.id, "user_id": t.id}).sort("created", -1).to_list(10), [])
    lines = [f"┃ {i + 1}. {esc(d.get('reason', '—'))} · {fmt_dt(d['created'])}" for i, d in enumerate(docs)] or ["┃ Koi active warning nahi."]
    await say(m, "📋", "ACTIVE WARNS", [("👤", "User", mt(t)), ("⚠️", "Count", f"{len(docs)}/{gs['warn_limit']}"), ""] + lines, gs=gs)


@dp.message(Cmd("resetwarns"))
async def c_resetwarns(m: Message, args: str):
    g = await gate(m, "can_restrict_members", args=args, skip_guard=True)
    if not g:
        return
    t, _, gs = g
    await sdb(db.warns.delete_many({"chat_id": m.chat.id, "user_id": t.id}))
    WARN_MEM.pop((m.chat.id, t.id))
    await say(m, "♻️", "WARNS RESET", [("👤", "User", mt(t)), ("👮", "By", actor_of(m))], gs=gs)


@dp.callback_query(F.data.startswith("ud:"))
async def cb_undo(cb: CallbackQuery):
    _, kind, cid, uid = cb.data.split(":")
    cid, uid = int(cid), int(uid)
    if not await has_right(cid, cb.from_user.id, "can_restrict_members"):
        return await cb.answer("Tumhare paas right nahi hai.", show_alert=True)
    try:
        if kind == "b":
            await bot.unban_chat_member(cid, uid, only_if_banned=True)
        elif kind == "m":
            await unmute_user(cid, uid)
        else:
            last = await sdb(db.warns.find_one({"chat_id": cid, "user_id": uid}, sort=[("created", -1)]))
            if last:
                await sdb(db.warns.delete_one({"_id": last["_id"]}))
    except TelegramAPIError as e:
        return await cb.answer(short(str(e), 150), show_alert=True)
    await edit(cb.message, card("↩️", "UNDONE", [("🆔", "ID", f"<code>{uid}</code>"),
                                                 ("👮", "By", mention(cb.from_user.id, cb.from_user.full_name))], await get_gs(cid)))
    await cb.answer("Done")


@dp.message(Cmd("del"))
async def c_del(m: Message):
    g = await gate(m, "can_delete_messages", need_target=False)
    if not g:
        return
    if not m.reply_to_message:
        return await say(m, "⚠️", "REPLY NEEDED", [("💡", "Use", "Jis message ko delete karna hai usse reply karo.")], delete_after=10)
    await try_delete(m.chat.id, m.reply_to_message.message_id)
    await try_delete(m.chat.id, m.message_id)


@dp.message(Cmd("purge"))
async def c_purge(m: Message):
    g = await gate(m, "can_delete_messages", need_target=False)
    if not g:
        return
    if not m.reply_to_message:
        return await say(m, "⚠️", "REPLY NEEDED", [("💡", "Use", "Jahan se purge karna hai us message ko reply karo.")], delete_after=10)
    ids = list(range(m.reply_to_message.message_id, m.message_id + 1))[:200]
    done = 0
    for i in range(0, len(ids), 100):
        try:
            await bot.delete_messages(m.chat.id, ids[i:i + 100])
            done += len(ids[i:i + 100])
        except TelegramAPIError as e:
            log.info("purge chunk failed: %s", e)
    await say(m, "🧹", "PURGED", [("🗑", "Messages", done), ("👮", "By", actor_of(m))], delete_after=6)


@dp.message(Cmd("pin"))
async def c_pin(m: Message, args: str):
    g = await gate(m, "can_pin_messages", need_target=False)
    if not g:
        return
    if not m.reply_to_message:
        return await say(m, "⚠️", "REPLY NEEDED", [("💡", "Use", "Pin karne ke liye message par reply karo.")], delete_after=10)
    try:
        await bot.pin_chat_message(m.chat.id, m.reply_to_message.message_id, disable_notification="quiet" in args.lower())
    except TelegramAPIError as e:
        return await fail(m, e)
    await say(m, "📌", "PINNED", [("👮", "By", actor_of(m))], delete_after=10)


@dp.message(Cmd("unpin"))
async def c_unpin(m: Message):
    g = await gate(m, "can_pin_messages", need_target=False)
    if not g:
        return
    try:
        await bot.unpin_chat_message(m.chat.id, m.reply_to_message.message_id if m.reply_to_message else None)
    except TelegramAPIError as e:
        return await fail(m, e)
    await say(m, "📍", "UNPINNED", [("👮", "By", actor_of(m))], delete_after=10)


@dp.message(Cmd("lock"))
async def c_lock(m: Message):
    g = await gate(m, "can_restrict_members", need_target=False)
    if not g:
        return
    try:
        await bot.set_chat_permissions(m.chat.id, NOPERM)
    except TelegramAPIError as e:
        return await fail(m, e)
    await set_gs(m.chat.id, locked=True)
    await say(m, "🔒", "GROUP LOCKED", [("💬", "Info", "Sirf admins message kar sakte hain."), ("👮", "By", actor_of(m))])


@dp.message(Cmd("unlock"))
async def c_unlock(m: Message):
    g = await gate(m, "can_restrict_members", need_target=False)
    if not g:
        return
    try:
        await bot.set_chat_permissions(m.chat.id, FULL)
    except TelegramAPIError as e:
        return await fail(m, e)
    await set_gs(m.chat.id, locked=False)
    await say(m, "🔓", "GROUP UNLOCKED", [("💬", "Info", "Sab members message kar sakte hain."), ("👮", "By", actor_of(m))])


LEVELS = {
    1: ("Junior", dict(can_manage_chat=True, can_delete_messages=True, can_pin_messages=True)),
    2: ("Senior", dict(can_manage_chat=True, can_delete_messages=True, can_pin_messages=True,
                       can_restrict_members=True, can_invite_users=True, can_manage_video_chats=True)),
    3: ("Full", dict(can_manage_chat=True, can_delete_messages=True, can_pin_messages=True,
                     can_restrict_members=True, can_invite_users=True, can_manage_video_chats=True,
                     can_change_info=True)),
}
NO_ADMIN = dict(can_manage_chat=False, can_delete_messages=False, can_pin_messages=False, can_restrict_members=False,
                can_invite_users=False, can_manage_video_chats=False, can_change_info=False, can_promote_members=False)


@dp.message(Cmd("promote"))
async def c_promote(m: Message, args: str):
    g = await gate(m, "can_promote_members", args=args, skip_guard=True)
    if not g:
        return
    t, rest, gs = g
    lvl = int(rest.split()[0]) if rest.split() and rest.split()[0] in ("1", "2", "3") else 1
    if t.id == BOT_ID:
        return await say(m, "🛡", "BLOCKED", [("⚠️", "Reason", "Bot khud ko promote nahi kar sakta.")], gs=gs, delete_after=10)
    name, perms = LEVELS[lvl]
    try:
        await bot.promote_chat_member(m.chat.id, t.id, **perms)
    except TelegramAPIError as e:
        return await fail(m, e, gs)
    await say(m, "⭐", "PROMOTED", [("👤", "User", mt(t)), ("🎖", "Level", f"{lvl} · {name}"), ("👮", "By", actor_of(m))], gs=gs)


@dp.message(Cmd("demote"))
async def c_demote(m: Message, args: str):
    g = await gate(m, "can_promote_members", args=args, skip_guard=True)
    if not g:
        return
    t, _, gs = g
    if t.id == BOT_ID or is_sudo(t.id) and m.from_user.id != OWNER_ID:
        return await say(m, "🛡", "BLOCKED", [("⚠️", "Reason", "Is user par action allowed nahi.")], gs=gs, delete_after=10)
    try:
        await bot.promote_chat_member(m.chat.id, t.id, **NO_ADMIN)
    except TelegramAPIError as e:
        return await fail(m, e, gs)
    await say(m, "⬇️", "DEMOTED", [("👤", "User", mt(t)), ("👮", "By", actor_of(m))], gs=gs)


# ════════════════════════════ PROTECTION / SETUP / VIP ════════════════════════════
def norm_word(w):
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z]", "", w.lower().translate(LEET)))


@dp.message(Cmd("protect"))
async def c_protect(m: Message, args: str):
    g = await gate(m, None, need_target=False)
    if not g:
        return
    if not await is_admin(m.chat.id, m.from_user.id):
        return await say(m, "🚫", "NO PERMISSION", [("🔐", "Need", "Admin only")], delete_after=10)
    a = args.lower().strip()
    if a not in ("on", "off"):
        return await say(m, "🛡", "PROTECTION", [("📌", "Status", "ON ✅" if g[2]["protect"] else "OFF ❌"), ("💡", "Use", "/protect on|off")])
    await set_gs(m.chat.id, protect=(a == "on"))
    await say(m, "🛡", "PROTECTION", [("📌", "Status", "ON ✅" if a == "on" else "OFF ❌"), ("👮", "By", actor_of(m))])


BW_CACHE = LRU(1000)


async def get_badwords(chat_id):
    hit = BW_CACHE.get(chat_id)
    if hit and time.time() - hit[0] < 120:
        return hit[1]
    docs = await sdb(db.badwords.find({"chat_id": chat_id}).to_list(500), None)
    if docs is None:
        return hit[1] if hit else set()
    words = {d["word"] for d in docs}
    BW_CACHE.set(chat_id, (time.time(), words))
    return words


@dp.message(Cmd("addbadword"))
async def c_addbad(m: Message, args: str):
    if not is_group(m) or not await is_admin(m.chat.id, m.from_user.id):
        return
    words = {norm_word(w) for w in re.split(r"[\s,]+", args) if w}
    words = [w for w in words if len(w) >= 2][:20]
    for w in words:
        await sdb(db.badwords.update_one({"chat_id": m.chat.id, "word": w}, {"$set": {"by": m.from_user.id}}, upsert=True))
    BW_CACHE.pop(m.chat.id)
    await try_delete(m.chat.id, m.message_id)
    await say(m, "🚫", "BAD WORDS ADDED", [("➕", "Count", len(words))], delete_after=10)


@dp.message(Cmd("delbadword"))
async def c_delbad(m: Message, args: str):
    if not is_group(m) or not await is_admin(m.chat.id, m.from_user.id):
        return
    w = norm_word(args)
    r = await sdb(db.badwords.delete_one({"chat_id": m.chat.id, "word": w}))
    BW_CACHE.pop(m.chat.id)
    await try_delete(m.chat.id, m.message_id)
    await say(m, "✅", "BAD WORD REMOVED" if r and r.deleted_count else "NOT FOUND", [("🔤", "Word", esc(w))], delete_after=10)


@dp.message(Cmd("setwelcome"))
async def c_setwelcome(m: Message, args: str):
    if not is_group(m) or not await is_admin(m.chat.id, m.from_user.id):
        return
    text = args or (m.reply_to_message.text if m.reply_to_message and m.reply_to_message.text else "")
    if not text:
        return await say(m, "⚠️", "USAGE", [("💡", "Use", "/setwelcome Welcome {name} to {group}!")], delete_after=15)
    await set_gs(m.chat.id, welcome=text[:500])
    await say(m, "🎉", "WELCOME SET", [("📝", "Text", esc(short(text, 150)))])


@dp.message(Cmd("setrules"))
async def c_setrules(m: Message, args: str):
    if not is_group(m) or not await is_admin(m.chat.id, m.from_user.id):
        return
    text = args or (m.reply_to_message.text if m.reply_to_message and m.reply_to_message.text else "")
    if not text:
        return await say(m, "⚠️", "USAGE", [("💡", "Use", "/setrules <rules text>")], delete_after=15)
    await set_gs(m.chat.id, rules=text[:1500])
    await say(m, "📜", "RULES SET", [("✅", "Info", "Rules save ho gaye. /rules se dekho.")])


@dp.message(Cmd("announce"))
async def c_announce(m: Message, args: str):
    g = await gate(m, "can_pin_messages", need_target=False)
    if not g:
        return
    text = args or (m.reply_to_message.text or m.reply_to_message.caption or "" if m.reply_to_message else "")
    if not text:
        return await say(m, "⚠️", "USAGE", [("💡", "Use", "/announce <text>")], delete_after=15)
    msg = await send(m.chat.id, card("📢", "ANNOUNCEMENT", [esc(text)], g[2]), disable_notification=True)
    await try_delete(m.chat.id, m.message_id)
    if msg:
        try:
            await bot.pin_chat_message(m.chat.id, msg.message_id, disable_notification=True)
        except TelegramAPIError as e:
            log.info("announce pin failed: %s", e)


@dp.message(Cmd("addvip"))
async def c_addvip(m: Message, args: str):
    g = await gate(m, None, args=args, skip_guard=True)
    if not g or not await is_admin(m.chat.id, m.from_user.id):
        return
    t, _, gs = g
    await sdb(db.vips.update_one({"chat_id": m.chat.id, "user_id": t.id}, {"$set": {"name": short(t.name, 40), "by": m.from_user.id}}, upsert=True))
    VIP_CACHE.pop(m.chat.id)
    await say(m, "💎", "VIP ADDED", [("👤", "User", mt(t)), ("👮", "By", actor_of(m))], gs=gs)


@dp.message(Cmd("delvip"))
async def c_delvip(m: Message, args: str):
    g = await gate(m, None, args=args, skip_guard=True)
    if not g or not await is_admin(m.chat.id, m.from_user.id):
        return
    t, _, gs = g
    await sdb(db.vips.delete_one({"chat_id": m.chat.id, "user_id": t.id}))
    VIP_CACHE.pop(m.chat.id)
    await say(m, "💔", "VIP REMOVED", [("👤", "User", mt(t)), ("👮", "By", actor_of(m))], gs=gs)


# ════════════════════════════ SETTINGS PANEL ════════════════════════════
TOGGLES = {"protect": "Protection", "link_approval": "Link Approval", "welcome_on": "Welcome", "cleanservice": "Clean Service",
           "sticker_on": "Stickers", "sticker_captcha": "Sticker Captcha", "reports_on": "6H Reports"}
WARN_CYCLE = [2, 3, 4, 5, 6, 8, 10]
ACTIONS = ["mute", "ban", "kick"]


def onoff(v):
    return "ON ✅" if v else "OFF ❌"


def settings_view(cid, title, gs):
    def t(k):
        return b(f"{'🟢' if gs[k] else '🔴'} {TOGGLES[k]}", f"st:{cid}:tg:{k}")
    k = kb([t("protect"), t("link_approval")],
           [b(f"⚠️ Warn Limit: {gs['warn_limit']}", f"st:{cid}:wl"), b(f"⚡ Action: {gs['action'].title()}", f"st:{cid}:ac")],
           [t("welcome_on"), t("cleanservice")], [t("sticker_on"), t("sticker_captcha")], [t("reports_on")],
           [b(f"🎨 Theme: {gs['theme'].title()}", f"st:{cid}:th"), b(f"🔤 Font: {gs['font'].title()}", f"st:{cid}:fn")],
           [b("✖ Close", f"st:{cid}:x")])
    rows = [("💬", "Group", esc(short(title, 30))), ("🛡", "Protection", onoff(gs["protect"])),
            ("⚠️", "Warns", f"{gs['warn_limit']} → {gs['action'].title()}"), ("🔗", "Link approval", onoff(gs["link_approval"])),
            ("🎨", "Theme / Font", f"{gs['theme'].title()} / {gs['font'].title()}"), "", "┃ Buttons se settings badlo 👇"]
    return card("⚙️", "CONTROL PANEL", rows, gs), k


@dp.message(Cmd("settings"))
async def c_settings(m: Message):
    if is_private(m):
        return await say(m, "⚙️", "SETTINGS", [("💡", "Info", "Control Panel se apna group chuno.")], kb([b("🎛 Control Panel", "pn:home")]))
    if not is_group(m):
        return
    if not await is_admin(m.chat.id, m.from_user.id):
        return await say(m, "🚫", "NO PERMISSION", [("🔐", "Need", "Admin only")], delete_after=10)
    text, k = settings_view(m.chat.id, m.chat.title, await get_gs(m.chat.id))
    await send(m.chat.id, text, reply_markup=k)


@dp.callback_query(F.data.startswith("st:"))
async def cb_settings(cb: CallbackQuery):
    parts = cb.data.split(":")
    cid, act = int(parts[1]), parts[2]
    if not await is_admin(cid, cb.from_user.id):
        return await cb.answer("Sirf admins.", show_alert=True)
    gs = await get_gs(cid)
    if act == "x":
        await try_delete(cb.message.chat.id, cb.message.message_id)
        return await cb.answer()
    if act == "tg" and parts[3] in TOGGLES:
        await set_gs(cid, **{parts[3]: not gs[parts[3]]})
    elif act == "wl":
        cur = gs["warn_limit"]
        await set_gs(cid, warn_limit=WARN_CYCLE[(WARN_CYCLE.index(cur) + 1) % len(WARN_CYCLE)] if cur in WARN_CYCLE else 3)
    elif act == "ac":
        await set_gs(cid, action=ACTIONS[(ACTIONS.index(gs["action"]) + 1) % 3])
    elif act == "sth" and parts[3] in THEMES:
        await set_gs(cid, theme=parts[3])
    elif act == "sfn" and parts[3] in FONT_NAMES:
        await set_gs(cid, font=parts[3])
    elif act == "th":
        await edit(cb.message, card("🎨", "CHOOSE THEME", [f"┃ {THEMES[t]}  {t.title()}" for t in THEMES], gs),
                   kb([b(f"{t.title()}", f"st:{cid}:sth:{t}") for t in THEMES], [b("⬅️ Back", f"st:{cid}:home")]))
        return await cb.answer()
    elif act == "fn":
        btns = [b(("✅ " if gs["font"] == f else "") + f.title(), f"st:{cid}:sfn:{f}") for f in FONT_NAMES]
        sample = [f"┃ {f.title()}: {fancy('GUARDIX', f)}" for f in FONT_NAMES if f != "auto"]
        await edit(cb.message, card("🔤", "CHOOSE FONT", sample + ["┃ Auto: random har card me"], gs),
                   kb(btns[:3], btns[3:6], [btns[6]], [b("⬅️ Back", f"st:{cid}:home")]))
        return await cb.answer()
    gs = await get_gs(cid)
    title = cb.message.chat.title if cb.message.chat.type != "private" else (await sdb(db.groups.find_one({"chat_id": cid}), {}) or {}).get("title", "Group")
    text, k = settings_view(cid, title, gs)
    await edit(cb.message, text, k)
    await cb.answer()


@dp.callback_query(F.data == "pn:home")
async def cb_panel(cb: CallbackQuery):
    uid = cb.from_user.id
    if is_sudo(uid):
        docs = await sdb(db.groups.find({"active": {"$ne": False}}).limit(10).to_list(10), [])
    else:
        mems = await sdb(db.members.find({"user_id": uid}, {"chat_id": 1}).limit(40).to_list(40), [])
        docs = []
        for d in mems:
            if await is_admin(d["chat_id"], uid):
                docs.append(await sdb(db.groups.find_one({"chat_id": d["chat_id"]})) or {"chat_id": d["chat_id"]})
            if len(docs) >= 10:
                break
    btns = [[b(f"⚙️ {short(d.get('title') or d['chat_id'], 28)}", f"st:{d['chat_id']}:home")] for d in docs]
    btns.append([b("➕ Add to Group", url=add_url())])
    rows = ["┃ Apna group chuno 👇"] if docs else ["┃ Koi group nahi mila.", "┃ Bot ko group me add karke admin banao."]
    await edit(cb.message, card("🎛", "CONTROL PANEL", rows), Kb(inline_keyboard=btns)) if cb.message.text else \
        await send(cb.message.chat.id, card("🎛", "CONTROL PANEL", rows), reply_markup=Kb(inline_keyboard=btns))
    await cb.answer()


# ════════════════════════════ GAALI FILTER ════════════════════════════
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
                      "@": "a", "$": "s", "!": "i", "|": "i", "€": "e", "+": "t"})


def collapse(s):
    return re.sub(r"(.)\1+", r"\1", s)


BAD_WORDS = """madarchod maderchod behenchod bhenchod behnchod betichod bhosdike bhosdika bhosda bhosdiwala
chutiya chutiye chutiyapa chut chutmarike gandu gaandu gand gaand lund lauda lavda loda lodu randi raand
harami haramkhor haramzada kamina kamine kutta kutti kuttiya bhadwa bhadwe bhadva chodu jhatu tatti hijda
bsdk bkl gandmasti fuck fucker fucking fucked motherfucker shit bullshit bitch asshole bastard dick dickhead
pussy whore slut cunt nigger nigga faggot retard wanker prick""".split()
BASE_BAD = {collapse(w) for w in BAD_WORDS}


def has_bad(text, extra=()):
    words = BASE_BAD | set(extra)
    long_ = [w for w in words if len(w) >= 6]
    low = text[:2000].lower()
    for v in (low, low.translate(LEET)):
        toks = {collapse(t) for t in re.findall(r"[a-z]+", v)}
        if toks & words:
            return True
        comp = collapse(re.sub(r"[^a-z]", "", v))
        if any(w in comp for w in long_):
            return True
    return False


# ════════════════════════════ LINK / BOT-LINK DETECTION ════════════════════════════
LINK_RE = re.compile(r"(?:https?://|www\.|t\.me/|telegram\.me/|telegram\.dog/)\S+|"
                     r"\b[a-z0-9-]+\.(?:com|net|org|xyz|link|gg|ly|site|online|club|info)\b(?:/\S*)?", re.I)
BOTLINK_RE = re.compile(r"(?:t\.me/|telegram\.me/|@)([A-Za-z0-9_]{3,}bot)\b", re.I)


def find_link(m):
    text = m.text or m.caption or ""
    for e in (m.entities or []) + (m.caption_entities or []):
        if e.type == "url":
            return e.extract_from(text)
        if e.type == "text_link":
            return e.url
    mm = LINK_RE.search(text)
    return mm.group(0) if mm else None


# ════════════════════════════ FLOOD / RAID STATE ════════════════════════════
FLOOD, REPEAT, MEDIA = {}, {}, {}
RAID_JOINS, RAID_UNTIL = {}, {}
LINK_CARD_CD = LRU(3000)
BOTLINK_CD = LRU(2000)
FUNNEL_SEEN = LRU(30000)
DM_WELCOMED = LRU(5000)
VERIFIED = LRU(20000)


def cleanup_state():
    cut = time.time() - 120
    for d in (FLOOD, MEDIA):
        for k in [k for k, v in d.items() if not v or v[-1] < cut]:
            d.pop(k, None)
    for k in [k for k, v in REPEAT.items() if not v or v[-1][1] < cut]:
        REPEAT.pop(k, None)
    for k in [k for k, v in RAID_UNTIL.items() if v < time.time()]:
        RAID_UNTIL.pop(k, None)
    for k in [k for k, v in RAID_JOINS.items() if not v or v[-1][0] < cut]:
        RAID_JOINS.pop(k, None)


async def penalize(m, gs, title, reason, secs=300):
    chat, u = m.chat.id, m.from_user
    await try_delete(chat, m.message_id)
    ok = await mute_user(chat, u.id, secs)
    FLOOD.pop((chat, u.id), None)
    REPEAT.pop((chat, u.id), None)
    MEDIA.pop((chat, u.id), None)
    if ok:
        await say(m, "🔇", title, [("👤", "User", mention(u.id, u.full_name)), ("⏳", "Muted", f"{secs // 60} min"),
                                   ("📝", "Reason", reason)], gs=gs, delete_after=30)
        await log_ev("automute", f"{u.id} {reason} in {chat}", chat)


# ════════════════════════════ VERIFIED / CAPTCHA ════════════════════════════
async def is_verified(chat_id, uid):
    k = (chat_id, uid)
    if k in VERIFIED:
        return True
    ok = await sdb(db.verified.find_one({"chat_id": chat_id, "user_id": uid}, {"_id": 1}))
    if ok:
        VERIFIED.set(k, 1)
    return bool(ok)


async def mark_verified(chat_id, uid):
    VERIFIED.set((chat_id, uid), 1)
    await sdb(db.verified.update_one({"chat_id": chat_id, "user_id": uid}, {"$set": {"ts": utcnow()}}, upsert=True))


async def create_captcha(chat_id, title, uid, kind):
    a, c = random.randint(2, 9), random.randint(2, 9)
    op = random.choice(["+", "-", "×"])
    if op == "-" and a < c:
        a, c = c, a
    ans = a + c if op == "+" else a - c if op == "-" else a * c
    opts = {ans}
    while len(opts) < 4:
        opts.add(max(0, ans + random.choice([-6, -4, -3, -2, -1, 1, 2, 3, 4, 6])))
    opts = random.sample(sorted(opts), 4)
    doc = {"_id": secrets.token_hex(4), "chat_id": chat_id, "title": short(title or "", 60), "user_id": uid,
           "kind": kind, "answer": ans, "q": f"{a} {op} {c}", "opts": opts, "attempts": 0, "created": utcnow()}
    try:
        await db.verif.insert_one(doc)
    except PyMongoError as e:
        log.warning("DB error (captcha): %s", e)
        return None
    return doc


async def send_captcha(uid, doc):
    k = kb([b(str(o), f"cp:{doc['_id']}:{o}") for o in doc["opts"][:2]],
           [b(str(o), f"cp:{doc['_id']}:{o}") for o in doc["opts"][2:]])
    rows = [("💬", "Group", esc(doc["title"])), ("🧮", "Solve", f"<b>{doc['q']} = ?</b>"),
            ("🎯", "Attempts", "Max 3"), "", "┃ Sahi answer chuno 👇"]
    return await send(uid, card("🔐", "VERIFY YOURSELF", rows), reply_markup=k)


@dp.callback_query(F.data.startswith("cp:"))
async def cb_captcha(cb: CallbackQuery):
    _, nonce, val = cb.data.split(":")
    uid = cb.from_user.id
    doc = await sdb(db.verif.find_one_and_update({"_id": nonce, "user_id": uid}, {"$inc": {"attempts": 1}},
                                                 return_document=ReturnDocument.AFTER))
    if not doc:
        await cb.answer("Expired ya tumhare liye nahi hai.", show_alert=True)
        return
    await touch_user(cb.from_user)
    cid, kind = doc["chat_id"], doc["kind"]
    if int(val) == doc["answer"]:
        r = await sdb(db.verif.delete_one({"_id": nonce}))
        if not r or r.deleted_count != 1:
            return await cb.answer()
        await mark_verified(cid, uid)
        if kind == "join":
            try:
                await bot.approve_chat_join_request(cid, uid)
            except TelegramBadRequest as e:
                if "ALREADY" not in str(e).upper():
                    await edit(cb.message, card("⌛", "REQUEST EXPIRED", [("💡", "Info", "Join request ab valid nahi hai.")]))
                    return await cb.answer()
            except TelegramAPIError as e:
                log.warning("approve failed: %s", e)
            await edit(cb.message, card("✅", "VERIFIED", [("💬", "Group", esc(doc["title"])), ("🎉", "Status", "Approved! Welcome 🎊")]))
            DM_WELCOMED.set((cid, uid), 1)
            await dm_welcome(cb.from_user, doc["title"])
        else:
            await edit(cb.message, card("✅", "VERIFIED", [("💬", "Group", esc(doc["title"])), ("🎉", "Status", "Ab tum stickers bhej sakte ho.")]))
        return await cb.answer("Verified ✅")
    if doc["attempts"] >= 3:
        await sdb(db.verif.delete_one({"_id": nonce}))
        msg = "Request pending hai, admins decide karenge." if kind == "join" else "Thodi der baad dobara try karo."
        await edit(cb.message, card("🚫", "TOO MANY ATTEMPTS", [("💡", "Info", msg)]))
        return await cb.answer("3 attempts khatam", show_alert=True)
    await cb.answer(f"Galat! {3 - doc['attempts']} attempt bache.", show_alert=True)


@dp.chat_join_request()
async def on_join_request(req: ChatJoinRequest):
    u, chat = req.from_user, req.chat
    if u.id in GBANS:
        try:
            await bot.decline_chat_join_request(chat.id, u.id)
        except TelegramAPIError:
            pass
        return
    doc = await create_captcha(chat.id, chat.title, u.id, "join")
    if not doc:
        return
    msg = await send_captcha(req.user_chat_id, doc)
    if msg:
        await schedule_delete(req.user_chat_id, msg.message_id, 600)
    else:
        log.info("join-request captcha DM failed for %s", u.id)


async def dm_welcome(u, title=None):
    rows = [("👤", "User", mention(u.id, u.full_name))]
    if title:
        rows.append(("💬", "Joined", esc(short(title, 40))))
    rows += ["", "┃ ✨ Premium security, 100% free", "┃ 📖 Help dekho ya group me add karo 👇"]
    photo = await profile_photo(u.id)
    try:
        if photo:
            await bot.send_photo(u.id, photo, caption=card("🎉", "WELCOME", rows), reply_markup=main_kb())
        else:
            await bot.send_message(u.id, card("🎉", "WELCOME", rows), reply_markup=main_kb())
        return True
    except TelegramAPIError:
        return False


# ════════════════════════════ LINK APPROVAL ════════════════════════════
async def handle_link(m, gs, link):
    chat, u = m.chat.id, m.from_user
    now = utcnow()
    ok = await sdb(db.link_approvals.find_one({"chat_id": chat, "user_id": u.id, "until": {"$gt": now}}))
    if ok:
        return False
    await try_delete(chat, m.message_id)
    if time.time() - (LINK_CARD_CD.get((chat, u.id)) or 0) < 30:
        return True
    LINK_CARD_CD.set((chat, u.id), time.time())
    text = m.text or m.caption or ""
    rows = [("👤", "User", f"{mention(u.id, u.full_name)} (ID: <code>{u.id}</code>)"), ("💬", "Group", esc(short(m.chat.title, 40))),
            ("📝", "Message", esc(short(text, 120))), ("🔗", "Link", esc(short(link, 100)))]
    msg = await send(chat, card("🔗", "LINK APPROVAL REQUEST", rows, gs),
                     reply_markup=kb([b("✅ Approve", f"la:ok:{chat}:{u.id}"), b("❌ Reject", f"la:no:{chat}:{u.id}")]))
    if msg:
        await schedule_delete(chat, msg.message_id, 600)
    return True


@dp.callback_query(F.data.startswith("la:"))
async def cb_link(cb: CallbackQuery):
    _, act, cid, uid = cb.data.split(":")
    cid, uid = int(cid), int(uid)
    if not await is_admin(cid, cb.from_user.id):
        return await cb.answer("Sirf admins approve kar sakte hain.", show_alert=True)
    if act == "no":
        await try_delete(cb.message.chat.id, cb.message.message_id)
        return await cb.answer("Rejected")
    now = utcnow()
    await sdb(db.link_approvals.update_one({"chat_id": cid, "user_id": uid}, {"$set": {"created": now, "until": now + timedelta(minutes=10)}}, upsert=True))
    gs = await get_gs(cid)
    await edit(cb.message, card("✅", "LINK APPROVED", [("🆔", "User ID", f"<code>{uid}</code>"), ("⏳", "Window", "10 minutes"),
                                                      ("👮", "By", mention(cb.from_user.id, cb.from_user.full_name))], gs))
    await schedule_delete(cb.message.chat.id, cb.message.message_id, 60)
    note = card("✅", "LINK APPROVED", [("💬", "Group", esc(short(cb.message.chat.title or "", 40))), ("⏳", "Window", "10 min – ab link bhej sakte ho")], gs)
    if not await send(uid, note):
        m2 = await send(cb.message.chat.id, f'{mention(uid, "User")} ' + "tumhara link approve ho gaya (10 min).")
        if m2:
            await schedule_delete(cb.message.chat.id, m2.message_id, 60)
    await cb.answer("Approved")


# ════════════════════════════ MAIN PROTECTION ENGINE ════════════════════════════
async def first_msg_dm(m):
    key = (m.chat.id, m.from_user.id)
    if key in FUNNEL_SEEN:
        return
    FUNNEL_SEEN.set(key, 1)
    uid = m.from_user.id
    if await sdb(db.users.find_one({"user_id": uid, "blocked": {"$ne": True}}, {"_id": 1})):
        return
    r = await sdb(db.members.find_one_and_update({"chat_id": m.chat.id, "user_id": uid, "dm_prompted": {"$ne": True}},
                                                 {"$set": {"dm_prompted": True}}))
    if not r:
        return
    try:
        await bot.send_message(uid, card("🛡", "GUARDIX", [("💬", "Group", esc(short(m.chat.title, 40))),
                                                          "", "┃ Premium security ke liye bot start karo 👇"]),
                               reply_markup=kb([b("🚀 Start Bot", url=bot_url("start"))]), disable_notification=True)
    except TelegramAPIError:
        pass


async def protect(m, edited=False):
    u = m.from_user
    if not u or u.is_bot or m.sender_chat or not has_content(m):
        return
    chat = m.chat.id
    if u.id in GBANS and not is_sudo(u.id):
        try:
            await bot.ban_chat_member(chat, u.id)
            await try_delete(chat, m.message_id)
            await log_ev("gban-hit", f"{u.id} banned in {chat}", chat)
        except TelegramAPIError:
            pass
        return
    gs = await get_gs(chat)
    text = m.text or m.caption or ""
    if not edited and not await is_admin(chat, u.id):
        spawn(first_msg_dm(m))
    if not edited and text and re.match(r"^@admins?\b", text, re.I):
        return await do_report(m, text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else "")
    if not gs["protect"] or await exempt(chat, u.id):
        return
    # bot link alert
    if text:
        bm = BOTLINK_RE.search(text)
        if bm and bm.group(1).lower() != BOT_USERNAME.lower() and time.time() - (BOTLINK_CD.get((chat, u.id)) or 0) > 60:
            BOTLINK_CD.set((chat, u.id), time.time())
            spawn(alert(card("🤖", "BOT LINK SHARED", [("👤", "User", mention(u.id, u.full_name)), ("💬", "Group", esc(short(m.chat.title, 40))),
                                                      ("🔗", "Bot", "@" + esc(bm.group(1)))])))
    # 1. gaali
    if text:
        extra = await get_badwords(chat)
        if has_bad(text, extra):
            await try_delete(chat, m.message_id)
            count = await add_warn(chat, u.id, "Abusive language", BOT_ID)
            acted = await apply_limit(chat, u.id, count, gs)
            rows = [("👤", "User", mention(u.id, u.full_name)), ("🚫", "Reason", "Gaali / abusive language"),
                    ("⚠️", "Warns", f"{min(count, gs['warn_limit'])}/{gs['warn_limit']}  {pbar(count, gs['warn_limit'], 6)}")]
            if acted:
                rows.append(("⚡", "Action", f"<b>{acted}</b>"))
            await say(m, "🚫", "GAALI DETECTED", rows, gs=gs, delete_after=30)
            return
    # 2. links
    if gs["link_approval"]:
        link = find_link(m)
        if link and await handle_link(m, gs, link):
            return
    if edited:
        return
    # 3. sticker captcha
    if m.sticker and not gs["sticker_on"]:
        if await is_verified(chat, u.id):
            return
        await try_delete(chat, m.message_id)
        if gs["sticker_captcha"]:
            await start_sticker_captcha(m)
        return
    now = time.time()
    key = (chat, u.id)
    # 4. flood
    dq = FLOOD.setdefault(key, deque(maxlen=10))
    while dq and now - dq[0] > 8:
        dq.popleft()
    dq.append(now)
    if len(dq) >= 6:
        return await penalize(m, gs, "FLOOD DETECTED", "6+ messages in 8s")
    # 5. repeat
    if m.text:
        h = hash(text.strip().lower())
        rq = REPEAT.setdefault(key, deque(maxlen=6))
        rq.append((h, now))
        if sum(1 for x, t in rq if x == h and now - t <= 30) >= 4:
            return await penalize(m, gs, "REPEAT SPAM", "Same message 4x in 30s")
    # 6. media spam
    if m.sticker or m.photo or m.video or m.forward_origin:
        mq = MEDIA.setdefault(key, deque(maxlen=8))
        while mq and now - mq[0] > 10:
            mq.popleft()
        mq.append(now)
        if len(mq) >= 5:
            return await penalize(m, gs, "MEDIA SPAM", "5+ media/forwards in 10s")


async def start_sticker_captcha(m):
    chat, u = m.chat.id, m.from_user
    if await sdb(db.verif.find_one({"chat_id": chat, "user_id": u.id, "kind": "sticker"}, {"_id": 1})):
        return  # 1 hour cooldown (TTL doc still alive)
    doc = await create_captcha(chat, m.chat.title, u.id, "sticker")
    if not doc:
        return
    msg = await send_captcha(u.id, doc)
    if msg:
        await schedule_delete(u.id, msg.message_id, 600)
        return
    g = await send(chat, f"{mention(u.id, u.full_name)} sticker ke liye verify karo 👇",
                   reply_markup=kb([b("✅ Verify Karo", url=bot_url("vf_" + doc["_id"]))]))
    if g:
        await schedule_delete(chat, g.message_id, 600)


# ════════════════════════════ JOIN / LEAVE / SERVICE ════════════════════════════
def render_welcome(gs, u, title):
    if gs.get("welcome"):
        return esc(gs["welcome"]).replace("{name}", mention(u.id, u.full_name)).replace("{group}", esc(title or ""))
    return None


async def raid_check(m, gs, u):
    chat, now = m.chat.id, time.time()
    dq = RAID_JOINS.setdefault(chat, deque(maxlen=30))
    dq.append((now, u.id))
    recent = [uid for t, uid in dq if now - t <= 30]
    if RAID_UNTIL.get(chat, 0) > now:
        await mute_user(chat, u.id, 600)
        return True
    if len(recent) >= 8:
        RAID_UNTIL[chat] = now + 600
        for uid in set(recent):
            await mute_user(chat, uid, 600)
            await asyncio.sleep(0.05)
        await say(m, "🚨", "RAID PROTECTION", [("👥", "Joins", f"{len(recent)} in 30s"), ("🔇", "Action", "New members 10 min muted")],
                  gs=gs, delete_after=300)
        await alert(card("🚨", "RAID DETECTED", [("💬", "Group", esc(short(m.chat.title, 40))), ("👥", "Joins", len(recent))]))
        return True
    return False


@dp.message(F.new_chat_members)
async def on_join(m: Message):
    chat, adder = m.chat, m.from_user
    gs = await get_gs(chat.id)
    if gs["cleanservice"]:
        await schedule_delete(chat.id, m.message_id, 3600)
    welcomed = 0
    for u in m.new_chat_members:
        if u.id == BOT_ID:
            continue
        if u.is_bot:
            if gs["protect"] and adder and adder.id != u.id and not await is_admin(chat.id, adder.id):
                try:
                    await kick_user(chat.id, u.id)
                    await say(m, "🤖", "ANTI-BOT", [("🤖", "Bot", f"@{esc(u.username or u.id)}"), ("👤", "Added by", mention(adder.id, adder.full_name)),
                                                     ("⚡", "Action", "Bot kicked")], gs=gs, delete_after=60)
                except TelegramAPIError as e:
                    log.info("antibot failed: %s", e)
            continue
        await touch_member(chat, u)
        if u.id in GBANS:
            try:
                await bot.ban_chat_member(chat.id, u.id)
                await say(m, "🚫", "GBAN HIT", [("👤", "User", mention(u.id, u.full_name)), ("⚡", "Action", "Auto-banned (global ban)")], gs=gs, delete_after=60)
            except TelegramAPIError:
                pass
            continue
        if gs["protect"] and await raid_check(m, gs, u):
            continue
        if not gs["welcome_on"] or welcomed >= 5:
            continue
        welcomed += 1
        dm_ok = (chat.id, u.id) in DM_WELCOMED
        if not dm_ok:
            dm_ok = await dm_welcome(u, chat.title)
        custom = render_welcome(gs, u, chat.title)
        rows = [("👤", "User", mention(u.id, u.full_name)), ("🆔", "ID", f"<code>{u.id}</code>"), ("💬", "Group", esc(short(chat.title, 40)))]
        btns = [b("📜 Rules", f"wl:r:{chat.id}"), b("💎 VIP Lounge", f"wl:v:{chat.id}")]
        rows_kb = [btns] if dm_ok else [btns, [b("🚀 Start Bot", url=bot_url("start"))]]
        photo = await profile_photo(u.id)
        msg = await send_card(chat.id, photo, card("🎉", "WELCOME", rows, gs, custom), Kb(inline_keyboard=rows_kb))
        if msg:
            await schedule_delete(chat.id, msg.message_id, 86400)


@dp.callback_query(F.data.startswith("wl:"))
async def cb_welcome(cb: CallbackQuery):
    _, act, cid = cb.data.split(":")
    cid = int(cid)
    if act == "r":
        gs = await get_gs(cid)
        txt = gs.get("rules") or "Abhi rules set nahi hain."
        return await cb.answer(short("📜 " + txt, 195), show_alert=True)
    docs = await sdb(db.vips.find({"chat_id": cid}).to_list(20), [])
    txt = "💎 " + (", ".join(short(d.get("name", "VIP"), 14) for d in docs) if docs else "Abhi koi VIP nahi.")
    await cb.answer(short(txt, 195), show_alert=True)


SERVICE = (F.left_chat_member | F.new_chat_title | F.new_chat_photo | F.delete_chat_photo | F.pinned_message
           | F.group_chat_created | F.video_chat_started | F.video_chat_ended | F.video_chat_scheduled
           | F.video_chat_participants_invited | F.message_auto_delete_timer_changed)


@dp.message(SERVICE)
async def on_service(m: Message):
    if m.left_chat_member and not m.left_chat_member.is_bot:
        await sdb(db.members.delete_one({"chat_id": m.chat.id, "user_id": m.left_chat_member.id}))
        MEMBER_NAMES.pop((m.chat.id, m.left_chat_member.id))
    gs = await get_gs(m.chat.id)
    if gs["cleanservice"]:
        await schedule_delete(m.chat.id, m.message_id, 3600)


@dp.my_chat_member()
async def on_my_chat_member(ev: ChatMemberUpdated):
    chat = ev.chat
    new, old = ev.new_chat_member.status, ev.old_chat_member.status
    gone = (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)
    if chat.type == "private":
        if new == ChatMemberStatus.KICKED:
            await sdb(db.users.update_one({"user_id": chat.id}, {"$set": {"blocked": True}}))
        elif new == ChatMemberStatus.MEMBER:
            await sdb(db.users.update_one({"user_id": chat.id}, {"$set": {"blocked": False}}))
        return
    if chat.type not in ("group", "supergroup"):
        return
    who = mention(ev.from_user.id, ev.from_user.full_name)
    if new in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR) and old in gone:
        await sdb(db.groups.update_one({"chat_id": chat.id}, {"$set": {"title": short(chat.title or "", 80), "active": True, "added_by": ev.from_user.id, "added_at": utcnow()}}, upsert=True))
        GS_CACHE.pop(chat.id)
        GROUP_SEEN.set(chat.id, 1)
        await alert(card("➕", "BOT ADDED", [("💬", "Group", esc(short(chat.title, 40))), ("🆔", "ID", f"<code>{chat.id}</code>"), ("👤", "By", who)]))
        await log_ev("bot-added", f"{chat.id} {chat.title}", chat.id)
        msg = await send(chat.id, card("🛡", "GUARDIX ACTIVE", [("✅", "Status", "Protection ON"), "",
                                                              "┃ Mujhe admin banao (Delete + Ban rights)", "┃ /settings se panel kholo"]),
                         reply_markup=kb([b("🚀 Open Bot", url=bot_url())]))
        if msg:
            await schedule_delete(chat.id, msg.message_id, 3600)
    elif new in gone:
        await sdb(db.groups.update_one({"chat_id": chat.id}, {"$set": {"active": False}}))
        await alert(card("➖", "BOT REMOVED", [("💬", "Group", esc(short(chat.title, 40))), ("🆔", "ID", f"<code>{chat.id}</code>"), ("👤", "By", who)]))
        await log_ev("bot-removed", f"{chat.id} {chat.title}", chat.id)


# ════════════════════════════ AUTO WATCHERS (registered last for groups) ════════════════════════════
def register_watchers():
    @dp.message(F.chat.type.in_({"group", "supergroup"}))
    async def watch(m: Message):
        await protect(m)

    @dp.edited_message(F.chat.type.in_({"group", "supergroup"}))
    async def watch_edit(m: Message):
        await protect(m, edited=True)


# ════════════════════════════ OWNER / SUDO (DM) ════════════════════════════
def sudo_dm(m):
    return is_private(m) and is_sudo(m.from_user.id)


def owner_dm(m):
    return is_private(m) and m.from_user.id == OWNER_ID


def parse_id(args, m=None):
    mm = re.search(r"-?\d{5,15}", args or "")
    if mm:
        return int(mm.group(0))
    if m and m.reply_to_message and m.reply_to_message.from_user:
        return m.reply_to_message.from_user.id
    return None


@dp.message(Cmd("addsudo"))
async def c_addsudo(m: Message, args: str):
    if not owner_dm(m):
        return
    uid = parse_id(args, m)
    if not uid:
        return await say(m, "⚠️", "USAGE", [("💡", "Use", "/addsudo <user_id>")])
    SUDO.add(uid)
    await sdb(db.sudolist.update_one({"user_id": uid}, {"$set": {"by": m.from_user.id, "ts": utcnow()}}, upsert=True))
    await say(m, "⚡", "SUDO ADDED", [("🆔", "ID", f"<code>{uid}</code>")])
    await log_ev("addsudo", str(uid))


@dp.message(Cmd("delsudo"))
async def c_delsudo(m: Message, args: str):
    if not owner_dm(m):
        return
    uid = parse_id(args, m)
    if not uid:
        return await say(m, "⚠️", "USAGE", [("💡", "Use", "/delsudo <user_id>")])
    SUDO.discard(uid)
    await sdb(db.sudolist.delete_one({"user_id": uid}))
    await say(m, "🗑", "SUDO REMOVED", [("🆔", "ID", f"<code>{uid}</code>")])
    await log_ev("delsudo", str(uid))


@dp.message(Cmd("sudolist"))
async def c_sudolist(m: Message):
    if not sudo_dm(m):
        return
    lines = [f"┃ 👑 <code>{OWNER_ID}</code> (Owner)"] + [f"┃ ⚡ {mention(u, str(u))}" for u in sorted(SUDO - {OWNER_ID})]
    await say(m, "⚡", "SUDO LIST", lines)


@dp.message(Cmd("gban"))
async def c_gban(m: Message, args: str):
    if not owner_dm(m):
        return
    uid = parse_id(args, m)
    if not uid or is_sudo(uid):
        return await say(m, "⚠️", "USAGE", [("💡", "Use", "/gban <user_id> (owner/sudo ban nahi hote)")])
    GBANS.add(uid)
    await sdb(db.gbans.update_one({"user_id": uid}, {"$set": {"by": m.from_user.id, "ts": utcnow()}}, upsert=True))
    await say(m, "🚫", "GLOBAL BAN", [("🆔", "ID", f"<code>{uid}</code>"), ("⚡", "Effect", "Join/message par auto-ban")])
    await log_ev("gban", str(uid))


@dp.message(Cmd("ungban"))
async def c_ungban(m: Message, args: str):
    if not owner_dm(m):
        return
    uid = parse_id(args, m)
    if not uid:
        return await say(m, "⚠️", "USAGE", [("💡", "Use", "/ungban <user_id>")])
    GBANS.discard(uid)
    await sdb(db.gbans.delete_one({"user_id": uid}))
    await say(m, "🟢", "GLOBAL UNBAN", [("🆔", "ID", f"<code>{uid}</code>")])
    await log_ev("ungban", str(uid))


@dp.message(Cmd("leavechat"))
async def c_leavechat(m: Message, args: str):
    if not owner_dm(m):
        return
    mm = re.search(r"-?\d{5,20}", args)
    if not mm:
        return await say(m, "⚠️", "USAGE", [("💡", "Use", "/leavechat <group_id>")])
    cid = int(mm.group(0))
    try:
        await bot.leave_chat(cid)
    except TelegramAPIError as e:
        return await fail(m, e)
    await sdb(db.groups.update_one({"chat_id": cid}, {"$set": {"active": False}}))
    await say(m, "🚪", "LEFT CHAT", [("🆔", "ID", f"<code>{cid}</code>")])


@dp.message(Cmd("uptime"))
async def c_uptime(m: Message):
    if sudo_dm(m):
        await say(m, "⏱", "UPTIME", [("🟢", "Running", fmt_td(time.time() - START_TS)), ("🕒", "Now", fmt_dt(utcnow()))])


@dp.message(Cmd("logs"))
async def c_logs(m: Message):
    if not sudo_dm(m):
        return
    docs = await sdb(db.events.find({"type": {"$ne": "sched_del"}}).sort("created", -1).limit(15).to_list(15), [])
    lines = [f"┃ <code>{local(d['created']).strftime('%d/%m %H:%M')}</code> {esc(d['type'])}: {esc(short(d.get('text', ''), 60))}" for d in docs] or ["┃ Koi log nahi."]
    await say(m, "📜", "RECENT EVENTS", lines)


# ════════════════════════════ BROADCAST ════════════════════════════
PENDING_BC = LRU(50)
BC_LOCK = asyncio.Lock()
BC_STOP = {"flag": False}


async def deliver(chat_id, src):
    for _ in range(3):
        try:
            if src[0] == "copy":
                await bot.copy_message(chat_id, src[1], src[2])
            else:
                await bot.send_message(chat_id, src[1], parse_mode=None)
            return "ok"
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            return "blocked"
        except TelegramAPIError:
            return "fail"
    return "fail"


async def bc_start(m, args, kind):
    if not sudo_dm(m):
        return
    r = m.reply_to_message
    if not args and not r:
        return await say(m, "⚠️", "USAGE", [("💡", "Use", f"/{kind} <message> ya kisi message par reply karo")])
    src = ("copy", r.chat.id, r.message_id) if r else ("text", args)
    bid = secrets.token_hex(3)
    PENDING_BC.set(bid, {"kind": kind, "src": src, "uid": m.from_user.id})
    ng = await sdb(db.groups.count_documents({"active": {"$ne": False}}), 0) if kind in ("broadcast", "gcast") else 0
    nu = await sdb(db.users.count_documents({"blocked": {"$ne": True}}), 0) if kind in ("broadcast", "dmpromo") else 0
    try:
        await deliver(m.chat.id, src)  # preview
    except Exception:
        pass
    await say(m, "📣", "CONFIRM " + kind.upper(), [("👥", "Groups", ng), ("👤", "Users", nu), "", "┃ Upar preview hai. Bhejun?"],
              kb([b("✅ Send", f"bc:{bid}:go"), b("❌ Cancel", f"bc:{bid}:no")]))


@dp.message(Cmd("broadcast"))
async def c_bc(m: Message, args: str):
    await bc_start(m, args, "broadcast")


@dp.message(Cmd("gcast"))
async def c_gcast(m: Message, args: str):
    await bc_start(m, args, "gcast")


@dp.message(Cmd("dmpromo"))
async def c_dmpromo(m: Message, args: str):
    await bc_start(m, args, "dmpromo")


@dp.callback_query(F.data.startswith("bc:"))
async def cb_bc(cb: CallbackQuery):
    _, bid, act = cb.data.split(":")
    if not is_sudo(cb.from_user.id):
        return await cb.answer("Not allowed", show_alert=True)
    job = PENDING_BC.pop(bid)
    if not job or job["uid"] != cb.from_user.id:
        return await cb.answer("Expired.", show_alert=True)
    if act == "no":
        await edit(cb.message, card("❌", "CANCELLED", [("💡", "Info", "Broadcast cancel ho gaya.")]))
        return await cb.answer()
    if BC_LOCK.locked():
        return await cb.answer("Ek broadcast already chal raha hai.", show_alert=True)
    await edit(cb.message, card("🚀", "SENDING...", [("⏳", "Status", "Chal raha hai. Report aayega. (/stop se ruk sakta hai)")]))
    await cb.answer("Started")
    spawn(run_broadcast(cb.message.chat.id, job))


async def run_broadcast(report_to, job):
    async with BC_LOCK:
        BC_STOP["flag"] = False
        kind, src = job["kind"], job["src"]
        targets = []
        if kind in ("broadcast", "gcast"):
            docs = await sdb(db.groups.find({"active": {"$ne": False}}, {"chat_id": 1}).to_list(100000), [])
            targets += [("g", d["chat_id"]) for d in docs]
        if kind in ("broadcast", "dmpromo"):
            docs = await sdb(db.users.find({"blocked": {"$ne": True}}, {"user_id": 1}).to_list(1000000), [])
            targets += [("u", d["user_id"]) for d in docs]
        stat = {"ok": 0, "blocked": 0, "fail": 0}
        for i, (typ, cid) in enumerate(targets, 1):
            if BC_STOP["flag"]:
                break
            r = await deliver(cid, src)
            stat[r] += 1
            if r == "blocked":
                if typ == "u":
                    await sdb(db.users.update_one({"user_id": cid}, {"$set": {"blocked": True}}))
                else:
                    await sdb(db.groups.update_one({"chat_id": cid}, {"$set": {"active": False}}))
            await asyncio.sleep(0.06)
            if i % 25 == 0:
                await asyncio.sleep(1.0)
        await send(report_to, card("📊", "BROADCAST REPORT" + (" (STOPPED)" if BC_STOP["flag"] else ""),
                                   [("🎯", "Targets", len(targets)), ("✅", "Delivered", stat["ok"]),
                                    ("🚫", "Blocked/removed", stat["blocked"]), ("❌", "Failed", stat["fail"])]))
        await log_ev("broadcast", f"{kind}: {stat}")


# ════════════════════════════ /callpm ════════════════════════════
CALLPM_LOG: deque = deque(maxlen=1000)
CALLPM_LOCK = asyncio.Lock()


@dp.message(Cmd("callpm"))
async def c_callpm(m: Message, args: str):
    if not is_sudo(m.from_user.id):
        return
    parts = args.split(None, 1)
    count, text = 50, "👋 Hey! GUARDIX ke premium features dekho – help aur control panel yahan hai."
    if parts and parts[0].isdigit():
        count = int(parts[0])
        if len(parts) > 1:
            text = parts[1]
    elif args:
        text = args
    now = time.time()
    hour = sum(1 for t in CALLPM_LOG if now - t < 3600)
    day = sum(1 for t in CALLPM_LOG if now - t < 86400)
    allowed = max(0, min(count, 100 - hour, 500 - day))
    if allowed == 0:
        return await say(m, "⏳", "RATE LIMIT", [("💡", "Info", "Limit: 100/hour, 500/day. Baad me try karo.")])
    if CALLPM_LOCK.locked():
        return await say(m, "⏳", "BUSY", [("💡", "Info", "Ek /callpm already chal raha hai.")])
    async with CALLPM_LOCK:
        if is_group(m):
            mem = await sdb(db.members.find({"chat_id": m.chat.id}, {"user_id": 1}).to_list(5000), [])
            ids = [d["user_id"] for d in mem]
            started = await sdb(db.users.find({"user_id": {"$in": ids}, "blocked": {"$ne": True}}, {"user_id": 1}).to_list(5000), [])
            targets = [d["user_id"] for d in started]
        else:
            docs = await sdb(db.users.find({"blocked": {"$ne": True}}, {"user_id": 1}).to_list(5000), [])
            targets = [d["user_id"] for d in docs]
        random.shuffle(targets)
        targets = targets[:allowed]
        ok = bad = 0
        k = kb([b("📖 Help", url=bot_url("start")), b("💬 Support", url=support_url())])
        for i, uid in enumerate(targets, 1):
            CALLPM_LOG.append(time.time())
            try:
                await bot.send_message(uid, card("📞", "GUARDIX", [esc(text)]), reply_markup=k)
                ok += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                bad += 1
            except TelegramForbiddenError:
                bad += 1
                await sdb(db.users.update_one({"user_id": uid}, {"$set": {"blocked": True}}))
            except TelegramAPIError:
                bad += 1
            await asyncio.sleep(0.06)
            if i % 25 == 0:
                await asyncio.sleep(1.0)
    await say(m, "📞", "CALLPM DONE", [("🎯", "Targets", len(targets)), ("✅", "Sent", ok), ("❌", "Failed", bad)])


# ════════════════════════════ ZOMBIES ════════════════════════════
ZOMBIE_RUN: dict = {}


@dp.message(Cmd("zombies"))
async def c_zombies(m: Message, args: str):
    if not is_group(m):
        return
    uid = m.from_user.id
    mem = await get_member(m.chat.id, uid, fresh=True)
    if not (uid == OWNER_ID or (mem and mem.status == ChatMemberStatus.CREATOR)):
        return await say(m, "🚫", "OWNER ONLY", [("🔐", "Need", "Sirf group owner ye chala sakta hai.")], delete_after=10)
    if m.chat.id in ZOMBIE_RUN:
        return await say(m, "⏳", "BUSY", [("💡", "Info", "Scan already chal raha hai. /stop se roko.")], delete_after=10)
    lvl = int(args.split()[0]) if args.split() and args.split()[0] in ("1", "2", "3", "4") else 1
    limit = lvl * 100
    docs = await sdb(db.members.find({"chat_id": m.chat.id}, {"user_id": 1}).sort("last", -1).limit(limit).to_list(limit), [])
    ZOMBIE_RUN[m.chat.id] = {"stop": False}
    gs = await get_gs(m.chat.id)
    total, found, kicked = len(docs), 0, 0
    prog = await say(m, "🧟", "ZOMBIE SCAN", [("🎯", "Scanning", f"{total} tracked members"), ("⛔", "Stop", "/stop")], gs=gs,
                     kb_=kb([b("⛔ Stop", f"zs:{m.chat.id}")]))
    try:
        for i, d in enumerate(docs, 1):
            if ZOMBIE_RUN[m.chat.id]["stop"]:
                break
            try:
                cm = await bot.get_chat_member(m.chat.id, d["user_id"])
                if cm.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                    await sdb(db.members.delete_one({"chat_id": m.chat.id, "user_id": d["user_id"]}))
                elif cm.user.first_name == "Deleted Account" and cm.status == ChatMemberStatus.MEMBER:
                    found += 1
                    await kick_user(m.chat.id, d["user_id"])
                    kicked += 1
                    await sdb(db.members.delete_one({"chat_id": m.chat.id, "user_id": d["user_id"]}))
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramAPIError:
                pass
            await asyncio.sleep(0.05)
            if prog and i % 25 == 0:
                await edit(prog, card("🧟", "ZOMBIE SCAN", [("📊", "Progress", f"{pbar(i, total)} {i}/{total}"), ("🧟", "Found", found),
                                                           ("👢", "Kicked", kicked), ("⛔", "Stop", "/stop")], gs),
                           kb([b("⛔ Stop", f"zs:{m.chat.id}")]))
        stopped = ZOMBIE_RUN[m.chat.id]["stop"]
    finally:
        ZOMBIE_RUN.pop(m.chat.id, None)
    final = card("🧟", "ZOMBIE SCAN " + ("STOPPED" if stopped else "DONE"),
                 [("🎯", "Scanned", total), ("🧟", "Found", found), ("👢", "Kicked", kicked),
                  ("💡", "Note", "Sirf tracked members scan hote hain.")], gs)
    if prog:
        await edit(prog, final)
    else:
        await send(m.chat.id, final)


@dp.callback_query(F.data.startswith("zs:"))
async def cb_zstop(cb: CallbackQuery):
    cid = int(cb.data.split(":")[1])
    mem = await get_member(cid, cb.from_user.id, fresh=True)
    if cb.from_user.id == OWNER_ID or (mem and mem.status == ChatMemberStatus.CREATOR):
        if cid in ZOMBIE_RUN:
            ZOMBIE_RUN[cid]["stop"] = True
        return await cb.answer("Stopping…")
    await cb.answer("Sirf group owner.", show_alert=True)


@dp.message(Cmd("stop"))
async def c_stop(m: Message):
    uid = m.from_user.id
    if is_group(m) and m.chat.id in ZOMBIE_RUN:
        mem = await get_member(m.chat.id, uid, fresh=True)
        if uid == OWNER_ID or (mem and mem.status == ChatMemberStatus.CREATOR):
            ZOMBIE_RUN[m.chat.id]["stop"] = True
    elif sudo_dm(m) and BC_LOCK.locked():
        BC_STOP["flag"] = True
        await say(m, "⛔", "STOPPING", [("💡", "Info", "Broadcast rok rahe hain...")])


# ════════════════════════════ EVENTS ════════════════════════════
async def next_event_id():
    last = await sdb(db.bot_events.find_one(sort=[("event_id", -1)]))
    return (last["event_id"] + 1) if last else 1


@dp.message(Cmd("addevent"))
async def c_addevent(m: Message, args: str):
    if not is_sudo(m.from_user.id):
        return
    r = m.reply_to_message
    typ, fid, text = "text", None, args
    if r and r.photo:
        typ, fid, text = "photo", r.photo[-1].file_id, (r.caption or args)
    elif r and (r.text or r.caption):
        text = r.text or r.caption
    if not text and not fid:
        return await say(m, "⚠️", "USAGE", [("💡", "Use", "Photo/text par reply karke /addevent")])
    eid = await next_event_id()
    await sdb(db.bot_events.insert_one({"event_id": eid, "type": typ, "file_id": fid, "text": text, "created_by": m.from_user.id,
                                        "created_at": utcnow(), "active": True}))
    await say(m, "🎪", "EVENT CREATED", [("🆔", "Event ID", eid), ("🎨", "Type", typ)])


@dp.message(Cmd("events"))
async def c_events(m: Message):
    if not is_sudo(m.from_user.id):
        return
    docs = await sdb(db.bot_events.find({"active": True}).sort("event_id", 1).to_list(50), [])
    if not docs:
        return await say(m, "😴", "EVENTS", ["😴 Koi event nahi hai abhi."])
    await say(m, "🎪", f"EVENTS ({len(docs)})", [f"┃ <b>#{d['event_id']}</b> · {d['type']} · {esc(short(d.get('text') or '—', 40))}" for d in docs])


async def show_event(chat_id, d):
    cap = card("🎪", f"EVENT #{d['event_id']}", [esc(d.get("text") or "")])
    if d["type"] == "photo" and d.get("file_id"):
        await send_card(chat_id, d["file_id"], cap)
    else:
        await send(chat_id, cap)


@dp.message(Cmd("event"))
async def c_event(m: Message, args: str):
    if not is_sudo(m.from_user.id):
        return
    if not args.strip().isdigit():
        return await say(m, "⚠️", "USAGE", [("💡", "Use", "/event <id>")])
    d = await sdb(db.bot_events.find_one({"event_id": int(args.strip()), "active": True}))
    if not d:
        return await say(m, "😴", "EVENT", ["😴 Koi event nahi hai abhi."])
    await show_event(m.chat.id, d)


@dp.message(Cmd("delevent"))
async def c_delevent(m: Message, args: str):
    if not is_sudo(m.from_user.id):
        return
    if not args.strip().isdigit():
        return await say(m, "⚠️", "USAGE", [("💡", "Use", "/delevent <id>")])
    r = await sdb(db.bot_events.delete_one({"event_id": int(args.strip())}))
    await say(m, "🗑", "EVENT DELETED" if r and r.deleted_count else "NOT FOUND", [("🆔", "Event ID", args.strip())])


@dp.message(Cmd("clearevents"))
async def c_clearevents(m: Message):
    if m.from_user.id != OWNER_ID:
        return
    r = await sdb(db.bot_events.delete_many({}))
    await say(m, "🧹", "EVENTS CLEARED", [("🗑", "Deleted", r.deleted_count if r else 0)])


# ════════════════════════════ PRIVATE FALLBACK ════════════════════════════
@dp.message(F.chat.type == "private", F.text)
async def private_fallback(m: Message):
    if m.text[:1] in "/.!" and CMD_RE.match(m.text):
        return
    await say(m, "💬", "GUARDIX", [("💡", "Info", "Help ke liye /help use karo.")], main_kb())


# ════════════════════════════ BACKGROUND LOOPS ════════════════════════════
async def msg_flusher():
    while True:
        await asyncio.sleep(5)
        if not MSG_BUF:
            continue
        batch = MSG_BUF[:5000]
        del MSG_BUF[:len(batch)]
        try:
            await db.messages.insert_many(batch, ordered=False)
        except PyMongoError as e:
            log.warning("flush failed: %s", e)
            if len(MSG_BUF) < 15000:
                MSG_BUF[:0] = batch


async def sweeper():
    while True:
        await asyncio.sleep(20)
        try:
            docs = await db.events.find({"type": "sched_del", "due": {"$lte": utcnow()}}).limit(100).to_list(100)
            for d in docs:
                await try_delete(d["chat_id"], d["msg_id"])
                await asyncio.sleep(0.05)
            if docs:
                await db.events.delete_many({"_id": {"$in": [d["_id"] for d in docs]}})
        except PyMongoError as e:
            log.warning("sweeper db error: %s", e)
        except Exception:
            log.exception("sweeper failed")


async def cleaner():
    while True:
        await asyncio.sleep(120)
        cleanup_state()


async def reporter():
    last = None
    while True:
        await asyncio.sleep(30)
        try:
            now = local(utcnow())
            if now.hour not in (0, 6, 12, 18) or now.minute >= 5:
                continue
            key = f"{now:%Y%m%d}-{now.hour}"
            if key == last:
                continue
            last = key
            since = utcnow() - timedelta(hours=24) if now.hour == 0 else now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
            docs = await db.groups.find({"active": {"$ne": False}, "reports_on": {"$ne": False}}, {"chat_id": 1}).to_list(100000)
            for d in docs:
                cid = d["chat_id"]
                r = await db.groups.update_one({"chat_id": cid, "rep_slot": {"$ne": key}}, {"$set": {"rep_slot": key}})
                if r.modified_count != 1:
                    continue
                total, active = await chat_totals(cid, since)
                if total == 0:
                    continue
                top = await top_users(cid, since, 3)
                gs = await get_gs(cid)
                rows = [("🕒", "Time", now.strftime("%I:%M %p")), ("💬", "Messages", total), ("👥", "Active users", active), ""] + top_rows(top)
                await send(cid, card("📊", "6-HOUR REPORT", rows, gs), reply_markup=kb([b("🏆 Full Leaderboard", f"lb:{cid}:0")]))
                await asyncio.sleep(0.1)
        except PyMongoError as e:
            log.warning("reporter db error: %s", e)
        except Exception:
            log.exception("reporter failed")


# ════════════════════════════ ERRORS / MAIN ════════════════════════════
@dp.errors()
async def on_error(event):
    log.error("Unhandled error: %s", event.exception, exc_info=event.exception)
    return True


BOT_COMMANDS = [("start", "VIP lounge"), ("help", "Help menu"), ("id", "Get IDs"), ("me", "My profile"), ("info", "User profile"),
                ("rules", "Group rules"), ("admins", "Admin list"), ("viplist", "VIP members"), ("report", "Report to admins"),
                ("kickme", "Leave group"), ("top", "Today's top 3"), ("stats", "Today's stats"), ("ban", "Ban user"),
                ("unban", "Unban user"), ("kick", "Kick user"), ("mute", "Mute user"), ("unmute", "Unmute user"),
                ("warn", "Warn user"), ("warns", "Show warns"), ("resetwarns", "Reset warns"), ("del", "Delete message"),
                ("purge", "Purge messages"), ("pin", "Pin message"), ("unpin", "Unpin"), ("lock", "Lock group"),
                ("unlock", "Unlock group"), ("promote", "Promote admin"), ("demote", "Demote admin"),
                ("protect", "Protection on/off"), ("addbadword", "Add bad words"), ("delbadword", "Remove bad word"),
                ("setwelcome", "Set welcome"), ("setrules", "Set rules"), ("settings", "Control panel"),
                ("announce", "Pinned announcement"), ("addvip", "Add VIP"), ("delvip", "Remove VIP"), ("zombies", "Kick deleted accounts")]


async def main():
    global BOT_ID, BOT_USERNAME
    dp.message.outer_middleware(Track())
    register_watchers()
    me = await bot.get_me()
    BOT_ID = me.id
    BOT_USERNAME = BOT_USERNAME or me.username
    log.info("GUARDIX starting as @%s (%s)", BOT_USERNAME, BOT_ID)
    if not OWNER_ID:
        log.warning("OWNER_ID not set - owner commands disabled")
    try:
        await db.groups.create_index("chat_id")
        await db.members.create_index([("chat_id", ASCENDING), ("user_id", ASCENDING)])
        await db.members.create_index("user_id")
        await db.users.create_index("user_id", unique=True)
        await db.warns.create_index([("chat_id", ASCENDING), ("user_id", ASCENDING)])
        await db.warns.create_index("created", expireAfterSeconds=7 * 86400)
        await db.verif.create_index("created", expireAfterSeconds=3600)
        await db.events.create_index("created", expireAfterSeconds=30 * 86400)
        await db.events.create_index([("type", ASCENDING), ("due", ASCENDING)])
        await db.messages.create_index([("chat_id", ASCENDING), ("ts", ASCENDING)])
        await db.messages.create_index("ts", expireAfterSeconds=86400)
        await db.link_approvals.create_index("created", expireAfterSeconds=600)
        await db.verified.create_index([("chat_id", ASCENDING), ("user_id", ASCENDING)])
        await db.vips.create_index([("chat_id", ASCENDING), ("user_id", ASCENDING)])
        await db.badwords.create_index("chat_id")
        for d in await db.sudolist.find({}).to_list(1000):
            SUDO.add(d["user_id"])
        for d in await db.gbans.find({}).to_list(100000):
            GBANS.add(d["user_id"])
        log.info("DB ready: %d sudo, %d gbans", len(SUDO), len(GBANS))
    except PyMongoError as e:
        log.error("DB init failed (running with defaults): %s", e)
    try:
        await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in BOT_COMMANDS])
    except TelegramAPIError as e:
        log.warning("set_my_commands failed: %s", e)
    for fn in (msg_flusher, sweeper, cleaner, reporter):
        spawn(fn())
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
