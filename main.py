"""GUARDIX - premium Telegram group protection bot (aiogram 3 + MongoDB)."""
import asyncio
import logging
import random
import re
import secrets
import sys
import time
from collections import defaultdict, deque
from datetime import datetime
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, Router, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (TelegramAPIError, TelegramBadRequest, TelegramForbiddenError,
                                TelegramRetryAfter)
from aiogram.types import (BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats,
                           BotCommandScopeAllChatAdministrators, BotCommandScopeChat,
                           CallbackQuery, ChatMemberUpdated, ChatPermissions, ErrorEvent,
                           InlineKeyboardButton, Message)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core import (Config, DB, FONTS, THEMES, esc, fmt_secs, fx, make_panel, mention, normalize,
                  parse_duration, utc)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("guardix")

cfg = Config()
db = DB(cfg)
bot: Bot = None  # set in main()
BOT_USER = cfg.BOT_USERNAME
START = time.time()
router = Router()
GROUPS = ("group", "supergroup")
ANON = 1087968824  # GroupAnonymousBot
TG_SERVICE = 777000  # linked-channel auto forwards
VERSION = "2.0"

NO_PERMS = ChatPermissions(
    can_send_messages=False, can_send_audios=False, can_send_documents=False, can_send_photos=False,
    can_send_videos=False, can_send_video_notes=False, can_send_voice_notes=False, can_send_polls=False,
    can_send_other_messages=False, can_add_web_page_previews=False, can_invite_users=False,
    can_pin_messages=False, can_change_info=False)
ALL_PERMS = ChatPermissions(
    can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True,
    can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
    can_send_other_messages=True, can_add_web_page_previews=True, can_invite_users=True)


# ═════════════════════════ GENERIC HELPERS ═════════════════════════
_bg = set()


def spawn(coro):
    t = asyncio.create_task(coro)
    _bg.add(t)
    t.add_done_callback(_bg.discard)
    return t


log_q: asyncio.Queue = asyncio.Queue(maxsize=1000)


def tolog(text, kb=None):
    if not cfg.LOG_GROUP_ID:
        return
    try:
        log_q.put_nowait((text, kb))
    except asyncio.QueueFull:
        pass


async def log_worker():
    while True:
        text, kb = await log_q.get()
        try:
            await bot.send_message(cfg.LOG_GROUP_ID, text[:4000], reply_markup=kb)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                log_q.put_nowait((text, kb))
            except asyncio.QueueFull:
                pass
        except Exception as e:
            log.warning("log send failed: %s", cfg.redact(e))
        await asyncio.sleep(0.4)


def tnow():
    return datetime.now(cfg.TZ).strftime("%d %b %Y, %I:%M:%S %p")


def mk(rows):
    """rows = [[(text, 'cb_data' | 'url:https://...'), ...], ...]"""
    b = InlineKeyboardBuilder()
    for row in rows:
        b.row(*[InlineKeyboardButton(text=t, url=d[4:]) if d.startswith("url:")
                else InlineKeyboardButton(text=t, callback_data=d) for t, d in row])
    return b.as_markup()


async def look(chat_id):
    if chat_id:
        g = await db.get_group(chat_id)
        return g["theme"], g["font"]
    m = await db.get_meta()
    return m.get("theme", 0), m.get("font", 0)


async def panel(chat_id, title, body):
    t, f = await look(chat_id)
    return make_panel(title, body, t, f)


async def say(m: Message, text, kb=None):
    try:
        return await m.reply(text, reply_markup=kb)
    except TelegramBadRequest:
        return await bot.send_message(m.chat.id, text, reply_markup=kb)


async def edit(cb: CallbackQuery, text, kb=None):
    msg = cb.message
    try:
        if getattr(msg, "photo", None):
            await msg.edit_caption(caption=text, reply_markup=kb)
        else:
            await msg.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        s = str(e).lower()
        if "not modified" in s:
            return
        if "too long" in s:
            try:
                await msg.delete()
            except TelegramAPIError:
                pass
            await bot.send_message(msg.chat.id, text, reply_markup=kb)
        else:
            log.debug("edit failed: %s", e)


async def _del_later(chat_id, mid, secs):
    await asyncio.sleep(secs)
    try:
        await bot.delete_message(chat_id, mid)
    except TelegramAPIError:
        pass


async def temp(chat_id, text, secs=8, kb=None):
    try:
        msg = await bot.send_message(chat_id, text, reply_markup=kb)
        spawn(_del_later(chat_id, msg.message_id, secs))
    except TelegramAPIError:
        pass


async def safe_delete(chat_id, mid):
    try:
        await bot.delete_message(chat_id, mid)
        return True
    except TelegramAPIError:
        return False


def tg_err(e):
    s = str(e).lower()
    if any(k in s for k in ("not enough rights", "chat_admin_required", "need administrator rights",
                            "have no rights", "can_restrict", "not enough permissions")):
        return "I don't have enough admin rights for this. Give me the needed permission."
    if "owner" in s and "chat" in s:
        return "Can't act on the group owner."
    if "administrator" in s:
        return "Can't act on an admin."
    if any(k in s for k in ("participant_id_invalid", "user_not_participant", "user not found", "member not found")):
        return "User not found in this chat."
    if "message to delete not found" in s or "can't be deleted" in s:
        return "Message can't be deleted (too old or already gone)."
    if "forbidden" in s:
        return "Telegram refused (bot blocked / removed from chat)."
    return "Telegram error: " + cfg.redact(getattr(e, "message", str(e)))[:120]


# ═════════════════════════ PREMIUM CARD HELPERS ═════════════════════════
def kv(icon, label, value):
    return f"{icon} <b>{label}</b> ➜ {value}"


_pic_cache = {}


async def dp_of(uid):
    """Largest current profile photo file_id of a user (None if hidden / none)."""
    c = _pic_cache.get(uid)
    if c and time.time() - c[0] < 1800:
        return c[1]
    fid = None
    try:
        ph = await bot.get_user_profile_photos(uid, limit=1)
        if ph.total_count and ph.photos:
            fid = ph.photos[0][-1].file_id
    except TelegramAPIError:
        pass
    if len(_pic_cache) > 3000:
        _pic_cache.clear()
    _pic_cache[uid] = (time.time(), fid)
    return fid


async def send_card(chat_id, uid, text, kb=None):
    """Send text with the user's profile photo (falls back to plain text)."""
    fid = await dp_of(uid)
    if fid:
        try:
            return await bot.send_photo(chat_id, fid, caption=text, reply_markup=kb)
        except TelegramBadRequest:
            pass
    return await bot.send_message(chat_id, text, reply_markup=kb)


async def say_card(m: Message, uid, text, kb=None):
    fid = await dp_of(uid)
    if fid:
        try:
            return await bot.send_photo(m.chat.id, fid, caption=text, reply_markup=kb, reply_to_message_id=m.message_id)
        except TelegramBadRequest:
            pass
    return await say(m, text, kb)


async def actor_name(chat_id, actor):
    if not actor:
        return "⚙️ System"
    if actor == bot.id:
        return "🤖 GUARDIX"
    return mention(actor, await name_of(chat_id, actor))


async def mod_card(chat_id, title, uid, name, actor, extra=(), reason="", raw=False):
    rows = [kv("👤", "User", mention(uid, name or uid)), kv("🆔", "ID", f"<code>{uid}</code>")]
    rows += [kv(i, l, v) for i, l, v in extra]
    if reason:
        rows.append(kv("📝", "Reason", esc(reason)))
    rows.append(kv("👮", "By", await actor_name(chat_id, actor)))
    rows.append(kv("🕒", "Time", tnow()))
    body = "\n".join(rows)
    return body if raw else await panel(chat_id, title, body)


# ═════════════════════════ ROLES / PERMISSIONS ═════════════════════════
def is_owner(uid):
    return uid == cfg.OWNER_ID


def is_sudo(uid):
    return uid == cfg.OWNER_ID or uid in cfg.SUDO_IDS or uid in db.sudo_ids


_admin_cache = {}
_bot_cache = {}


async def chat_admins(chat_id, force=False):
    c = _admin_cache.get(chat_id)
    if c and time.time() - c[0] < 45 and not force:
        return c[1]
    try:
        lst = await bot.get_chat_administrators(chat_id)
    except TelegramAPIError:
        return c[1] if c else {}
    d = {x.user.id: x for x in lst}
    _admin_cache[chat_id] = (time.time(), d)
    return d


def has_right(member, right):
    if member is None:
        return False
    if member.status == "creator":
        return True
    if member.status != "administrator":
        return False
    return True if right is None else bool(getattr(member, right, False))


async def god_active():
    return (await db.get_meta()).get("godmode", True)


async def actor_can(chat_id, uid, right=None):
    """Server-side check: Telegram admin rights (or Owner / Sudo with godmode)."""
    if is_owner(uid) or (is_sudo(uid) and await god_active()):
        return True
    return has_right((await chat_admins(chat_id)).get(uid), right)


async def is_group_creator(chat_id, uid):
    m = (await chat_admins(chat_id)).get(uid)
    return bool(m and m.status == "creator")


async def is_trusted(chat_id, uid):
    """Exempt from automatic protection: owner, sudo, admins, VIP."""
    if is_sudo(uid) or uid in (ANON, TG_SERVICE):
        return True
    if uid in await chat_admins(chat_id):
        return True
    return await db.is_vip(chat_id, uid)


async def bot_need(chat_id, right):
    """Return None if bot has the right, else a human error message."""
    c = _bot_cache.get(chat_id)
    if c and time.time() - c[0] < 30:
        mem = c[1]
    else:
        try:
            mem = await bot.get_chat_member(chat_id, bot.id)
        except TelegramAPIError as e:
            return tg_err(e)
        _bot_cache[chat_id] = (time.time(), mem)
    if mem.status != "administrator":
        return "I'm not an admin here. Make me admin first."
    if right and not getattr(mem, right, False):
        nice = right.replace("can_", "").replace("_", " ").title()
        return f"I'm missing the admin permission: <b>{nice}</b>."
    return None


_warned_missing = {}


async def notify_missing(chat_id, err):
    if time.time() - _warned_missing.get(chat_id, 0) > 600:
        _warned_missing[chat_id] = time.time()
        await temp(chat_id, f"⚠️ <b>GUARDIX needs permission</b>\n{err}", 20)


async def role_label(chat_id, uid):
    if is_owner(uid):
        return "👑 Bot Owner"
    if uid in cfg.SUDO_IDS or uid in db.sudo_ids:
        return "🔱 Sudo"
    if chat_id:
        a = (await chat_admins(chat_id)).get(uid)
        if a and a.status == "creator":
            return "👑 Group Owner"
        if a:
            return "👮 Admin"
        if await db.is_vip(chat_id, uid):
            return "💎 VIP"
    return "👤 Member"


async def default_perms(chat_id):
    try:
        c = await bot.get_chat(chat_id)
        if c.permissions:
            return c.permissions
    except TelegramAPIError:
        pass
    return ALL_PERMS


async def name_of(chat_id, uid):
    u = await db.get_user(uid)
    if u and u.get("first_name"):
        return u["first_name"]
    try:
        mem = await bot.get_chat_member(chat_id, uid)
        return mem.user.full_name
    except TelegramAPIError:
        return str(uid)


# ═════════════════════════ TARGET RESOLUTION ═════════════════════════
async def resolve_target(m: Message, args):
    args = (args or "").strip()
    r = m.reply_to_message
    if r and r.from_user and not r.forum_topic_created:
        return r.from_user.id, r.from_user.full_name, args
    for ent in (m.entities or []):
        if ent.type == "text_mention" and ent.user:
            txt = ent.extract_from(m.text or "")
            return ent.user.id, ent.user.full_name, args.replace(txt, "", 1).strip()
    parts = args.split(None, 1)
    if parts:
        tok, rest = parts[0], (parts[1] if len(parts) > 1 else "")
        uid = None
        if tok.lstrip("-").isdigit():
            uid = int(tok)
        elif tok.startswith("@"):
            d = await db.find_by_username(tok)
            uid = d["_id"] if d else None
        if uid:
            return uid, await name_of(m.chat.id, uid), rest
    return None, None, args


async def mod_guard(m: Message, right, bot_right=None):
    if m.chat.type not in GROUPS:
        await say(m, "❌ This command works in groups only.")
        return False
    if not m.from_user or m.from_user.id == ANON or m.sender_chat:
        await say(m, "⚠️ Anonymous admin detected. Turn off <i>Remain Anonymous</i> to use this command.")
        return False
    if not await actor_can(m.chat.id, m.from_user.id, right):
        await say(m, "⛔ You need admin rights"
                  + (f" (<b>{right.replace('can_', '').replace('_', ' ')}</b>)" if right else "") + " for this.")
        return False
    if bot_right:
        err = await bot_need(m.chat.id, bot_right)
        if err:
            await say(m, "❌ " + err)
            return False
    return True


async def target_check(chat_id, uid):
    if uid == bot.id:
        return "I won't act on myself 🙂"
    if is_sudo(uid):
        return "That user is a bot Owner/Sudo — protected."
    if uid in await chat_admins(chat_id):
        return "That user is a group admin — protected."
    return None


# ═════════════════════════ MODERATION SERVICE ═════════════════════════
async def svc_ban(chat_id, uid, name, actor, secs=None, reason="", raw=False):
    try:
        until = int(time.time() + max(secs, 60)) if secs else None
        await bot.ban_chat_member(chat_id, uid, until_date=until)
    except TelegramAPIError as e:
        await db.audit(chat_id, actor, uid, "ban", "failed", str(e))
        return False, "❌ " + tg_err(e)
    if secs:
        await db.schedule("unban", chat_id, uid, time.time() + max(secs, 60))
    else:
        await db.cancel_job("unban", chat_id, uid)
    await db.audit(chat_id, actor, uid, "ban" if not secs else "tban", "ok", reason)
    return True, await mod_card(chat_id, "BANNED", uid, name, actor,
                                [("🔨", "Action", "<b>Banned</b>"), ("⏱", "Duration", fmt_secs(secs) if secs else "Permanent")], reason, raw)


async def svc_mute(chat_id, uid, name, actor, secs=None, reason="", raw=False):
    try:
        until = int(time.time() + max(secs, 60)) if secs else None
        await bot.restrict_chat_member(chat_id, uid, permissions=NO_PERMS, until_date=until)
    except TelegramAPIError as e:
        await db.audit(chat_id, actor, uid, "mute", "failed", str(e))
        return False, "❌ " + tg_err(e)
    if secs:
        await db.schedule("unmute", chat_id, uid, time.time() + max(secs, 60))
    else:
        await db.cancel_job("unmute", chat_id, uid)
    await db.audit(chat_id, actor, uid, "mute" if not secs else "tmute", "ok", reason)
    return True, await mod_card(chat_id, "MUTED", uid, name, actor,
                                [("🔇", "Action", "<b>Muted</b>"), ("⏱", "Duration", fmt_secs(secs) if secs else "Until unmuted")], reason, raw)


async def svc_unmute(chat_id, uid, name, actor, manual=True, raw=False):
    try:
        await bot.restrict_chat_member(chat_id, uid, permissions=await default_perms(chat_id))
    except TelegramAPIError as e:
        return False, "❌ " + tg_err(e)
    await db.cancel_job("unmute", chat_id, uid)
    if manual:
        await db.sessions.update_many(
            {"chat_id": chat_id, "user_id": uid, "status": {"$in": ["pending", "captcha", "failed", "expired", "review"]}},
            {"$set": {"status": "verified", "manual_by": actor}})
    await db.audit(chat_id, actor, uid, "unmute", "ok")
    return True, await mod_card(chat_id, "UNMUTED", uid, name, actor, [("🔊", "Action", "<b>Unmuted</b>")], "", raw)


async def svc_unban(chat_id, uid, name, actor, raw=False):
    try:
        await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
    except TelegramAPIError as e:
        return False, "❌ " + tg_err(e)
    await db.cancel_job("unban", chat_id, uid)
    await db.audit(chat_id, actor, uid, "unban", "ok")
    return True, await mod_card(chat_id, "UNBANNED", uid, name, actor, [("♻️", "Action", "<b>Unbanned</b>")], "", raw)


async def svc_kick(chat_id, uid, name, actor, reason="", raw=False):
    try:
        await bot.ban_chat_member(chat_id, uid)
        await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
    except TelegramAPIError as e:
        return False, "❌ " + tg_err(e)
    await db.audit(chat_id, actor, uid, "kick", "ok", reason)
    return True, await mod_card(chat_id, "KICKED", uid, name, actor, [("👢", "Action", "<b>Kicked</b>")], reason, raw)


async def issue_warn(chat_id, uid, name, actor, reason):
    g = await db.get_group(chat_id)
    n = await db.add_warn(chat_id, uid, actor, reason or "no reason")
    limit = g["warn_limit"]
    await db.audit(chat_id, actor, uid, "warn", "ok", f"{n}/{limit} {reason}")
    bar = "🟥" * min(n, limit) + "⬜" * max(0, limit - n)
    body = await mod_card(chat_id, "WARNING", uid, name, actor,
                          [("⚠️", "Warnings", f"<b>{n}/{limit}</b>  {bar}")], reason, raw=True)
    if n >= limit:
        act = g["warn_action"]
        why = f"warn limit reached ({limit})"
        if act == "ban":
            ok, t = await svc_ban(chat_id, uid, name, actor, None, why, raw=True)
        elif act == "kick":
            ok, t = await svc_kick(chat_id, uid, name, actor, why, raw=True)
        else:
            ok, t = await svc_mute(chat_id, uid, name, actor, g["warn_mute_secs"], why, raw=True)
        if ok:
            await db.clear_warns(chat_id, uid)
            done = {"ban": "banned", "kick": "kicked"}.get(act, "muted")
            body += f"\n\n🚨 <b>Limit reached</b> ➜ user was <b>{done}</b>"
        else:
            body += "\n\n" + t
    return await panel(chat_id, "WARNING", body), n


# ═════════════════════════ LOG HELPERS ═════════════════════════
async def log_new_user(u, source="DM /start"):
    total = await db.users.estimated_document_count()
    tolog(
        f"🆕 <b>NEW BOT USER</b>\n━━━━━━━━━━━━━━━\n"
        f"👤 Name: {mention(u.id, u.full_name)}\n🆔 ID: <code>{u.id}</code>\n"
        f"🔗 Username: {('@' + esc(u.username)) if u.username else '—'}\n"
        f"🌐 Lang: {esc(u.language_code or '—')}\n💎 Premium: {'Yes' if getattr(u, 'is_premium', False) else 'No'}\n"
        f"📍 Source: {esc(source)}\n🕒 {tnow()}\n📊 Total users: <b>{total}</b>")


# ═════════════════════════ HELP ═════════════════════════
HELP = {
    "pub": ("🌐 Public", "/start — welcome, verify &amp; start\n/help — this menu\n/id — show IDs\n/profile — your profile with DP (also /me)\n/ping — latency"),
    "mem": ("👥 Group Members", "/info [user] — user info\n/rules — group rules\n/admins — admin list\n/viplist — VIP members\n"
            "/report (reply) or @admin — call staff\n/kickme — leave the group\n/top — all-time top chatters\n/active — today's active members\n"
            "/groupinfo — group card\n/stats — group stats"),
    "mod": ("🔨 Moderation (Admins)", "/ban /unban /kick — [reply|id|@user] [time] [reason]\n/mute /unmute — same format (10m, 2h, 1d)\n"
            "/warn /unwarn /warns /resetwarns\n/del (reply) · /purge (reply)\n/pin · /unpin · /unpinall\n/lock · /unlock — chat lock mode\n/promote · /demote\n"
            "/verify [user] — manual verify\n/unverified — pending list\n/verifyall — notify unverified members (no mute)\n/zombies — remove deleted accounts"),
    "prot": ("🛡 Protection (Admins)", "/protect — protection dashboard\n/addbadword word1 word2\n/delbadword word\n/allowlink add|remove|list domain\n"
             "/captcha on|off — verification switch"),
    "set": ("⚙️ Setup (Admins)", "/settings — full settings panel (themes &amp; fonts too)\n/setwelcome text — {name} {mention} {group} {id}\n/welcome on|off\n"
            "/setrules text\n/announce text\n/addvip [user] [time] · /delvip [user]\n/link — create invite link"),
    "sudo": ("👑 Owner + Sudo (DM)", "/broadcast · /gcast · /dmpromo · /callpm\n/logs · /sudolist · /stats · /uptime · /owner\n"
             "/addevent title | desc | when · /events · /event id · /delevent id\n/stop [off] · /godmode · /banall"),
    "own": ("🔒 Owner Only", "/addsudo · /delsudo\n/gban · /ungban\n/leavechat chat_id\n/clearevents\n/godmode on|off"),
}


def help_cats(uid, in_group_admin=True):
    cats = ["pub", "mem"]
    if in_group_admin:
        cats += ["mod", "prot", "set"]
    if is_sudo(uid):
        cats.append("sudo")
    if is_owner(uid):
        cats.append("own")
    return cats


def help_kb(uid, admin=True):
    names = {k: HELP[k][0] for k in help_cats(uid, admin)}
    rows, row = [], []
    for k, v in names.items():
        row.append((v, f"h:{k}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("🆘 Support", "url:" + cfg.SUPPORT_URL)])
    return mk(rows)


# ═════════════════════════ BASIC COMMANDS ═════════════════════════
def start_kb(uid, verified=False):
    rows = []
    if verified:
        rows.append([("👤 My Profile", "p:me"), ("📊 Status", "v:st")])
    else:
        rows.append([("🔐 Verify Account", "v:menu"), ("📊 Know Your Status", "v:st")])
        rows.append([("👤 My Profile", "p:me")])
    rows.append([("📖 Help", "h:home"), ("🆘 Support", "url:" + cfg.SUPPORT_URL)])
    if BOT_USER:
        rows.append([("➕ Add me to your group", f"url:https://t.me/{BOT_USER}?startgroup=true&admin=delete_messages+restrict_members+ban_users+pin_messages")])
    return mk(rows)


async def start_body(u):
    nver = await db.sessions.count_documents({"user_id": u.id, "status": "verified"})
    lines = [kv("👤", "Name", mention(u.id, u.full_name)), kv("🆔", "ID", f"<code>{u.id}</code>"),
             kv("🔗", "Username", ("@" + esc(u.username)) if u.username else "—"),
             kv("💎", "Telegram Premium", "Yes ✨" if getattr(u, "is_premium", False) else "No"),
             kv("🛡", "Status", "🟢 Verified" if nver else "⚪ Not verified yet")]
    body = ("🛡️ <b>Welcome to GUARDIX</b>\n<i>Premium group security assistant</i>\n\n" + "\n".join(lines)
            + "\n\n⚡ Choose an option below 👇")
    return body, bool(nver)


async def cmd_start(m: Message, args):
    if m.chat.type != "private":
        kb = mk([[("🚀 Open in DM", f"url:https://t.me/{BOT_USER}?start=hi")]])
        return await say(m, await panel(m.chat.id, "GUARDIX", "🛡️ Your group security assistant is <b>active</b>.\nOpen me in DM for verification &amp; status."), kb)
    u = m.from_user
    src = "DM /start" + (f" ({args[:20]})" if args and not args.startswith(("v_", "g_")) else "")
    new = await db.upsert_user(u, started=True, source=src)
    if new:
        await log_new_user(u, src)
    if args.startswith("v_"):
        return await begin_verification(m, args[2:], u.id)
    if args.startswith("g_") and args[2:].isdigit():
        return await soft_verify(m, -int(args[2:]))
    body, ver = await start_body(u)
    await say_card(m, u.id, await panel(0, "GUARDIX SECURITY", body), start_kb(u.id, ver))


async def cmd_help(m: Message, args):
    admin = True
    if m.chat.type in GROUPS and m.from_user:
        admin = await actor_can(m.chat.id, m.from_user.id)
    await say(m, await panel(m.chat.id if m.chat.type in GROUPS else 0, "HELP CENTER", "Choose a category below 👇"),
              help_kb(m.from_user.id, admin))


async def cmd_ping(m: Message, args):
    t = time.perf_counter()
    msg = await say(m, "🏓 …")
    ms = (time.perf_counter() - t) * 1000
    try:
        dbms = f"{await db.ping():.0f} ms"
    except Exception:
        dbms = "🔴 down"
    await msg.edit_text(f"🏓 <b>Pong!</b>\n⚡ Telegram: <b>{ms:.0f} ms</b>\n🗄 Database: <b>{dbms}</b>\n⏱ Uptime: <b>{fmt_secs(time.time() - START)}</b>")


async def cmd_id(m: Message, args):
    lines = [kv("🆔", "You", f"<code>{m.from_user.id}</code>"), kv("💬", "Chat", f"<code>{m.chat.id}</code>")]
    r = m.reply_to_message
    if r and r.from_user:
        lines.append(kv("↩️", "Replied user", f"<code>{r.from_user.id}</code>"))
    await say_card(m, m.from_user.id, await panel(m.chat.id if m.chat.type in GROUPS else 0, "IDENTITY", "\n".join(lines)))


async def profile_body(cid, uid, name):
    d = await db.get_user(uid) or {}
    lines = [kv("👤", "Name", mention(uid, name)), kv("🆔", "ID", f"<code>{uid}</code>"),
             kv("🔗", "Username", ("@" + esc(d["username"])) if d.get("username") else "—"),
             kv("💎", "Telegram Premium", "Yes ✨" if d.get("premium") else "No"),
             kv("🏷", "Role", await role_label(cid, uid))]
    if cid:
        sess = await db.sessions.find_one({"chat_id": cid, "user_id": uid}, sort=[("created", -1)])
        if sess:
            ver = STATUS_LABEL.get(sess.get("status"), sess.get("status"))
        elif await is_trusted(cid, uid):
            ver = "🟢 Trusted"
        else:
            ver = STATUS_LABEL[None]
        lines.append(kv("🔐", "Verification", ver))
        st = await db.stats.find_one({"_id": f"{cid}:{uid}"})
        g = await db.get_group(cid)
        lines.append(kv("💬", "Messages here", f"<b>{(st or {}).get('count', 0)}</b>"))
        lines.append(kv("⚠️", "Warnings", f"<b>{await db.count_warns(cid, uid)}/{g['warn_limit']}</b>"))
    else:
        nver = await db.sessions.count_documents({"user_id": uid, "status": "verified"})
        lines.append(kv("🔐", "Verification", f"🟢 Verified in <b>{nver}</b> group(s)" if nver else "⚪ Not verified yet"))
    if d.get("first_seen"):
        lines.append(kv("📅", "First seen", datetime.fromtimestamp(d["first_seen"], cfg.TZ).strftime("%d %b %Y")))
    lines.append(kv("🏘", "Groups seen", f"<b>{len(d.get('groups', []))}</b>"))
    return "\n".join(lines)


async def cmd_profile(m: Message, args):
    cid = m.chat.id if m.chat.type in GROUPS else 0
    uid, name = m.from_user.id, m.from_user.full_name
    if cid:
        t_uid, t_name, _ = await resolve_target(m, args)
        if t_uid:
            uid, name = t_uid, t_name
    await say_card(m, uid, await panel(cid, "USER PROFILE", await profile_body(cid, uid, name)), mk([[("❌ Close", "x")]]))


async def cb_profile(cb: CallbackQuery, p):
    u = cb.from_user
    await cb.answer()
    await edit(cb, await panel(0, "USER PROFILE", await profile_body(0, u.id, u.full_name)),
               mk([[("⬅️ Back", "h:start"), ("❌ Close", "x")]]))


async def need_group(m):
    if m.chat.type not in GROUPS:
        await say(m, "❌ This command works in groups only.")
        return False
    return True


async def cmd_info(m: Message, args):
    if not await need_group(m):
        return
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        uid, name = m.from_user.id, m.from_user.full_name
    cid = m.chat.id
    if uid != m.from_user.id and not await actor_can(cid, m.from_user.id):
        return await say(m, "⛔ Only admins can view other members' info.")
    d = await db.get_user(uid) or {}
    sess = await db.sessions.find_one({"chat_id": cid, "user_id": uid}, sort=[("created", -1)])
    lines = [f"👤 {mention(uid, name)}", f"🆔 <code>{uid}</code>",
             f"🔗 {('@' + esc(d['username'])) if d.get('username') else '—'}", f"🏷 Role: {await role_label(cid, uid)}",
             f"🔐 Verification: {STATUS_LABEL.get((sess or {}).get('status'), 'Not Started')}",
             f"⚠️ Warnings: {await db.count_warns(cid, uid)}"]
    try:
        mem = await bot.get_chat_member(cid, uid)
        lines.append(f"📌 Telegram status: {mem.status}")
    except TelegramAPIError:
        pass
    if await actor_can(cid, m.from_user.id) and uid in db.gban_ids:
        lines.append("🚫 Globally banned")
    await say_card(m, uid, await panel(cid, "USER INFO", "\n".join(lines)))


async def cmd_rules(m: Message, args):
    if not await need_group(m):
        return
    g = await db.get_group(m.chat.id)
    txt = esc(g["rules"]) if g["rules"] else "No rules set yet. Admins: use /setrules."
    await say(m, await panel(m.chat.id, "GROUP RULES", txt))


async def cmd_admins(m: Message, args):
    if not await need_group(m):
        return
    ad = await chat_admins(m.chat.id, force=True)
    lines = []
    for a in ad.values():
        if a.user.is_bot:
            continue
        lines.append(f"{'👑' if a.status == 'creator' else '👮'} {mention(a.user.id, a.user.full_name)}")
    await say(m, await panel(m.chat.id, "ADMINS", "\n".join(lines) or "No admins found."))


async def cmd_viplist(m: Message, args):
    if not await need_group(m):
        return
    vips = await db.list_vips(m.chat.id)
    lines = [f"💎 {mention(v['user_id'], await name_of(m.chat.id, v['user_id']))}"
             + (f" — {fmt_secs(v['until'] - time.time())} left" if v.get("until") else "") for v in vips]
    await say(m, await panel(m.chat.id, "VIP MEMBERS", "\n".join(lines) or "No VIPs yet. Admins: /addvip"))


async def cmd_addvip(m, args):
    if not await mod_guard(m, "can_promote_members"):
        return
    uid, name, rest = await resolve_target(m, args)
    if not uid:
        return await say(m, "Usage: /addvip [reply|id|@user] [time e.g. 7d]")
    secs = parse_duration(rest.split()[0]) if rest.split() else None
    await db.add_vip(m.chat.id, uid, m.from_user.id, time.time() + secs if secs else None)
    await db.audit(m.chat.id, m.from_user.id, uid, "addvip")
    body = "\n".join([kv("💎", "VIP", "<b>Activated</b>"), kv("👤", "User", mention(uid, name)), kv("🆔", "ID", f"<code>{uid}</code>"),
                      kv("⏳", "Duration", fmt_secs(secs) if secs else "Lifetime ♾️"),
                      kv("✨", "Perks", "Skips auto-protection · chat in lock mode"),
                      kv("👮", "Granted by", mention(m.from_user.id, m.from_user.full_name))])
    await say_card(m, uid, await panel(m.chat.id, "VIP ACCESS", body))


async def cmd_delvip(m, args):
    if not await mod_guard(m, "can_promote_members"):
        return
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        return await say(m, "Usage: /delvip [reply|id|@user]")
    ok = await db.del_vip(m.chat.id, uid)
    await db.audit(m.chat.id, m.from_user.id, uid, "delvip", "ok" if ok else "none")
    if not ok:
        return await say(m, "ℹ️ That user isn't a VIP.")
    body = "\n".join([kv("💎", "VIP", "<b>Removed</b>"), kv("👤", "User", mention(uid, name)),
                      kv("👮", "By", mention(m.from_user.id, m.from_user.full_name))])
    await say(m, await panel(m.chat.id, "VIP REMOVED", body))


async def cmd_kickme(m: Message, args):
    if not await need_group(m):
        return
    kb = mk([[("✅ Yes, kick me", f"k:me:{m.chat.id}:{m.from_user.id}"), ("❌ Cancel", "x")]])
    await say(m, "👢 Do you really want to leave this group?", kb)


async def cmd_top(m: Message, args):
    if not await need_group(m):
        return
    rows = await db.top(m.chat.id)
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = [f"{medals[i]} {esc(r.get('name'))} — <b>{r['count']}</b>" for i, r in enumerate(rows)]
    await say(m, await panel(m.chat.id, "TOP CHATTERS", "\n".join(lines) or "No data yet — start chatting!"))


async def cmd_stats(m: Message, args):
    if m.chat.type == "private":
        if not is_sudo(m.from_user.id):
            return await say(m, "⛔ Owner/Sudo only in DM.")
        users = await db.users.estimated_document_count()
        started = await db.users.count_documents({"started": True, "blocked": {"$ne": True}})
        grp = await db.groups.count_documents({"active": True})
        body = (f"👥 Users: <b>{users}</b>\n📩 DM-reachable: <b>{started}</b>\n🏘 Active groups: <b>{grp}</b>\n"
                f"🚫 Global bans: <b>{len(db.gban_ids)}</b>\n🔱 Sudo (DB): <b>{len(db.sudo_ids)}</b>\n⏱ Uptime: <b>{fmt_secs(time.time() - START)}</b>")
        return await say(m, await panel(0, "BOT STATISTICS", body))
    cid = m.chat.id
    try:
        count = await bot.get_chat_member_count(cid)
    except TelegramAPIError:
        count = "?"
    agg = await db.stats.aggregate([{"$match": {"chat_id": cid}}, {"$group": {"_id": None, "n": {"$sum": "$count"}}}]).to_list(1)
    g = await db.get_group(cid)
    body = (f"👥 Members: <b>{count}</b>\n💬 Messages tracked: <b>{agg[0]['n'] if agg else 0}</b>\n"
            f"⚠️ Warnings stored: <b>{await db.warns.count_documents({'chat_id': cid})}</b>\n"
            f"💎 VIPs: <b>{len(await db.list_vips(cid))}</b>\n🔒 Lock: {'🟢 ON' if g['locked'] else '🔴 OFF'}")
    await say(m, await panel(cid, "GROUP STATS", body))


# ═════════════════════════ MODERATION COMMANDS ═════════════════════════
def split_dur(rest):
    parts = rest.split(None, 1)
    if parts:
        d = parse_duration(parts[0])
        if d:
            return d, (parts[1] if len(parts) > 1 else "")
    return None, rest


async def _target_flow(m, args, right, usage):
    if not await mod_guard(m, right, right):
        return None
    uid, name, rest = await resolve_target(m, args)
    if not uid:
        await say(m, usage)
        return None
    err = await target_check(m.chat.id, uid)
    if err:
        await say(m, "🛡 " + err)
        return None
    return uid, name, rest


async def cmd_ban(m, args):
    r = await _target_flow(m, args, "can_restrict_members", "Usage: /ban [reply|id|@user] [time] [reason]")
    if not r:
        return
    uid, name, rest = r
    secs, reason = split_dur(rest)
    ok, t = await svc_ban(m.chat.id, uid, name, m.from_user.id, secs, reason)
    await say(m, t, mk([[("♻️ Unban", f"mod:ub:{m.chat.id}:{uid}")]]) if ok else None)


async def cmd_mute(m, args):
    r = await _target_flow(m, args, "can_restrict_members", "Usage: /mute [reply|id|@user] [time] [reason]")
    if not r:
        return
    uid, name, rest = r
    secs, reason = split_dur(rest)
    ok, t = await svc_mute(m.chat.id, uid, name, m.from_user.id, secs, reason)
    await say(m, t, mk([[("🔊 Unmute", f"mod:um:{m.chat.id}:{uid}")]]) if ok else None)


async def cmd_kick(m, args):
    r = await _target_flow(m, args, "can_restrict_members", "Usage: /kick [reply|id|@user] [reason]")
    if r:
        ok, t = await svc_kick(m.chat.id, r[0], r[1], m.from_user.id, r[2])
        await say(m, t)


async def cmd_unban(m, args):
    if not await mod_guard(m, "can_restrict_members", "can_restrict_members"):
        return
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        return await say(m, "Usage: /unban [reply|id|@user]")
    await say(m, (await svc_unban(m.chat.id, uid, name, m.from_user.id))[1])


async def cmd_unmute(m, args):
    if not await mod_guard(m, "can_restrict_members", "can_restrict_members"):
        return
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        return await say(m, "Usage: /unmute [reply|id|@user]")
    await say(m, (await svc_unmute(m.chat.id, uid, name, m.from_user.id))[1])


async def cmd_warn(m, args):
    if not await mod_guard(m, "can_restrict_members"):
        return
    uid, name, reason = await resolve_target(m, args)
    if not uid:
        return await say(m, "Usage: /warn [reply|id|@user] [reason]")
    err = await target_check(m.chat.id, uid)
    if err:
        return await say(m, "🛡 " + err)
    text, n = await issue_warn(m.chat.id, uid, name, m.from_user.id, reason)
    await say(m, text, mk([[("🗑 Remove warn", f"w:rm:{m.chat.id}:{uid}"), ("♻️ Reset", f"w:rs:{m.chat.id}:{uid}")]]))


async def cmd_warns(m, args):
    if not await need_group(m):
        return
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        uid, name = m.from_user.id, m.from_user.full_name
    if uid != m.from_user.id and not await actor_can(m.chat.id, m.from_user.id):
        return await say(m, "⛔ Admins only.")
    g = await db.get_group(m.chat.id)
    ws = await db.list_warns(m.chat.id, uid)
    lines = [f"{i + 1}. {esc(w['reason'])} — <i>{datetime.fromtimestamp(w['ts'], cfg.TZ).strftime('%d %b')}</i>" for i, w in enumerate(ws)]
    body = (kv("👤", "User", mention(uid, name)) + "\n" + kv("⚠️", "Warnings", f"<b>{await db.count_warns(m.chat.id, uid)}/{g['warn_limit']}</b>")
            + "\n\n" + ("\n".join(lines) or "No warnings 🎉"))
    await say_card(m, uid, await panel(m.chat.id, "WARNINGS", body))


async def cmd_resetwarns(m, args):
    if not await mod_guard(m, "can_restrict_members"):
        return
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        return await say(m, "Usage: /resetwarns [reply|id|@user]")
    n = await db.clear_warns(m.chat.id, uid)
    await db.audit(m.chat.id, m.from_user.id, uid, "resetwarns", "ok", n)
    body = "\n".join([kv("♻️", "Cleared", f"<b>{n}</b> warning(s)"), kv("👤", "User", mention(uid, name)),
                      kv("👮", "By", mention(m.from_user.id, m.from_user.full_name))])
    await say(m, await panel(m.chat.id, "WARNS RESET", body))


async def cmd_del(m, args):
    if not await mod_guard(m, "can_delete_messages", "can_delete_messages"):
        return
    if not m.reply_to_message:
        return await say(m, "Reply to the message you want to delete.")
    ok = await safe_delete(m.chat.id, m.reply_to_message.message_id)
    await safe_delete(m.chat.id, m.message_id)
    if ok:
        await db.audit(m.chat.id, m.from_user.id, m.reply_to_message.from_user.id if m.reply_to_message.from_user else 0, "del")
    else:
        await temp(m.chat.id, "❌ Couldn't delete (too old or no permission).")


async def cmd_purge(m, args):
    if not await mod_guard(m, "can_delete_messages", "can_delete_messages"):
        return
    if not m.reply_to_message:
        return await say(m, "Reply to the first message to purge from.")
    ids = list(range(m.reply_to_message.message_id, m.message_id + 1))[:1000]
    done = 0
    for i in range(0, len(ids), 100):
        try:
            await bot.delete_messages(m.chat.id, ids[i:i + 100])
            done += len(ids[i:i + 100])
        except TelegramAPIError:
            pass
    await db.audit(m.chat.id, m.from_user.id, 0, "purge", "ok", f"{done} ids attempted")
    await temp(m.chat.id, f"🧹 Purge finished (up to <b>{done}</b> messages; Telegram skips old/missing ones).", 6)


async def cmd_pin(m, args):
    if not await mod_guard(m, "can_pin_messages", "can_pin_messages"):
        return
    if not m.reply_to_message:
        return await say(m, "Reply to the message to pin.")
    try:
        await bot.pin_chat_message(m.chat.id, m.reply_to_message.message_id)
        await db.audit(m.chat.id, m.from_user.id, 0, "pin")
        await say(m, await panel(m.chat.id, "PINNED", "📌 Message pinned by " + mention(m.from_user.id, m.from_user.full_name)))
    except TelegramAPIError as e:
        await say(m, "❌ " + tg_err(e))


async def cmd_unpin(m, args):
    if not await mod_guard(m, "can_pin_messages", "can_pin_messages"):
        return
    try:
        if m.reply_to_message:
            await bot.unpin_chat_message(m.chat.id, m.reply_to_message.message_id)
        else:
            await bot.unpin_chat_message(m.chat.id)
        await say(m, await panel(m.chat.id, "UNPINNED", "📍 Unpinned by " + mention(m.from_user.id, m.from_user.full_name)))
    except TelegramAPIError as e:
        await say(m, "❌ " + tg_err(e))


async def cmd_promote(m, args):
    if not await mod_guard(m, "can_promote_members", "can_promote_members"):
        return
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        return await say(m, "Usage: /promote [reply|id|@user]")
    try:
        await bot.promote_chat_member(m.chat.id, uid, can_delete_messages=True, can_restrict_members=True,
                                      can_pin_messages=True, can_invite_users=True, can_manage_video_chats=True)
    except TelegramAPIError as e:
        return await say(m, "❌ " + tg_err(e))
    _admin_cache.pop(m.chat.id, None)
    await db.audit(m.chat.id, m.from_user.id, uid, "promote")
    body = "\n".join([kv("⬆️", "Action", "<b>Promoted to admin</b>"), kv("👤", "User", mention(uid, name)),
                      kv("🔑", "Rights", "delete · restrict · pin · invite"), kv("👮", "By", mention(m.from_user.id, m.from_user.full_name))])
    await say_card(m, uid, await panel(m.chat.id, "PROMOTED", body))


async def cmd_demote(m, args):
    if not await mod_guard(m, "can_promote_members", "can_promote_members"):
        return
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        return await say(m, "Usage: /demote [reply|id|@user]")
    if is_sudo(uid):
        return await say(m, "🛡 Protected user.")
    try:
        await bot.promote_chat_member(m.chat.id, uid, can_delete_messages=False, can_restrict_members=False,
                                      can_pin_messages=False, can_invite_users=False, can_manage_video_chats=False,
                                      can_change_info=False, can_promote_members=False, can_manage_chat=False)
    except TelegramAPIError as e:
        return await say(m, "❌ " + tg_err(e))
    _admin_cache.pop(m.chat.id, None)
    await db.audit(m.chat.id, m.from_user.id, uid, "demote")
    body = "\n".join([kv("⬇️", "Action", "<b>Demoted</b>"), kv("👤", "User", mention(uid, name)),
                      kv("👮", "By", mention(m.from_user.id, m.from_user.full_name))])
    await say_card(m, uid, await panel(m.chat.id, "DEMOTED", body))


# ═════════════════════════ EXTRA COMMANDS ═════════════════════════
async def cmd_unwarn(m, args):
    if not await mod_guard(m, "can_restrict_members"):
        return
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        return await say(m, "Usage: /unwarn [reply|id|@user]")
    ok = await db.remove_last_warn(m.chat.id, uid)
    await db.audit(m.chat.id, m.from_user.id, uid, "unwarn", "ok" if ok else "none")
    g = await db.get_group(m.chat.id)
    body = "\n".join([kv("🗑", "Action", "Last warning removed" if ok else "No warnings to remove"), kv("👤", "User", mention(uid, name)),
                      kv("⚠️", "Warnings", f"<b>{await db.count_warns(m.chat.id, uid)}/{g['warn_limit']}</b>"),
                      kv("👮", "By", mention(m.from_user.id, m.from_user.full_name))])
    await say(m, await panel(m.chat.id, "WARN REMOVED", body))


async def cmd_unpinall(m, args):
    if not await mod_guard(m, "can_pin_messages", "can_pin_messages"):
        return
    try:
        await bot.unpin_all_chat_messages(m.chat.id)
        await say(m, await panel(m.chat.id, "UNPINNED", "📍 All pinned messages removed."))
    except TelegramAPIError as e:
        await say(m, "❌ " + tg_err(e))


async def cmd_link(m, args):
    if not await mod_guard(m, "can_invite_users", "can_invite_users"):
        return
    try:
        lk = await bot.create_chat_invite_link(m.chat.id, name="GUARDIX")
    except TelegramAPIError as e:
        return await say(m, "❌ " + tg_err(e))
    await say(m, await panel(m.chat.id, "INVITE LINK", f"🔗 {esc(lk.invite_link)}"))


async def cmd_active(m, args):
    if not await need_group(m):
        return
    rows = await db.top_today(m.chat.id, 10)
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = [f"{medals[i]} {mention(r['user_id'], r.get('name'))} — <b>{r['count']}</b> msgs" for i, r in enumerate(rows)]
    await say(m, await panel(m.chat.id, "ACTIVE TODAY", "\n".join(lines) or "No activity tracked yet today — start chatting! 💬"))


async def cmd_groupinfo(m, args):
    if not await need_group(m):
        return
    cid = m.chat.id
    try:
        chat = await bot.get_chat(cid)
        count = await bot.get_chat_member_count(cid)
    except TelegramAPIError as e:
        return await say(m, "❌ " + tg_err(e))
    admins = await chat_admins(cid, force=True)
    g = await db.get_group(cid)
    body = "\n".join([kv("🏘", "Name", f"<b>{esc(chat.title)}</b>"), kv("🆔", "ID", f"<code>{cid}</code>"),
                      kv("🔗", "Link", ("@" + esc(chat.username)) if chat.username else "Private"),
                      kv("👥", "Members", f"<b>{count}</b>"),
                      kv("👮", "Admins", f"<b>{len([a for a in admins.values() if not a.user.is_bot])}</b>"),
                      kv("🔐", "Verification", f"{onoff(g['captcha'])} ({g['verify_mode']})"),
                      kv("🔒", "Lock", onoff(g["locked"])), kv("🎨", "Theme", THEMES[g["theme"]]["name"])])
    text = await panel(cid, "GROUP INFO", body)
    if chat.photo:
        try:
            return await bot.send_photo(cid, chat.photo.big_file_id, caption=text, reply_to_message_id=m.message_id)
        except TelegramBadRequest:
            pass
    await say(m, text)


async def _toggle_cmd(m, args, key, label):
    if not await mod_guard(m, "can_change_info"):
        return
    a = (args or "").strip().lower()
    g = await db.get_group(m.chat.id)
    if a not in ("on", "off"):
        return await say(m, f"{label} is {onoff(g[key])} <b>{'ON' if g[key] else 'OFF'}</b>\nUsage: /{key} on|off")
    if key == "captcha" and a == "on":
        err = await bot_need(m.chat.id, "can_restrict_members")
        if err:
            return await say(m, "❌ " + err)
    await db.set_group(m.chat.id, **{key: a == "on"})
    await db.audit(m.chat.id, m.from_user.id, 0, "setting", "ok", f"{key}={a}")
    await say(m, await panel(m.chat.id, label.upper(), f"{onoff(a == 'on')} {label} is now <b>{a.upper()}</b>"))


async def cmd_welcome(m, args):
    await _toggle_cmd(m, args, "welcome", "Welcome message")


async def cmd_captcha(m, args):
    await _toggle_cmd(m, args, "captcha", "Verification")


async def cmd_verify(m, args):
    if not await mod_guard(m, "can_restrict_members", "can_restrict_members"):
        return
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        return await say(m, "Usage: /verify [reply|id|@user]")
    d = await db.sessions.find_one({"chat_id": m.chat.id, "user_id": uid}, sort=[("created", -1)])
    ok, t = await svc_unmute(m.chat.id, uid, name, m.from_user.id, manual=True)
    if ok and d:
        try:
            mem = await bot.get_chat_member(m.chat.id, uid)
            await settle_group_msgs(d["_id"], mem.user)
        except TelegramAPIError:
            pass
    await say_card(m, uid, t) if ok else await say(m, t)


async def cmd_unverified(m, args):
    if not await mod_guard(m, "can_restrict_members"):
        return
    now = time.time()
    seen, lines = set(), []
    async for d in db.sessions.find({"chat_id": m.chat.id, "soft": {"$ne": True},
                                     "status": {"$in": ["pending", "captcha", "failed", "expired", "review"]}}).sort("created", -1).limit(40):
        if d["user_id"] in seen:
            continue
        seen.add(d["user_id"])
        lines.append(f"⏳ {mention(d['user_id'], await name_of(m.chat.id, d['user_id']))} — {fmt_secs(now - d['created'])} · 🔔 {d.get('remind_n', 0)}")
        if len(lines) >= 20:
            break
    await say(m, await panel(m.chat.id, "UNVERIFIED", "\n".join(lines) + "\n\n/verify [user] · /verifyall" if lines else "✅ Nobody is waiting for verification."))


_last_notice = {}


async def cmd_verifyall(m, args):
    """Notify unverified / random known members with a Verify button. Nobody is muted."""
    if not await mod_guard(m, "can_restrict_members"):
        return
    cid = m.chat.id
    if time.time() - _last_notice.get(cid, 0) < 300:
        return await say(m, "⏳ A verification notice was sent recently. Try again in a few minutes.")
    admins = await chat_admins(cid)
    verified = {d["user_id"] async for d in db.sessions.find({"chat_id": cid, "status": "verified"}, {"user_id": 1})}
    pend = list(dict.fromkeys([d["user_id"] async for d in db.sessions.find(
        {"chat_id": cid, "soft": {"$ne": True}, "status": {"$in": ["pending", "captcha", "failed", "expired", "review"]}}, {"user_id": 1})]))
    pool = [u for u in await db.tracked_ids(cid, 3000)
            if u not in admins and u not in verified and u not in pend and not is_sudo(u) and u != bot.id]
    picks = pend[:6] + random.sample(pool, min(len(pool), 14))
    final = []
    for u in picks:
        if len(final) >= 10:
            break
        if await db.is_vip(cid, u):
            continue
        final.append(u)
    if not final:
        return await say(m, "✅ Everyone I know in this group is verified!")
    _last_notice[cid] = time.time()
    names = [f"• {mention(u, await name_of(cid, u))}" for u in final]
    body = ("⚠️ <b>Verification needed</b>\n\nThese members haven't verified yet:\n" + "\n".join(names)
            + "\n\n🔐 Tap <b>Verify Now</b> — one tap. Nobody gets muted. ✅")
    link = f"https://t.me/{BOT_USER}?start=g_{abs(cid)}"
    await bot.send_message(cid, await panel(cid, "VERIFY NOTICE", body), reply_markup=mk([[("✅ Verify Now", "url:" + link)]]))
    await safe_delete(cid, m.message_id)


# ═════════════════════════ LOCK / UNLOCK ═════════════════════════
async def lock_authorized(chat_id, uid, g):
    if is_sudo(uid) or await is_group_creator(chat_id, uid):
        return True
    if g["lock_vip"] and await db.is_vip(chat_id, uid):
        return True
    return has_right((await chat_admins(chat_id)).get(uid), "can_delete_messages")


async def lock_exempt(chat_id, uid, g):
    if is_sudo(uid) or uid in (ANON, TG_SERVICE) or await is_group_creator(chat_id, uid):
        return True
    if g["lock_admins"] and uid in await chat_admins(chat_id):
        return True
    return bool(g["lock_vip"] and await db.is_vip(chat_id, uid))


async def _lock_toggle(m, lock):
    if not await need_group(m):
        return
    uid = m.from_user.id
    if uid == ANON or m.sender_chat:
        return await say(m, "⚠️ Turn off anonymous mode to use this.")
    g = await db.get_group(m.chat.id)
    if not await lock_authorized(m.chat.id, uid, g):
        return await say(m, "⛔ Only Owner, Sudo, Group Owner, authorized VIP, or admins with Delete rights can do this.")
    if lock:
        err = await bot_need(m.chat.id, "can_delete_messages")
        if err:
            return await say(m, f"❌ Lock mode can't work reliably.\n{err}")
    if g["locked"] == lock:
        return await say(m, f"ℹ️ Lock mode is already {'ON 🔒' if lock else 'OFF 🔓'}.")
    await db.set_group(m.chat.id, locked=lock, locked_by=uid, locked_at=time.time())
    await db.audit(m.chat.id, uid, 0, "lock" if lock else "unlock")
    tolog(f"{'🔒' if lock else '🔓'} <b>Lock {'ON' if lock else 'OFF'}</b>\nGroup: {esc(m.chat.title)} (<code>{m.chat.id}</code>)\nBy: {mention(uid, m.from_user.full_name)}\n🕒 {tnow()}")
    if lock:
        body = ("🔒 <b>Lock mode ON</b>\nOnly Owner, Sudo, Group Owner and VIP can chat.\n"
                "Everyone else's messages will be deleted.\nUse /unlock to reopen.")
    else:
        body = "🔓 <b>Lock mode OFF</b>\nEveryone can chat again."
    await say(m, await panel(m.chat.id, "LOCK MODE", body))


async def cmd_lock(m, args):
    await _lock_toggle(m, True)


async def cmd_unlock(m, args):
    await _lock_toggle(m, False)


# ═════════════════════════ RULES / WELCOME / ANNOUNCE / BADWORDS ═════════════════════════
def _text_arg(m, args):
    t = (args or "").strip()
    if not t and m.reply_to_message:
        t = (m.reply_to_message.text or m.reply_to_message.caption or "").strip()
    return t


async def cmd_setrules(m, args):
    if not await mod_guard(m, "can_change_info"):
        return
    t = _text_arg(m, args)
    if not t:
        return await say(m, "Usage: /setrules your rules text (or reply to a message)")
    await db.set_group(m.chat.id, rules=t[:3000])
    await db.audit(m.chat.id, m.from_user.id, 0, "setrules")
    await say(m, "✅ Rules saved. Members can read them with /rules.")


async def cmd_setwelcome(m, args):
    if not await mod_guard(m, "can_change_info"):
        return
    t = _text_arg(m, args)
    if not t:
        return await say(m, "Usage: /setwelcome text\nPlaceholders: {name} {mention} {group} {id}\n/setwelcome reset → default")
    if t.lower() == "reset":
        await db.set_group(m.chat.id, welcome_text=None)
        return await say(m, "✅ Welcome text reset to default.")
    await db.set_group(m.chat.id, welcome_text=t[:1500])
    await db.audit(m.chat.id, m.from_user.id, 0, "setwelcome")
    await say(m, "✅ Welcome saved. Preview:\n\n" + render_welcome(t, m.from_user, m.chat))


async def cmd_announce(m, args):
    if not await mod_guard(m, "can_pin_messages"):
        return
    t = _text_arg(m, args)
    if not t:
        return await say(m, "Usage: /announce your message")
    await safe_delete(m.chat.id, m.message_id)
    await db.audit(m.chat.id, m.from_user.id, 0, "announce")
    await bot.send_message(m.chat.id, await panel(m.chat.id, "ANNOUNCEMENT", esc(t[:3500])))


def _words(args):
    parts = re.split(r"[,\n]+", args) if ("," in args or "\n" in args) else args.split()
    return [normalize(p) for p in parts if 1 <= len(p.strip()) <= 40][:50]


async def cmd_addbadword(m, args):
    if not await mod_guard(m, "can_change_info"):
        return
    w = _words(args or "")
    if not w:
        return await say(m, "Usage: /addbadword word1 word2  (or comma-separated phrases)")
    g = await db.get_group(m.chat.id)
    if len(g["badwords"]) + len(w) > 500:
        return await say(m, "❌ Limit of 500 words reached.")
    await db.add_to_list(m.chat.id, "badwords", w)
    await db.audit(m.chat.id, m.from_user.id, 0, "addbadword", "ok", len(w))
    await safe_delete(m.chat.id, m.message_id)
    await temp(m.chat.id, f"✅ Added <b>{len(w)}</b> word(s) to the filter.")


async def cmd_delbadword(m, args):
    if not await mod_guard(m, "can_change_info"):
        return
    w = _words(args or "")
    if not w:
        return await say(m, "Usage: /delbadword word")
    await db.del_from_list(m.chat.id, "badwords", w)
    await db.audit(m.chat.id, m.from_user.id, 0, "delbadword", "ok", len(w))
    await say(m, f"✅ Removed <b>{len(w)}</b> word(s).")


async def cmd_allowlink(m, args):
    if not await mod_guard(m, "can_change_info"):
        return
    p = (args or "").split()
    g = await db.get_group(m.chat.id)
    if not p or p[0] == "list":
        return await say(m, "🔗 Allowed domains:\n" + ("\n".join("• " + esc(d) for d in g["allowed_domains"]) or "none"))
    if len(p) < 2 or p[0] not in ("add", "remove"):
        return await say(m, "Usage: /allowlink add|remove|list domain.com")
    dom = p[1].lower().replace("https://", "").replace("http://", "").split("/")[0].lstrip("www.")
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", dom):
        return await say(m, "❌ Invalid domain.")
    if p[0] == "add":
        await db.add_to_list(m.chat.id, "allowed_domains", [dom])
    else:
        await db.del_from_list(m.chat.id, "allowed_domains", [dom])
    await db.audit(m.chat.id, m.from_user.id, 0, "allowlink_" + p[0], "ok", dom)
    await say(m, f"✅ <code>{esc(dom)}</code> {'allowed' if p[0] == 'add' else 'removed'}.")


# ═════════════════════════ SETTINGS PANELS ═════════════════════════
# key: (kind, label, step, min, max, divisor) | ('t', label) | ('c', label, options)
SPEC = {
    "antispam": ("t", "Anti-Spam"), "antiflood": ("t", "Anti-Flood"), "antiraid": ("t", "Anti-Raid"),
    "links": ("t", "Link Filter"), "badwords_on": ("t", "Bad-word Filter"), "safe_mode": ("t", "Safe Mode (dry-run)"),
    "captcha": ("t", "CAPTCHA"), "welcome": ("t", "Welcome message"), "del_service": ("t", "Hide join/leave notices"),
    "locked": ("t", "Lock Mode"), "lock_vip": ("t", "VIP can chat in lock"), "lock_admins": ("t", "Admins can chat in lock"),
    "flood_limit": ("n", "Flood limit (msgs)", 1, 2, 30, 1), "raid_limit": ("n", "Raid joins", 1, 3, 60, 1),
    "captcha_attempts": ("n", "CAPTCHA tries (math mode)", 1, 1, 10, 1), "captcha_timeout": ("n", "Verify window (hours)", 3600, 3600, 172800, 3600),
    "remind_every": ("n", "Reminder gap (min)", 60, 60, 3600, 60), "remind_max": ("n", "Reminders (max)", 1, 1, 10, 1),
    "warn_limit": ("n", "Warn limit", 1, 1, 10, 1), "warn_mute_secs": ("n", "Warn mute (min)", 600, 60, 86400, 60),
    "warn_action": ("c", "Warn action", ["mute", "ban", "kick"]),
    "captcha_expire_action": ("c", "On expiry", ["none", "kick"]),
    "verify_mode": ("c", "Verify mode", ["tap", "math"]),
}
SECTIONS = {
    "prot": ("🛡 Protection", ["antispam", "antiflood", "antiraid", "links", "badwords_on", "safe_mode", "flood_limit", "raid_limit"]),
    "cap": ("🔐 Verification", ["captcha", "verify_mode", "remind_every", "remind_max", "captcha_timeout", "captcha_attempts", "captcha_expire_action"]),
    "lock": ("🔒 Lock Mode", ["locked", "lock_vip", "lock_admins"]),
    "warn": ("⚠️ Warnings", ["warn_limit", "warn_action", "warn_mute_secs"]),
    "wel": ("👋 Welcome", ["welcome", "del_service"]),
}


def onoff(v):
    return "🟢" if v else "🔴"


async def render_home(cid):
    g = await db.get_group(cid)
    body = (f"⚡ <b>Protection Dashboard</b>\n\n🛡️ Anti-Spam: {onoff(g['antispam'])}\n🌊 Anti-Flood: {onoff(g['antiflood'])}\n"
            f"🚨 Anti-Raid: {onoff(g['antiraid'])}\n🔗 Link Filter: {onoff(g['links'])}\n🔐 CAPTCHA: {onoff(g['captcha'])}\n"
            f"🔒 Lock: {onoff(g['locked'])}\n🧪 Safe Mode: {onoff(g['safe_mode'])}\n\n"
            f"🎨 Theme: <b>{THEMES[g['theme']]['name']}</b> · 🔤 Font: <b>{FONTS[g['font']][0]}</b>\n\nSelect a module below.")
    kb = mk([[("🛡 Protection", f"s:o:{cid}:prot"), ("🔐 CAPTCHA", f"s:o:{cid}:cap")],
             [("🔒 Lock", f"s:o:{cid}:lock"), ("⚠️ Warnings", f"s:o:{cid}:warn")],
             [("👋 Welcome", f"s:o:{cid}:wel"), ("🎨 Themes & Fonts", f"s:o:{cid}:look")],
             [("♻️ Reset", f"s:rs:{cid}"), ("❌ Close", "x")]])
    return await panel(cid, "GUARDIX SETTINGS", body), kb


async def render_section(cid, sec):
    g = await db.get_group(cid)
    title, keys = SECTIONS[sec]
    lines, rows = [], []
    for k in keys:
        sp = SPEC[k]
        v = g[k]
        if sp[0] == "t":
            lines.append(f"{onoff(v)} {sp[1]}")
            rows.append([(f"{onoff(v)} {sp[1]}", f"s:t:{cid}:{k}")])
        elif sp[0] == "n":
            val = max(v // sp[5], 1)
            lines.append(f"🔢 {sp[1]}: <b>{val}</b>")
            rows.append([("➖", f"s:n:{cid}:{k}:-1"), (f"{sp[1]}: {val}", "noop"), ("➕", f"s:n:{cid}:{k}:1")])
        else:
            lines.append(f"🔁 {sp[1]}: <b>{v}</b>")
            rows.append([(f"🔁 {sp[1]}: {v}", f"s:c:{cid}:{k}")])
    if sec == "prot":
        lines.append(f"\n📝 Bad words: <b>{len(g['badwords'])}</b> · 🔗 Allowed domains: <b>{len(g['allowed_domains'])}</b>")
        lines.append("Manage with /addbadword /delbadword /allowlink")
    rows.append([("⬅️ Back", f"s:home:{cid}"), ("❌ Close", "x")])
    return await panel(cid, title.split(" ", 1)[1], "\n".join(lines)), mk(rows)


async def render_look(cid):
    t, f = await look(cid)
    rows = [[(("✅ " if i == t else "") + f"{th['icon']} {th['name']}", f"s:th:{cid}:{i}")] for i, th in enumerate(THEMES)]
    row = []
    for i, (nm, _) in enumerate(FONTS):
        row.append((("✅ " if i == f else "") + fx("Abc", i) + " " + nm.split()[0], f"s:ft:{cid}:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    rows.append([("⬅️ Back", f"s:home:{cid}" if cid else "x"), ("❌ Close", "x")])
    body = f"🎨 Theme: <b>{THEMES[t]['name']}</b>\n🔤 Font: <b>{FONTS[f][0]}</b>\n\nPreview: {fx('Guardix Security 2025', f)}\n\nTap to change 👇"
    return await panel(cid, "THEMES & FONTS", body), mk(rows)


async def cmd_settings(m, args):
    if m.chat.type == "private":
        if not is_sudo(m.from_user.id):
            return await say(m, "⚙️ Settings are per-group. Run /settings inside your group (admins only).")
        text, kb = await render_look(0)
        return await say(m, text, kb)
    if not await mod_guard(m, "can_change_info"):
        return
    text, kb = await render_home(m.chat.id)
    await say(m, text, kb)


async def cmd_protect(m, args):
    if not await mod_guard(m, "can_change_info"):
        return
    text, kb = await render_section(m.chat.id, "prot")
    await say(m, text, kb)


async def settings_auth(cb: CallbackQuery, cid):
    if cid == 0:
        return cb.message.chat.type == "private" and is_sudo(cb.from_user.id)
    if cb.message.chat.id != cid:
        return False
    return await actor_can(cid, cb.from_user.id, "can_change_info")


async def cb_settings(cb: CallbackQuery, p):
    act = p[1]
    if act == "noop":
        return await cb.answer()
    cid = int(p[2])
    if not await settings_auth(cb, cid):
        return await cb.answer("⛔ Admins with 'Change group info' only.", show_alert=True)
    if act == "home":
        text, kb = await render_home(cid)
    elif act == "o":
        if p[3] == "look":
            text, kb = await render_look(cid)
        elif p[3] in SECTIONS and cid != 0:
            text, kb = await render_section(cid, p[3])
        else:
            return await cb.answer("Invalid section", show_alert=True)
    elif act in ("t", "n", "c"):
        key = p[3]
        sp = SPEC.get(key)
        if not sp or cid == 0:
            return await cb.answer("Invalid setting", show_alert=True)
        g = await db.get_group(cid)
        if act == "t" and sp[0] == "t":
            nv = not g[key]
            if key == "locked" and nv:
                err = await bot_need(cid, "can_delete_messages")
                if err:
                    return await cb.answer(re.sub("<.*?>", "", err), show_alert=True)
            if key == "captcha" and nv:
                err = await bot_need(cid, "can_restrict_members")
                if err:
                    return await cb.answer(re.sub("<.*?>", "", err), show_alert=True)
            await db.set_group(cid, **{key: nv})
        elif act == "n" and sp[0] == "n":
            nv = max(sp[3], min(sp[4], g[key] + sp[2] * int(p[4])))
            await db.set_group(cid, **{key: nv})
        elif act == "c" and sp[0] == "c":
            opts = sp[2]
            await db.set_group(cid, **{key: opts[(opts.index(g[key]) + 1) % len(opts)] if g[key] in opts else opts[0]})
        else:
            return await cb.answer("Invalid", show_alert=True)
        await db.audit(cid, cb.from_user.id, 0, "setting", "ok", key)
        sec = next(s for s, v in SECTIONS.items() if key in v[1])
        text, kb = await render_section(cid, sec)
    elif act in ("th", "ft"):
        i = int(p[3])
        if not 0 <= i < (len(THEMES) if act == "th" else len(FONTS)):
            return await cb.answer("Invalid", show_alert=True)
        key = "theme" if act == "th" else "font"
        if cid:
            await db.set_group(cid, **{key: i})
        else:
            await db.set_meta(**{key: i})
        text, kb = await render_look(cid)
    elif act == "rs":
        return await edit(cb, "♻️ <b>Reset ALL settings of this group to defaults?</b>\n(VIPs, warnings and logs are kept.)",
                          mk([[("✅ Yes, reset", f"s:rsy:{cid}"), ("⬅️ No", f"s:home:{cid}")]]))
    elif act == "rsy":
        await db.reset_group(cid)
        await db.audit(cid, cb.from_user.id, 0, "reset_settings")
        text, kb = await render_home(cid)
    else:
        return await cb.answer()
    await edit(cb, text, kb)
    await cb.answer("✅ Saved" if act in ("t", "n", "c", "th", "ft", "rsy") else None)


# ═════════════════════════ VERIFICATION (CAPTCHA) ═════════════════════════
STATUS_LABEL = {None: "⚪ Not Started", "pending": "🟡 Pending", "captcha": "🧩 CAPTCHA Required", "verified": "🟢 Verified",
                "expired": "⌛ Expired (retry available)", "failed": "🔁 Retry Available",
                "review": "🔴 Restricted / Awaiting Staff Review", "left": "🚪 Left the group"}
_sysrand = random.SystemRandom()


def make_captcha():
    op = _sysrand.choice("+-×")
    if op == "+":
        a, b = _sysrand.randint(3, 30), _sysrand.randint(3, 30)
        ans = a + b
    elif op == "-":
        a = _sysrand.randint(10, 40)
        b = _sysrand.randint(2, a - 1)
        ans = a - b
    else:
        a, b = _sysrand.randint(2, 9), _sysrand.randint(2, 9)
        ans = a * b
    opts = {ans}
    while len(opts) < 4:
        opts.add(max(0, ans + _sysrand.choice([-1, 1]) * _sysrand.randint(1, 9)))
    opts = list(opts)
    _sysrand.shuffle(opts)
    return f"{a} {op} {b} = ?", opts, opts.index(ans)


def verify_link(token):
    return f"https://t.me/{BOT_USER}?start=v_{token}"


async def new_session(chat_id, title, uid, g, soft=False):
    await db.sessions.update_many({"chat_id": chat_id, "user_id": uid, "status": {"$in": ["pending", "captcha", "failed"]}},
                                  {"$set": {"status": "expired"}})
    token = secrets.token_urlsafe(8)
    now = time.time()
    exp = now + g["captcha_timeout"]
    await db.sessions.insert_one({"_id": token, "chat_id": chat_id, "title": title, "user_id": uid, "status": "pending",
                                  "created": now, "expires": exp, "attempts": 0, "rounds": 0, "soft": soft,
                                  "remind_n": 0, "purge_at": utc(exp + 7 * 86400)})
    await db.schedule("captcha", chat_id, uid, exp, extra={"token": token}, key=f"captcha:{token}")
    if g["remind_max"] > 0:
        await db.schedule("remind", chat_id, uid, now + g["remind_every"], extra={"token": token, "n": 0}, key=f"remind:{token}:0")
    return token


async def issue_challenge(token, uid, reset_attempts=False):
    q, opts, correct = make_captcha()
    upd = {"status": "captcha", "cid": secrets.randbelow(10 ** 9), "question": q, "options": opts, "correct": correct}
    if reset_attempts:
        upd["attempts"] = 0
    return await db.sessions.find_one_and_update(
        {"_id": token, "user_id": uid, "status": {"$in": ["pending", "captcha", "failed"]}, "expires": {"$gt": time.time()}},
        {"$set": upd}, return_document=True)


def challenge_view(doc, g_attempts, note=""):
    left = max(0, g_attempts - doc["attempts"])
    mins = max(1, int((doc["expires"] - time.time()) // 60))
    body = (f"{note}📍 Group: <b>{esc(doc.get('title'))}</b>\n\n🧩 Solve to prove you're human:\n\n"
            f"<b>{esc(doc['question'])}</b>\n\n❤️ Attempts left: <b>{left}</b>\n⏳ Expires in: <b>~{mins} min</b>")
    rows = [[(str(o), f"v:a:{doc['_id']}:{doc['cid']}:{i}") for i, o in enumerate(doc["options"][:2])],
            [(str(o), f"v:a:{doc['_id']}:{doc['cid']}:{i + 2}") for i, o in enumerate(doc["options"][2:])]]
    return body, mk(rows)


async def begin_verification(target, token, uid):
    """target = Message (deep link) or CallbackQuery. tap mode = verified instantly."""
    is_msg = isinstance(target, Message)
    send = target.answer if is_msg else target.message.answer
    user = target.from_user
    doc = await db.sessions.find_one({"_id": token})
    if not doc or doc["user_id"] != uid:
        return await send("❌ This verification link is invalid or isn't yours.")
    if doc["status"] == "verified":
        return await finish_verify(user, doc, target, again=True)
    if doc["status"] == "left":
        return await send("🚪 You left that group. Join again to verify.")
    if doc["status"] == "review":
        return await send("🔴 Too many failed rounds. Please contact the group staff for manual review.")
    try:
        mem = await bot.get_chat_member(doc["chat_id"], uid)
        if mem.status in ("left", "kicked"):
            return await send(f"🚪 You're no longer in <b>{esc(doc.get('title'))}</b>.")
    except TelegramAPIError:
        pass
    g = await db.get_group(doc["chat_id"])
    if doc["expires"] <= time.time() or doc["status"] in ("expired", "failed"):
        exp = time.time() + g["captcha_timeout"]
        doc = await db.sessions.find_one_and_update(
            {"_id": token, "user_id": uid, "status": {"$in": ["expired", "failed", "pending", "captcha"]}},
            {"$set": {"status": "pending", "expires": exp, "attempts": 0, "purge_at": utc(exp + 7 * 86400)}}, return_document=True)
        if not doc:
            return await send("⌛ Session no longer active. Use 📊 Know Your Status.")
        await db.schedule("captcha", doc["chat_id"], uid, exp, extra={"token": token}, key=f"captcha:{token}")
    if g["verify_mode"] == "tap":
        return await finish_verify(user, doc, target)
    nd = await issue_challenge(token, uid)
    if not nd:
        return await send("⌛ Session no longer active. Use 📊 Know Your Status.")
    body, kb = challenge_view(nd, g["captcha_attempts"])
    await send(await panel(0, "CAPTCHA", body), reply_markup=kb)


async def finish_verify(user, doc, target=None, again=False, preset=False):
    """Verify + unrestrict + start bot, all in one shot.
    again=True: already verified before (just show card). preset=True: caller already set status=verified."""
    uid, chat_id = user.id, doc["chat_id"]
    if not again and not preset:
        r = await db.sessions.update_one(
            {"_id": doc["_id"], "user_id": uid, "status": {"$in": ["pending", "captcha", "failed", "expired"]}},
            {"$set": {"status": "verified", "verified_at": time.time()}})
        if r.modified_count != 1:
            again = True
    ok = True
    if not again:
        await db.sessions.update_one({"_id": doc["_id"]}, {"$set": {"purge_at": utc(time.time() + 365 * 86400)}})
        if not doc.get("soft"):
            ok, _ = await svc_unmute(chat_id, uid, "", uid, manual=False, raw=True)
        await db.jobs.delete_many({"kind": {"$in": ["captcha", "remind"]}, "extra.token": doc["_id"], "status": "pending"})
        await db.audit(chat_id, uid, uid, "verify", "ok" if ok else "unrestrict_failed")
        spawn(settle_group_msgs(doc["_id"], user))
        if not ok:
            tolog(f"⚠️ Verified user but unrestrict failed in <code>{chat_id}</code> for <code>{uid}</code>")
    await db.upsert_user(user, started=True)
    lines = [kv("✅", "Verification", "<b>Successful</b>"), kv("📍", "Group", f"<b>{esc(doc.get('title'))}</b>"),
             kv("🛡", "Account", "🟢 Verified"), kv("🚀", "Bot", "Started &amp; ready")]
    body = "\n".join(lines)
    if not ok:
        body += "\n\n⚠️ I couldn't lift your restriction automatically — ask an admin to /unmute you."
    body += "\n\n🎉 You're all set! Use the buttons below."
    text = await panel(0, "VERIFIED", body)
    if isinstance(target, CallbackQuery):
        await safe_delete(target.message.chat.id, target.message.message_id)
    await send_card(uid, uid, text, start_kb(uid, True))


async def settle_group_msgs(token, user):
    d = await db.sessions.find_one({"_id": token})
    if not d:
        return
    cid = d["chat_id"]
    if d.get("rem_mid"):
        await safe_delete(cid, d["rem_mid"])
    mid = d.get("wel_mid")
    if mid:
        text = await panel(cid, "VERIFIED", f"✅ {mention(user.id, user.full_name)} is now <b>verified</b>\n🎉 Welcome to <b>{esc(d.get('title'))}</b>!")
        try:
            if d.get("wel_photo"):
                await bot.edit_message_caption(chat_id=cid, message_id=mid, caption=text, reply_markup=None)
            else:
                await bot.edit_message_text(text, chat_id=cid, message_id=mid, reply_markup=None)
            spawn(_del_later(cid, mid, 90))
        except TelegramAPIError:
            pass


async def drop_pending(chat_id, uid):
    async for d in db.sessions.find({"chat_id": chat_id, "user_id": uid,
                                     "status": {"$in": ["pending", "captcha", "failed", "expired", "review"]}}):
        for k in ("wel_mid", "rem_mid"):
            if d.get(k):
                await safe_delete(chat_id, d[k])
        await db.sessions.update_one({"_id": d["_id"]}, {"$set": {"status": "left", "wel_mid": None, "rem_mid": None}})
        await db.jobs.delete_many({"extra.token": d["_id"], "status": "pending"})


async def soft_verify(m: Message, chat_id):
    """Group 'Verify Now' notice (no restriction): tap = verified + bot started."""
    u = m.from_user
    try:
        mem = await bot.get_chat_member(chat_id, u.id)
    except TelegramAPIError:
        return await say(m, "❌ I can't find you in that group.")
    if mem.status in ("left", "kicked"):
        return await say(m, "🚪 You're not a member of that group.")
    pend = await db.sessions.find_one({"chat_id": chat_id, "user_id": u.id, "soft": {"$ne": True},
                                       "status": {"$in": ["pending", "captcha", "failed", "expired"]}}, sort=[("created", -1)])
    if pend:
        return await begin_verification(m, pend["_id"], u.id)
    done = await db.sessions.find_one({"chat_id": chat_id, "user_id": u.id, "status": "verified"})
    if done:
        return await finish_verify(u, done, m, again=True)
    g = await db.get_group(chat_id)
    now = time.time()
    doc = {"_id": secrets.token_urlsafe(8), "chat_id": chat_id, "title": g["_title"], "user_id": u.id, "status": "verified",
           "soft": True, "created": now, "expires": now, "attempts": 0, "rounds": 0, "verified_at": now}
    await db.sessions.insert_one(doc)
    await finish_verify(u, doc, m, preset=True)





async def cb_verify(cb: CallbackQuery, p):
    uid = cb.from_user.id
    act = p[1]
    if act == "menu":
        d = await db.sessions.find_one({"user_id": uid, "soft": {"$ne": True}, "status": {"$in": ["pending", "captcha", "failed", "expired"]}},
                                       sort=[("created", -1)])
        await cb.answer()
        if not d:
            return await cb.message.answer(await panel(0, "VERIFY", "✅ <b>Nothing pending.</b>\nJoin a protected group and tap <b>Verify Now</b> there — it takes one tap."))
        return await begin_verification(cb, d["_id"], uid)
    if act in ("go", "tap"):
        await cb.answer("⏳ Verifying…")
        return await begin_verification(cb, p[2], uid)
    if act == "st":
        docs = [d async for d in db.sessions.find({"user_id": uid, "soft": {"$ne": True}}).sort("created", -1).limit(5)]
        lines, rows = [], []
        for d in docs:
            st = d["status"]
            lines.append(f"📍 <b>{esc(d.get('title'))}</b>\n   {STATUS_LABEL.get(st, st)}")
            if st in ("expired", "failed", "pending", "captcha"):
                rows.append([(f"✅ Verify · {d.get('title', '')[:20]}", f"v:go:{d['_id']}")])
        body = "\n\n".join(lines) or STATUS_LABEL[None] + "\nJoin a protected group to start."
        body += f"\n\n🏷 Role: {await role_label(0, uid)}"
        await cb.answer()
        return await edit(cb, await panel(0, "YOUR STATUS", body), mk(rows + [[("🏠 Home", "h:start")]]))
    token = p[2]
    doc = await db.sessions.find_one({"_id": token})
    if not doc or doc["user_id"] != uid:
        return await cb.answer("⛔ This isn't your verification.", show_alert=True)
    g = await db.get_group(doc["chat_id"])
    if act == "r":  # retry
        if doc["status"] == "verified":
            return await cb.answer("Already verified ✅", show_alert=True)
        if doc["status"] == "review" or doc.get("rounds", 0) >= 3:
            return await cb.answer("Too many rounds — contact staff.", show_alert=True)
        try:
            mem = await bot.get_chat_member(doc["chat_id"], uid)
            if mem.status in ("left", "kicked"):
                return await cb.answer("You're no longer in that group.", show_alert=True)
        except TelegramAPIError:
            return await cb.answer("Can't check the group right now.", show_alert=True)
        exp = time.time() + g["captcha_timeout"]
        await db.sessions.update_one({"_id": token, "user_id": uid, "status": {"$in": ["expired", "failed", "pending", "captcha"]}},
                                     {"$set": {"status": "pending", "expires": exp, "attempts": 0, "purge_at": utc(exp + 7 * 86400)}})
        await db.schedule("captcha", doc["chat_id"], uid, exp, extra={"token": token}, key=f"captcha:{token}")
        nd = await issue_challenge(token, uid, True)
        if not nd:
            return await cb.answer("Couldn't start. Try again.", show_alert=True)
        body, kb = challenge_view(nd, g["captcha_attempts"], "🔁 <b>New challenge</b>\n\n")
        await cb.answer()
        return await edit(cb, await panel(0, "CAPTCHA", body), kb)
    if act == "a":  # answer (math mode)
        cid_, idx = int(p[3]), int(p[4])
        if doc["status"] == "verified":
            return await cb.answer("Already verified ✅")
        if doc["expires"] <= time.time():
            await cb.answer("⌛ Expired", show_alert=True)
            return await edit(cb, "⌛ This verification expired.", mk([[("🔁 Retry", f"v:r:{token}")]]))
        if doc["status"] != "captcha" or doc.get("cid") != cid_:
            return await cb.answer("⚠️ Old button — use the latest challenge.", show_alert=True)
        r = await db.sessions.update_one(
            {"_id": token, "user_id": uid, "status": "captcha", "cid": cid_, "correct": idx, "expires": {"$gt": time.time()}},
            {"$set": {"status": "verified", "verified_at": time.time()}})
        if r.modified_count == 1:
            await cb.answer("✅ Correct!")
            return await finish_verify(cb.from_user, doc, cb, preset=True)
        d2 = await db.sessions.find_one_and_update({"_id": token, "user_id": uid, "status": "captcha", "cid": cid_},
                                                   {"$inc": {"attempts": 1}}, return_document=True)
        if not d2:
            return await cb.answer("⚠️ Already handled.", show_alert=True)
        if d2["attempts"] >= g["captcha_attempts"]:
            rounds = d2.get("rounds", 0) + 1
            st = "review" if rounds >= 3 else "failed"
            await db.sessions.update_one({"_id": token, "status": "captcha"}, {"$set": {"status": st, "rounds": rounds}})
            await db.audit(doc["chat_id"], uid, uid, "captcha_failed", "failed", f"round {rounds}")
            await cb.answer("❌ Wrong — no attempts left", show_alert=True)
            msg = "🔴 Too many failures. You stay restricted until staff reviews you." if st == "review" else "❌ <b>Too many wrong answers.</b>\nYou can try again."
            return await edit(cb, msg, None if st == "review" else mk([[("🔁 Try again", f"v:r:{token}")]]))
        nd = await issue_challenge(token, uid)
        await cb.answer("❌ Wrong answer")
        if nd:
            body, kb = challenge_view(nd, g["captcha_attempts"], "❌ <b>Wrong answer — new challenge</b>\n\n")
            await edit(cb, await panel(0, "CAPTCHA", body), kb)


def render_welcome(tpl, user, chat):
    t = esc(tpl or "")
    for k, v in (("{name}", esc(user.full_name)), ("{mention}", mention(user.id, user.full_name)),
                 ("{group}", esc(chat.title)), ("{id}", str(user.id))):
        t = t.replace(k, v)
    return t


join_times = defaultdict(deque)


async def welcome_card(chat, u, g, verify):
    n = None
    try:
        n = await bot.get_chat_member_count(chat.id)
    except TelegramAPIError:
        pass
    if g["welcome_text"]:
        body = render_welcome(g["welcome_text"], u, chat)
    else:
        rows = [f"👋 <b>Welcome</b> {mention(u.id, u.full_name)}", "",
                kv("🏘", "Group", f"<b>{esc(chat.title)}</b>"), kv("🆔", "ID", f"<code>{u.id}</code>"),
                kv("🔗", "Username", ("@" + esc(u.username)) if u.username else "—"),
                kv("💎", "Premium", "Yes ✨" if getattr(u, "is_premium", False) else "No")]
        if n:
            rows.append(kv("👥", "Member", f"<b>#{n}</b>"))
        body = "\n".join(rows)
    if verify:
        body += "\n\n🔐 <b>Verification required</b>\nTap <b>Verify Now</b> below — one tap and you can chat."
    else:
        body += "\n\n📜 Read /rules and enjoy your stay!"
    return await panel(chat.id, "WELCOME", body)


async def on_join(m: Message):
    chat = m.chat
    g = await db.get_group(chat.id)
    if g["del_service"]:
        await safe_delete(chat.id, m.message_id)
    for u in m.new_chat_members:
        if u.is_bot:
            continue
        await db.upsert_user(u, chat.id)
        tolog(f"👥 <b>NEW MEMBER</b>\n━━━━━━━━━━━━━━━\n👤 {mention(u.id, u.full_name)}\n🆔 <code>{u.id}</code>\n"
              f"🔗 {('@' + esc(u.username)) if u.username else '—'}\n💎 Premium: {'Yes' if getattr(u, 'is_premium', False) else 'No'}\n"
              f"🏘 Group: {esc(chat.title)} (<code>{chat.id}</code>)\n🕒 {tnow()}")
        if u.id in db.gban_ids:
            ok, _ = await svc_ban(chat.id, u.id, u.full_name, bot.id, None, "globally banned")
            if ok:
                await temp(chat.id, f"🚫 {mention(u.id, u.full_name)} is globally banned and was removed.", 15)
            continue
        if (u.first_name or "").lower().startswith("deleted account"):
            await svc_kick(chat.id, u.id, u.full_name, bot.id, "deleted account")
            continue
        raid = False
        if g["antiraid"]:
            dq = join_times[chat.id]
            now = time.time()
            dq.append(now)
            while dq and now - dq[0] > g["raid_window"]:
                dq.popleft()
            if len(dq) >= g["raid_limit"] and g["raid_until"] < now:
                await db.set_group(chat.id, raid_until=now + 600)
                g = await db.get_group(chat.id)
                await db.audit(chat.id, bot.id, 0, "raid_detected", "ok", f"{len(dq)} joins")
                tolog(f"🚨 <b>RAID ALERT</b> in {esc(chat.title)} (<code>{chat.id}</code>)\n{len(dq)} joins / {g['raid_window']}s → forced verification for 10 min")
                await temp(chat.id, "🚨 <b>Raid protection active</b> — every new member must verify for the next 10 minutes. Admins: /settings", 30)
            raid = g["raid_until"] > time.time()
        if g["captcha"] or raid:
            err = await bot_need(chat.id, "can_restrict_members")
            if err:
                await notify_missing(chat.id, "Verification needs <b>Restrict members</b>. " + err)
            else:
                try:
                    await bot.restrict_chat_member(chat.id, u.id, permissions=NO_PERMS)
                except TelegramAPIError as e:
                    await notify_missing(chat.id, tg_err(e))
                    continue
                token = await new_session(chat.id, chat.title, u.id, g)
                msg = await send_card(chat.id, u.id, await welcome_card(chat, u, g, True),
                                      mk([[("✅ Verify Now", "url:" + verify_link(token))]]))
                # message stays until the user verifies (then it turns into a "verified" card)
                await db.sessions.update_one({"_id": token}, {"$set": {"wel_mid": msg.message_id, "wel_photo": bool(msg.photo)}})
                try:
                    btn = "✅ Verify & Start" if g["verify_mode"] == "tap" else "🧩 Start Verification"
                    await send_card(u.id, u.id, await panel(0, "WELCOME", f"🛡️ Welcome to <b>{esc(chat.title)}</b>!\n"
                                    "Tap the button — you'll be verified and the bot starts instantly."),
                                    mk([[(btn, f"v:tap:{token}")]]))
                except TelegramAPIError:
                    pass  # user hasn't started the bot yet: the group button handles it
                continue
        if g["welcome"]:
            msg = await send_card(chat.id, u.id, await welcome_card(chat, u, g, False))
            spawn(_del_later(chat.id, msg.message_id, 300))


async def on_leave(m: Message):
    g = await db.get_group(m.chat.id)
    if g["del_service"]:
        await safe_delete(m.chat.id, m.message_id)
    if m.left_chat_member:
        await drop_pending(m.chat.id, m.left_chat_member.id)
        await db.forget_member(m.chat.id, m.left_chat_member.id)


@router.chat_member()
async def on_chat_member(ev: ChatMemberUpdated):
    """Tracks every join/leave (needs bot admin) — this is what makes /zombies see more members."""
    chat, new = ev.chat, ev.new_chat_member
    if chat.type not in GROUPS or new.user.is_bot:
        return
    gone = new.status in ("left", "kicked") or (new.status == "restricted" and not getattr(new, "is_member", True))
    if gone:
        await db.forget_member(chat.id, new.user.id)
        await drop_pending(chat.id, new.user.id)
    else:
        await db.upsert_user(new.user, chat.id)


# ═════════════════════════ AUTO PROTECTION ═════════════════════════
flood_hist = defaultdict(deque)
spam_hist = defaultdict(deque)
URL_RE = re.compile(r"(?:https?://|www\.|t\.me/|telegram\.me/)[^\s<>\"']+|\b[a-z0-9-]+\.(?:com|net|org|io|me|xyz|ru|in|info|biz|co|link|site|top|club|online)\b/?\S*", re.I)
_bw_cache = {}


def extract_hosts(m: Message):
    text = m.text or m.caption or ""
    urls = [x.group(0) for x in URL_RE.finditer(text)]
    for ent in (m.entities or m.caption_entities or []):
        if ent.type == "text_link" and ent.url:
            urls.append(ent.url)
        elif ent.type == "url":
            urls.append(ent.extract_from(text))
    hosts = set()
    for u in urls:
        u = u if "://" in u else "http://" + u
        h = (urlparse(u).hostname or "").lower().removeprefix("www.")
        if h:
            hosts.add(h)
    return hosts


def bw_match(chat_id, words, text):
    key = (chat_id, hash(tuple(words)))
    rx = _bw_cache.get(key)
    if rx is None:
        rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + r")(?!\w)")
        if len(_bw_cache) > 200:
            _bw_cache.clear()
        _bw_cache[key] = rx
    mo = rx.search(normalize(text))
    return mo.group(0) if mo else None


async def violation(m: Message, g, reason, kind):
    u = m.from_user
    if g["safe_mode"]:
        tolog(f"🧪 <b>DRY-RUN</b> ({kind}) in {esc(m.chat.title)}\nUser: {mention(u.id, u.full_name)} <code>{u.id}</code>\nReason: {esc(reason)}\nNo action taken.")
        await db.audit(m.chat.id, bot.id, u.id, "dryrun_" + kind, "ok", reason)
        return
    err = await bot_need(m.chat.id, "can_delete_messages")
    if err:
        return await notify_missing(m.chat.id, "Auto-protection needs <b>Delete messages</b>. " + err)
    await safe_delete(m.chat.id, m.message_id)
    if kind == "flood":
        ok, t = await svc_mute(m.chat.id, u.id, u.full_name, bot.id, 300, reason)
        await temp(m.chat.id, t if ok else f"🌊 Flood from {mention(u.id, u.full_name)} deleted.", 10)
    else:
        t, _ = await issue_warn(m.chat.id, u.id, u.full_name, bot.id, reason)
        await temp(m.chat.id, t, 12)
    await db.audit(m.chat.id, bot.id, u.id, "auto_" + kind, "ok", reason)


async def run_protection(m: Message, g):
    u = m.from_user
    now = time.time()
    key = (m.chat.id, u.id)
    text = m.text or m.caption or ""
    if g["antiflood"]:
        dq = flood_hist[key]
        dq.append(now)
        while dq and now - dq[0] > g["flood_window"]:
            dq.popleft()
        if len(dq) > g["flood_limit"]:
            dq.clear()
            return await violation(m, g, f"flood (> {g['flood_limit']} msgs / {g['flood_window']}s)", "flood")
    if not text:
        return
    if g["badwords"] and g["badwords_on"]:
        hit = bw_match(m.chat.id, g["badwords"], text)
        if hit:
            return await violation(m, g, "blocked word", "badword")
    if g["links"]:
        bad = [h for h in extract_hosts(m) if not any(h == d or h.endswith("." + d) for d in g["allowed_domains"])]
        if bad:
            return await violation(m, g, "link not allowed: " + bad[0], "link")
    if g["antispam"] and not re.match(r"^[/.!]\w", text) and len(text) > 3:
        n = normalize(text)
        dq = spam_hist[key]
        dq.append((now, n))
        while dq and now - dq[0][0] > 30:
            dq.popleft()
        if sum(1 for _, x in dq if x == n) >= 3:
            dq.clear()
            return await violation(m, g, "repeated message spam", "spam")


# ═════════════════════════ REPORT / @admin ═════════════════════════
_last_report = {}


async def cmd_report(m: Message, args):
    if not await need_group(m):
        return
    uid = m.from_user.id
    if time.time() - _last_report.get(uid, 0) < 60:
        return await say(m, "⏳ Please wait a minute before reporting again.")
    r = m.reply_to_message
    target = r.from_user if r and r.from_user else None
    if target and (target.id == uid or await is_trusted(m.chat.id, target.id)):
        return await say(m, "ℹ️ You can't report that user.")
    _last_report[uid] = time.time()
    rid = secrets.token_urlsafe(6)
    reason = re.sub(r"(?i)^@admins?\s*", "", args or "").strip()[:200]
    await db.reports.insert_one({"_id": rid, "chat_id": m.chat.id, "reporter": uid, "target": target.id if target else 0,
                                 "msg_id": r.message_id if r else 0, "reason": reason, "status": "open", "ts": time.time()})
    admins = [a for a in (await chat_admins(m.chat.id)).values() if not a.user.is_bot][:5]
    pings = " ".join(f'<a href="tg://user?id={a.user.id}">\u2063</a>' for a in admins)
    link = None
    if r:
        link = (f"https://t.me/{m.chat.username}/{r.message_id}" if m.chat.username
                else f"https://t.me/c/{str(m.chat.id)[4:]}/{r.message_id}" if str(m.chat.id).startswith("-100") else None)
    rows = [[("✅ Resolve", f"rp:ok:{rid}"), ("🗑 Delete msg", f"rp:del:{rid}"), ("⚠️ Warn", f"rp:warn:{rid}")]]
    if link:
        rows.append([("👁 View message", "url:" + link)])
    body = (f"🚨 <b>Report</b> by {mention(uid, m.from_user.full_name)}\n🎯 Target: {mention(target.id, target.full_name) if target else '—'}\n"
            f"📝 {esc(reason) or 'No reason given'}{pings}")
    await bot.send_message(m.chat.id, body, reply_markup=mk(rows))
    tolog(f"🚨 <b>REPORT</b> in {esc(m.chat.title)}\nBy {mention(uid, m.from_user.full_name)} → {mention(target.id, target.full_name) if target else '—'}\n{esc(reason)}")
    await safe_delete(m.chat.id, m.message_id)


async def cb_report(cb: CallbackQuery, p):
    d = await db.reports.find_one({"_id": p[2]})
    if not d or cb.message.chat.id != d["chat_id"]:
        return await cb.answer("Report not found.", show_alert=True)
    if not await actor_can(d["chat_id"], cb.from_user.id, "can_delete_messages" if p[1] == "del" else "can_restrict_members" if p[1] == "warn" else None):
        return await cb.answer("⛔ Staff only.", show_alert=True)
    if d["status"] != "open":
        return await cb.answer("Already handled.", show_alert=True)
    res = None
    if p[1] == "del":
        if d["msg_id"]:
            await safe_delete(d["chat_id"], d["msg_id"])
        res = "message deleted"
    elif p[1] == "warn":
        if d["target"]:
            t, _ = await issue_warn(d["chat_id"], d["target"], await name_of(d["chat_id"], d["target"]), cb.from_user.id, "report")
        res = "target warned"
    else:
        res = "dismissed/resolved"
    r = await db.reports.update_one({"_id": p[2], "status": "open"}, {"$set": {"status": "closed", "outcome": res, "by": cb.from_user.id}})
    if r.modified_count:
        await db.audit(d["chat_id"], cb.from_user.id, d["target"], "report_" + p[1], "ok")
        await edit(cb, cb.message.html_text + f"\n\n✅ <b>Handled</b> by {mention(cb.from_user.id, cb.from_user.full_name)}: {res}")
    await cb.answer("Done")


# ═════════════════════════ ZOMBIES / BANALL / CALLPM ═════════════════════════
PENDING = {}  # token -> dict (zombies, banall, broadcast)


async def scan_zombies(chat_id, progress=None):
    ids = await db.tracked_ids(chat_id, 5000)
    found, gone = [], [0]
    sem = asyncio.Semaphore(5)

    async def chk(u):
        async with sem:
            await asyncio.sleep(0.12)
            for _ in range(2):
                try:
                    mem = await bot.get_chat_member(chat_id, u)
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                    continue
                except TelegramAPIError:
                    return
                if mem.status in ("left", "kicked"):
                    gone[0] += 1
                    await db.forget_member(chat_id, u)
                elif (mem.user.first_name or "").strip().lower().startswith("deleted account"):
                    found.append(u)
                return

    for i in range(0, len(ids), 50):
        await asyncio.gather(*(chk(u) for u in ids[i:i + 50]))
        if progress and (i // 50) % 3 == 2:
            await progress(min(i + 50, len(ids)), len(ids))
    return len(ids), found, gone[0]


async def cmd_zombies(m: Message, args):
    if not await mod_guard(m, "can_restrict_members", "can_restrict_members"):
        return
    uid = m.from_user.id
    msg = await say(m, await panel(m.chat.id, "ZOMBIE SCAN", "🧟 Scanning known members for deleted accounts…"))

    async def prog(done, total):
        try:
            await msg.edit_text(await panel(m.chat.id, "ZOMBIE SCAN", f"🧟 Scanning… <b>{done}/{total}</b>"))
        except TelegramAPIError:
            pass

    total, found, gone = await scan_zombies(m.chat.id, prog)
    note = "\n\n<i>Bots can only check members they have seen (messages / joins since I became admin). Keep me admin so every new join is tracked.</i>"
    head = kv("🔎", "Scanned", f"<b>{total}</b>") + "\n" + kv("🚪", "Already left", f"<b>{gone}</b>") + "\n"
    if not found:
        return await msg.edit_text(await panel(m.chat.id, "ZOMBIE SCAN", head + kv("🧟", "Deleted accounts", "<b>0</b> ✅") + note))
    t = secrets.token_urlsafe(5)
    PENDING[t] = {"type": "zombies", "uid": uid, "chat": m.chat.id, "ids": found}
    await msg.edit_text(await panel(m.chat.id, "ZOMBIE SCAN", head + kv("🧟", "Deleted accounts", f"<b>{len(found)}</b>") + note),
                        reply_markup=mk([[("🧹 Remove them", f"z:y:{t}"), ("❌ Cancel", f"z:n:{t}")]]))


async def cmd_banall(m: Message, args):
    if not is_sudo(m.from_user.id):
        return
    if not await need_group(m):
        return
    err = await bot_need(m.chat.id, "can_restrict_members")
    if err:
        return await say(m, "❌ " + err)
    ids = [u for u in await db.tracked_ids(m.chat.id, 2000)
           if u not in await chat_admins(m.chat.id) and not is_sudo(u) and u != bot.id]
    if not ids:
        return await say(m, "ℹ️ No tracked non-admin members.")
    t = secrets.token_urlsafe(5)
    PENDING[t] = {"type": "banall", "uid": m.from_user.id, "chat": m.chat.id, "ids": ids}
    await say(m, f"☢️ <b>BAN ALL</b>\nThis will ban <b>{len(ids)}</b> tracked non-admin members. Irreversible in bulk!",
              mk([[("☢️ CONFIRM BAN", f"z:ba:{t}"), ("❌ Cancel", f"z:n:{t}")]]))


async def cb_pending(cb: CallbackQuery, p):
    d = PENDING.get(p[2])
    if p[1] == "n":
        if d and d["uid"] == cb.from_user.id:
            PENDING.pop(p[2], None)
        await cb.answer("Cancelled")
        return await edit(cb, "❌ Cancelled.")
    if not d or d["uid"] != cb.from_user.id or d["chat"] != cb.message.chat.id:
        return await cb.answer("⛔ Not allowed / expired.", show_alert=True)
    allowed = is_sudo(d["uid"]) if d["type"] == "banall" else await actor_can(d["chat"], d["uid"], "can_restrict_members")
    if not allowed:
        return await cb.answer("⛔ Not allowed / expired.", show_alert=True)
    PENDING.pop(p[2], None)
    await cb.answer("Working…")
    ban = d["type"] == "banall"
    ok = fail = 0
    for u in d["ids"]:
        try:
            if ban:
                await bot.ban_chat_member(d["chat"], u)
            else:
                await bot.ban_chat_member(d["chat"], u)
                await bot.unban_chat_member(d["chat"], u, only_if_banned=True)
            await db.forget_member(d["chat"], u)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            fail += 1
        except TelegramAPIError:
            fail += 1
        await asyncio.sleep(0.05)
    await db.audit(d["chat"], cb.from_user.id, 0, d["type"], "ok", f"{ok} ok / {fail} failed")
    await edit(cb, await panel(d["chat"], "BAN ALL DONE" if ban else "ZOMBIES CLEANED",
                               kv("✅", "Removed", f"<b>{ok}</b>") + "\n" + kv("❌", "Failed", f"<b>{fail}</b>")))


async def cmd_callpm(m: Message, args):
    if not is_sudo(m.from_user.id):
        return
    if not await need_group(m):
        return
    txt = esc(args) if args else "Start me in DM to get verified, check your status and unlock all features!"
    await safe_delete(m.chat.id, m.message_id)
    await bot.send_message(m.chat.id, await panel(m.chat.id, "DM FUNNEL", f"📩 {txt}"),
                           reply_markup=mk([[("🚀 Start GUARDIX in DM", f"url:https://t.me/{BOT_USER}?start=funnel")]]))


# ═════════════════════════ OWNER / SUDO TOOLS ═════════════════════════
async def need_sudo(m: Message):
    if not is_sudo(m.from_user.id):
        return False
    return True


async def need_dm_sudo(m: Message):
    if not is_sudo(m.from_user.id):
        return False
    if m.chat.type != "private":
        await say(m, "🔒 Use this command in my DM for safety.")
        return False
    return True


async def cmd_owner(m, args):
    if not await need_dm_sudo(m):
        return
    try:
        dbms = f"🟢 {await db.ping():.0f} ms"
    except Exception:
        dbms = "🔴 down"
    meta = await db.get_meta()
    body = (f"🤖 @{esc(BOT_USER)} · v{VERSION}\n⏱ Uptime: <b>{fmt_secs(time.time() - START)}</b>\n🗄 Database: {dbms}\n"
            f"👥 Users: <b>{await db.users.estimated_document_count()}</b> · 🏘 Groups: <b>{await db.groups.count_documents({'active': True})}</b>\n"
            f"⚡ Godmode: {onoff(meta.get('godmode', True))} · 🛠 Maintenance: {onoff(meta.get('maintenance'))}\n"
            f"📝 Log group: {'set' if cfg.LOG_GROUP_ID else 'not set'}\n⏳ Pending jobs: <b>{await db.jobs.count_documents({'status': 'pending'})}</b>")
    await say(m, await panel(0, "OWNER PANEL", body))


async def cmd_uptime(m, args):
    if is_sudo(m.from_user.id):
        await say(m, f"⏱ Uptime: <b>{fmt_secs(time.time() - START)}</b>\n🕒 Started: {datetime.fromtimestamp(START, cfg.TZ).strftime('%d %b %Y %I:%M %p')}")


async def cmd_stop(m, args):
    if not await need_sudo(m):
        return
    off = (args or "").strip().lower() in ("off", "start", "resume")
    await db.set_meta(maintenance=not off)
    await db.audit(0, m.from_user.id, 0, "maintenance", "ok", "off" if off else "on")
    await say(m, "▶️ Maintenance <b>OFF</b> — bot is live for everyone." if off
              else "🛑 Maintenance <b>ON</b> — bot ignores everyone except Owner/Sudo.\nResume with <code>/stop off</code>.")


async def cmd_godmode(m, args):
    if not await need_sudo(m):
        return
    a = (args or "").strip().lower()
    cur = (await db.get_meta()).get("godmode", True)
    if a in ("on", "off"):
        if not is_owner(m.from_user.id):
            return await say(m, "⛔ Only the Owner can change godmode.")
        await db.set_meta(godmode=(a == "on"))
        cur = a == "on"
        await db.audit(0, m.from_user.id, 0, "godmode", "ok", a)
    await say(m, f"⚡ <b>Godmode</b>: {onoff(cur)} {'ON' if cur else 'OFF'}\nWhen ON, Owner &amp; Sudo have admin power in every group (bot still needs its own rights).\n"
              + ("" if is_owner(m.from_user.id) else "<i>Only the Owner can change this: /godmode on|off</i>"))


async def cmd_sudolist(m, args):
    if not await need_sudo(m):
        return
    ids = {cfg.OWNER_ID} | cfg.SUDO_IDS | db.sudo_ids
    lines = []
    for i in ids:
        d = await db.get_user(i) or {}
        lines.append(f"{'👑' if i == cfg.OWNER_ID else '🔱'} {mention(i, d.get('first_name') or i)} — <code>{i}</code>")
    await say(m, await panel(0, "SUDO LIST", "\n".join(lines)))


async def _uid_arg(m, args):
    uid, name, rest = await resolve_target(m, args)
    return uid, name, rest


async def cmd_addsudo(m, args):
    if not is_owner(m.from_user.id):
        return
    uid, name, _ = await _uid_arg(m, args)
    if not uid:
        return await say(m, "Usage: /addsudo [user id | reply]")
    if uid == cfg.OWNER_ID:
        return await say(m, "That's you, Owner 👑")
    await db.add_sudo(uid, m.from_user.id)
    await db.audit(0, m.from_user.id, uid, "addsudo")
    await say(m, f"🔱 {mention(uid, name)} added as <b>Sudo</b>.")


async def cmd_delsudo(m, args):
    if not is_owner(m.from_user.id):
        return
    uid, name, _ = await _uid_arg(m, args)
    if not uid:
        return await say(m, "Usage: /delsudo [user id | reply]")
    if uid in cfg.SUDO_IDS:
        return await say(m, "ℹ️ That Sudo comes from the SUDO_IDS environment variable — remove it in Railway.")
    await db.del_sudo(uid)
    await db.audit(0, m.from_user.id, uid, "delsudo")
    await say(m, f"🔱 {mention(uid, name)} removed from Sudo.")


async def _bg_gban(uid, ban, report_chat, actor):
    ok = fail = 0
    for cid in await db.active_groups():
        try:
            if ban:
                await bot.ban_chat_member(cid, uid)
            else:
                await bot.unban_chat_member(cid, uid, only_if_banned=True)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            fail += 1
        except TelegramAPIError:
            fail += 1
        await asyncio.sleep(0.1)
    await db.audit(0, actor, uid, "gban" if ban else "ungban", "ok", f"{ok} groups / {fail} failed")
    try:
        await bot.send_message(report_chat, f"{'🚫 Gban' if ban else '♻️ Ungban'} finished for <code>{uid}</code>: ✅ {ok} groups · ❌ {fail} failed (bot not admin / not member)")
    except TelegramAPIError:
        pass


async def cmd_gban(m, args):
    if not is_owner(m.from_user.id):
        return
    uid, name, reason = await _uid_arg(m, args)
    if not uid:
        return await say(m, "Usage: /gban [user id | reply] [reason]")
    if is_sudo(uid):
        return await say(m, "🛡 Can't gban Owner/Sudo.")
    await db.add_gban(uid, m.from_user.id, reason or "no reason")
    spawn(_bg_gban(uid, True, m.chat.id, m.from_user.id))
    await say(m, f"🚫 {mention(uid, name)} <b>globally banned</b>. Applying to all groups in background…")


async def cmd_ungban(m, args):
    if not is_owner(m.from_user.id):
        return
    uid, name, _ = await _uid_arg(m, args)
    if not uid:
        return await say(m, "Usage: /ungban [user id]")
    ok = await db.del_gban(uid)
    spawn(_bg_gban(uid, False, m.chat.id, m.from_user.id))
    await say(m, f"♻️ {mention(uid, name)} removed from gban list." if ok else "ℹ️ User wasn't gbanned; unbanning anyway…")


async def cmd_leavechat(m, args):
    if not is_owner(m.from_user.id):
        return
    t = (args or "").strip()
    if not t.lstrip("-").isdigit():
        return await say(m, "Usage: /leavechat chat_id")
    try:
        await bot.leave_chat(int(t))
        await db.groups.update_one({"_id": int(t)}, {"$set": {"active": False}})
        await db.audit(int(t), m.from_user.id, 0, "leavechat")
        await say(m, "👋 Left the chat.")
    except TelegramAPIError as e:
        await say(m, "❌ " + tg_err(e))


# ---- events ----
async def cmd_addevent(m, args):
    if not await need_dm_sudo(m):
        return
    parts = [x.strip() for x in (args or "").split("|")]
    if len(parts) < 2 or not parts[0]:
        return await say(m, "Usage: /addevent Title | Description | When")
    eid = await db.next_id("events")
    await db.events.insert_one({"_id": eid, "title": parts[0][:100], "desc": parts[1][:1000], "when": (parts[2] if len(parts) > 2 else "TBA")[:100],
                                "by": m.from_user.id, "ts": time.time()})
    await say(m, f"🎉 Event <b>#{eid}</b> added.")


async def cmd_events(m, args):
    if not await need_dm_sudo(m):
        return
    evs = [e async for e in db.events.find().sort("_id", -1).limit(20)]
    lines = [f"<b>#{e['_id']}</b> {esc(e['title'])} — <i>{esc(e['when'])}</i>" for e in evs]
    await say(m, await panel(0, "EVENTS", "\n".join(lines) or "No events. Add one with /addevent"))


async def cmd_event(m, args):
    if not await need_dm_sudo(m):
        return
    t = (args or "").strip().lstrip("#")
    e = await db.events.find_one({"_id": int(t)}) if t.isdigit() else None
    if not e:
        return await say(m, "Usage: /event id")
    await say(m, await panel(0, f"EVENT #{e['_id']}", f"🎉 <b>{esc(e['title'])}</b>\n🕒 {esc(e['when'])}\n\n{esc(e['desc'])}"))


async def cmd_delevent(m, args):
    if not await need_dm_sudo(m):
        return
    t = (args or "").strip().lstrip("#")
    if not t.isdigit():
        return await say(m, "Usage: /delevent id")
    r = await db.events.delete_one({"_id": int(t)})
    await say(m, "🗑 Event deleted." if r.deleted_count else "ℹ️ No such event.")


async def cmd_clearevents(m, args):
    if not is_owner(m.from_user.id):
        return
    r = await db.events.delete_many({})
    await say(m, f"🧹 Cleared <b>{r.deleted_count}</b> event(s).")


# ---- logs ----
async def render_logs(cid, page):
    rows, total = await db.recent_audit(cid, page * 8, 8)
    lines = []
    for r in rows:
        lines.append(f"🕒 {datetime.fromtimestamp(r['ts'], cfg.TZ).strftime('%d %b %H:%M')} · <b>{esc(r['action'])}</b> · {'✅' if r['result'] == 'ok' else '⚠️ ' + esc(r['result'])}\n"
                     f"   👮 <code>{r['actor']}</code> → 🎯 <code>{r['target']}</code>" + (f" · {esc(r['detail'])[:60]}" if r.get("detail") else ""))
    pages = max(1, -(-total // 8))
    nav = []
    if page > 0:
        nav.append(("⬅️ Prev", f"lg:{cid}:{page - 1}"))
    if page + 1 < pages:
        nav.append(("Next ➡️", f"lg:{cid}:{page + 1}"))
    return await panel(cid, f"AUDIT LOGS {page + 1}/{pages}", "\n\n".join(lines) or "No events yet."), mk([nav] if nav else [])


async def cmd_logs(m, args):
    if m.chat.type == "private":
        if not is_sudo(m.from_user.id):
            return await say(m, "⛔ Owner/Sudo only.")
        cid = 0
    else:
        if not await mod_guard(m, "can_restrict_members"):
            return
        cid = m.chat.id
    text, kb = await render_logs(cid, 0)
    await say(m, text, kb)


async def cb_logs(cb: CallbackQuery, p):
    cid, page = int(p[1]), int(p[2])
    ok = is_sudo(cb.from_user.id) and cb.message.chat.type == "private" if cid == 0 else (
        cb.message.chat.id == cid and await actor_can(cid, cb.from_user.id, "can_restrict_members"))
    if not ok:
        return await cb.answer("⛔ Not allowed.", show_alert=True)
    text, kb = await render_logs(cid, max(0, page))
    await edit(cb, text, kb)
    await cb.answer()


# ---- broadcast ----
async def _start_bc(m: Message, args, mode):
    if not await need_dm_sudo(m):
        return
    r = m.reply_to_message
    if mode == "promo":
        t = (args or "").strip() or ((r.text or r.caption or "") if r else "")
        if not t:
            return await say(m, "Usage: /dmpromo your promo text (or reply to a text)")
        payload = {"kind": "text", "text": await panel(0, "SPECIAL OFFER", esc(t[:3500])), "promo": True}
    elif r:
        payload = {"kind": "copy", "from": m.chat.id, "mid": r.message_id}
    elif (args or "").strip():
        payload = {"kind": "text", "text": esc(args.strip())}
    else:
        return await say(m, f"Usage: /{'gcast' if mode == 'groups' else 'broadcast'} text — or reply to any message (media works too)")
    n = (await db.users.count_documents({"started": True, "blocked": {"$ne": True}})) if mode != "groups" else len(await db.active_groups())
    t = secrets.token_urlsafe(5)
    PENDING[t] = {"type": "bc", "uid": m.from_user.id, "mode": mode, "payload": payload, "ts": time.time()}
    await say(m, f"👆 <b>Preview below.</b> Target: <b>{n}</b> {'groups' if mode == 'groups' else 'users'}. Send?")
    await _send_one(m.chat.id, payload)
    await bot.send_message(m.chat.id, "Confirm broadcast?", reply_markup=mk([[("📣 SEND NOW", f"bc:y:{t}"), ("❌ Cancel", f"bc:n:{t}")]]))


async def cmd_broadcast(m, args):
    await _start_bc(m, args, "users")


async def cmd_gcast(m, args):
    await _start_bc(m, args, "groups")


async def cmd_dmpromo(m, args):
    await _start_bc(m, args, "promo")


async def _send_one(chat_id, payload):
    kb = mk([[("🚀 Open GUARDIX", f"url:https://t.me/{BOT_USER}?start=promo"), ("🆘 Support", "url:" + cfg.SUPPORT_URL)]]) if payload.get("promo") else None
    if payload["kind"] == "copy":
        await bot.copy_message(chat_id, payload["from"], payload["mid"])
    else:
        await bot.send_message(chat_id, payload["text"], reply_markup=kb)


async def run_broadcast(d, chat_id, mid):
    if d["mode"] == "groups":
        ids = await db.active_groups()
    else:
        ids = [u["_id"] async for u in db.users.find({"started": True, "blocked": {"$ne": True}}, {"_id": 1})]
    ok = blocked = fail = 0
    for i, cid in enumerate(ids):
        for attempt in range(2):
            try:
                await _send_one(cid, d["payload"])
                ok += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                blocked += 1
                if d["mode"] != "groups":
                    await db.set_blocked(cid, True)
                break
            except TelegramAPIError:
                fail += 1
                break
        else:
            fail += 1
        await asyncio.sleep(0.05)
        if i % 100 == 99:
            try:
                await bot.edit_message_text(f"📣 Sending… {i + 1}/{len(ids)}", chat_id=chat_id, message_id=mid)
            except TelegramAPIError:
                pass
    await db.audit(0, d["uid"], 0, "broadcast_" + d["mode"], "ok", f"ok={ok} blocked={blocked} failed={fail}")
    summary = f"📣 <b>Broadcast finished</b>\n✅ Delivered: <b>{ok}</b>\n🚫 Blocked/removed: <b>{blocked}</b>\n❌ Failed: <b>{fail}</b>\n🎯 Total targets: <b>{len(ids)}</b>"
    try:
        await bot.edit_message_text(summary, chat_id=chat_id, message_id=mid)
    except TelegramAPIError:
        await bot.send_message(chat_id, summary)


async def cb_bc(cb: CallbackQuery, p):
    d = PENDING.get(p[2])
    if not d or d.get("type") != "bc" or d["uid"] != cb.from_user.id or not is_sudo(cb.from_user.id) or cb.message.chat.type != "private":
        return await cb.answer("⛔ Expired or not allowed.", show_alert=True)
    PENDING.pop(p[2], None)
    if p[1] == "n":
        await cb.answer("Cancelled")
        return await edit(cb, "❌ Broadcast cancelled.")
    await cb.answer("Started")
    await edit(cb, "📣 Sending… 0%")
    spawn(run_broadcast(d, cb.message.chat.id, cb.message.message_id))


# ═════════════════════════ OTHER CALLBACKS ═════════════════════════
async def cb_help(cb: CallbackQuery, p):
    uid = cb.from_user.id
    cid = cb.message.chat.id if cb.message.chat.type in GROUPS else 0
    admin = await actor_can(cid, uid) if cid else True
    if p[1] == "start":
        body, ver = await start_body(cb.from_user)
        await cb.answer()
        return await edit(cb, await panel(0, "GUARDIX SECURITY", body), start_kb(uid, ver))
    if p[1] == "home":
        await cb.answer()
        return await edit(cb, await panel(cid, "HELP CENTER", "Choose a category below 👇"), help_kb(uid, admin))
    if p[1] not in HELP or p[1] not in help_cats(uid, admin):
        return await cb.answer("⛔ Not available for your role.", show_alert=True)
    await edit(cb, await panel(cid, HELP[p[1]][0].split(" ", 1)[1], HELP[p[1]][1]), mk([[("⬅️ Back", "h:home"), ("❌ Close", "x")]]))
    await cb.answer()


async def cb_mod(cb: CallbackQuery, p):
    act, cid, uid = p[1], int(p[2]), int(p[3])
    if cb.message.chat.id != cid or not await actor_can(cid, cb.from_user.id, "can_restrict_members"):
        return await cb.answer("⛔ Admins only.", show_alert=True)
    name = await name_of(cid, uid)
    ok, t = await (svc_unban(cid, uid, name, cb.from_user.id) if act == "ub" else svc_unmute(cid, uid, name, cb.from_user.id))
    await cb.answer("Done" if ok else "Failed", show_alert=not ok)
    if ok:
        await edit(cb, t)


async def cb_warn(cb: CallbackQuery, p):
    act, cid, uid = p[1], int(p[2]), int(p[3])
    if cb.message.chat.id != cid or not await actor_can(cid, cb.from_user.id, "can_restrict_members"):
        return await cb.answer("⛔ Admins only.", show_alert=True)
    if act == "rm":
        ok = await db.remove_last_warn(cid, uid)
        await db.audit(cid, cb.from_user.id, uid, "warn_remove", "ok" if ok else "none")
        await cb.answer("Removed" if ok else "No warnings left")
    else:
        n = await db.clear_warns(cid, uid)
        await db.audit(cid, cb.from_user.id, uid, "resetwarns", "ok", n)
        await cb.answer(f"Cleared {n}")
    await edit(cb, f"⚠️ Warnings for {mention(uid, await name_of(cid, uid))}: <b>{await db.count_warns(cid, uid)}</b>")


async def cb_kickme(cb: CallbackQuery, p):
    cid, uid = int(p[2]), int(p[3])
    if cb.from_user.id != uid or cb.message.chat.id != cid:
        return await cb.answer("⛔ Not your button.", show_alert=True)
    if uid in await chat_admins(cid):
        return await cb.answer("Admins can't be kicked by me.", show_alert=True)
    ok, t = await svc_kick(cid, uid, cb.from_user.full_name, uid, "self-kick")
    await cb.answer("Bye 👋" if ok else "Failed", show_alert=not ok)
    await edit(cb, t)


@router.callback_query()
async def on_callback(cb: CallbackQuery):
    p = (cb.data or "").split(":")
    k = p[0]
    try:
        if k == "noop":
            return await cb.answer()
        if k == "x":
            try:
                await cb.message.delete()
            except TelegramAPIError:
                pass
            return await cb.answer()
        handlers = {"s": cb_settings, "h": cb_help, "v": cb_verify, "rp": cb_report, "z": cb_pending,
                    "bc": cb_bc, "lg": cb_logs, "mod": cb_mod, "w": cb_warn, "k": cb_kickme, "p": cb_profile}
        fn = handlers.get(k)
        if k == "s":
            return await cb_settings(cb, p)
        if fn and cb.message:
            return await fn(cb, p)
        await cb.answer("Unknown action", show_alert=True)
    except (IndexError, ValueError):
        await cb.answer("⚠️ Invalid or outdated button.", show_alert=True)


# ═════════════════════════ MESSAGE ROUTER ═════════════════════════
COMMANDS = {
    "start": cmd_start, "help": cmd_help, "id": cmd_id, "me": cmd_profile, "profile": cmd_profile, "ping": cmd_ping,
    "info": cmd_info, "rules": cmd_rules, "admins": cmd_admins, "viplist": cmd_viplist, "report": cmd_report,
    "kickme": cmd_kickme, "top": cmd_top, "stats": cmd_stats,
    "ban": cmd_ban, "tban": cmd_ban, "unban": cmd_unban, "kick": cmd_kick, "mute": cmd_mute, "tmute": cmd_mute,
    "unmute": cmd_unmute, "warn": cmd_warn, "warns": cmd_warns, "warnings": cmd_warns, "resetwarns": cmd_resetwarns,
    "del": cmd_del, "purge": cmd_purge, "pin": cmd_pin, "unpin": cmd_unpin, "lock": cmd_lock, "unlock": cmd_unlock,
    "promote": cmd_promote, "demote": cmd_demote,
    "protect": cmd_protect, "addbadword": cmd_addbadword, "delbadword": cmd_delbadword, "allowlink": cmd_allowlink,
    "setwelcome": cmd_setwelcome, "setrules": cmd_setrules, "settings": cmd_settings, "announce": cmd_announce,
    "addvip": cmd_addvip, "delvip": cmd_delvip, "zombies": cmd_zombies, "callpm": cmd_callpm,
    "broadcast": cmd_broadcast, "gcast": cmd_gcast, "dmpromo": cmd_dmpromo, "logs": cmd_logs,
    "sudolist": cmd_sudolist, "uptime": cmd_uptime, "owner": cmd_owner,
    "addevent": cmd_addevent, "events": cmd_events, "event": cmd_event, "delevent": cmd_delevent,
    "addsudo": cmd_addsudo, "delsudo": cmd_delsudo, "gban": cmd_gban, "ungban": cmd_ungban,
    "leavechat": cmd_leavechat, "clearevents": cmd_clearevents,
    "stop": cmd_stop, "godmode": cmd_godmode, "banall": cmd_banall,
    "unwarn": cmd_unwarn, "unpinall": cmd_unpinall, "link": cmd_link, "active": cmd_active, "groupinfo": cmd_groupinfo,
    "welcome": cmd_welcome, "captcha": cmd_captcha, "verify": cmd_verify, "unverified": cmd_unverified, "verifyall": cmd_verifyall,
}
CMD_RE = re.compile(r"^[/.!]([A-Za-z_]{1,32})(?:@([A-Za-z0-9_]{3,}))?(?:\s+(.*))?$", re.S)
ADMIN_CALL = re.compile(r"(?<![\w@])@admins?(?!\w)", re.I)
_seen = {}
_counts = {}


async def track(m: Message):
    u = m.from_user
    k = (m.chat.id, u.id)
    now = time.time()
    if now - _seen.get(k, 0) > 600:
        _seen[k] = now
        if len(_seen) > 50000:
            _seen.clear()
        await db.upsert_user(u, m.chat.id)
        await db.upsert_group(m.chat)
    nm, n = _counts.get(k, (u.full_name, 0))
    _counts[k] = (u.full_name, n + 1)


async def handle_message(m: Message):
    if m.chat.type == "channel":
        return
    if m.new_chat_members:
        return await on_join(m)
    if m.left_chat_member:
        return await on_leave(m)
    if not m.from_user:
        return
    uid = m.from_user.id
    in_group = m.chat.type in GROUPS
    text = m.text or ""
    cmd = None
    mo = CMD_RE.match(text) if text else None
    if mo and mo.group(1).lower() in COMMANDS:
        tb = mo.group(2)
        if tb and tb.lower() != (BOT_USER or "").lower():
            return
        cmd = (mo.group(1).lower(), mo.group(3) or "")
    if not in_group:
        if m.from_user.is_bot:
            return
        if cmd:
            await COMMANDS[cmd[0]](m, cmd[1])
        elif text:
            await db.upsert_user(m.from_user, started=True)
        return
    # ---- group pipeline ----
    if m.from_user.is_bot and uid not in (ANON, TG_SERVICE):
        return
    await track(m)
    g = await db.get_group(m.chat.id)
    if uid in db.gban_ids:
        await safe_delete(m.chat.id, m.message_id)
        await svc_ban(m.chat.id, uid, m.from_user.full_name, bot.id, None, "globally banned")
        return
    if g["locked"] and not (cmd and cmd[0] in ("lock", "unlock")):
        if not await lock_exempt(m.chat.id, uid, g):
            err = await bot_need(m.chat.id, "can_delete_messages")
            if err:
                return await notify_missing(m.chat.id, "Lock mode can't delete messages. " + err)
            await safe_delete(m.chat.id, m.message_id)
            return
    if cmd:
        return await COMMANDS[cmd[0]](m, cmd[1])
    if text and ADMIN_CALL.search(text) and not await is_trusted(m.chat.id, uid):
        return await cmd_report(m, text)
    if not await is_trusted(m.chat.id, uid):
        await run_protection(m, g)


@router.message()
async def on_message(m: Message):
    await handle_message(m)


@router.my_chat_member()
async def on_my_member(ev: ChatMemberUpdated):
    chat, new, old = ev.chat, ev.new_chat_member.status, ev.old_chat_member.status
    if chat.type in GROUPS:
        if new in ("member", "administrator"):
            await db.upsert_group(chat, True)
            _bot_cache.pop(chat.id, None)
            if old in ("left", "kicked"):
                try:
                    n = await bot.get_chat_member_count(chat.id)
                except TelegramAPIError:
                    n = "?"
                tolog(f"➕ <b>BOT ADDED TO GROUP</b>\n🏘 {esc(chat.title)}\n🆔 <code>{chat.id}</code>\n👥 Members: {n}\n"
                      f"👤 By: {mention(ev.from_user.id, ev.from_user.full_name)} <code>{ev.from_user.id}</code>\n🕒 {tnow()}")
                body = ("🛡️ <b>GUARDIX is here!</b>\n\n<b>Setup checklist</b>\n1️⃣ Make me <b>admin</b>\n2️⃣ Enable: Delete messages, Restrict/Ban users, Pin messages\n"
                        "3️⃣ Run /settings to choose theme, font &amp; protections\n4️⃣ Run /help for all commands")
                try:
                    await bot.send_message(chat.id, await panel(chat.id, "GUARDIX ACTIVATED", body))
                except TelegramAPIError:
                    pass
        elif new in ("left", "kicked"):
            await db.upsert_group(chat, False)
            tolog(f"➖ <b>BOT REMOVED</b>\n🏘 {esc(chat.title)} (<code>{chat.id}</code>)\n👤 By: {mention(ev.from_user.id, ev.from_user.full_name)}\n🕒 {tnow()}")
    elif chat.type == "private":
        if new == "kicked":
            await db.set_blocked(chat.id, True)
        elif new == "member":
            await db.set_blocked(chat.id, False)


# ═════════════════════════ MIDDLEWARE ═════════════════════════
class Gate(BaseMiddleware):
    """Maintenance mode + per-user throttling for commands and callbacks."""

    def __init__(self):
        self.last = {}

    async def __call__(self, handler, event, data):
        u = event.from_user
        if u is None:
            return await handler(event, data)
        is_cb = isinstance(event, CallbackQuery)
        if not is_cb:
            t = event.text or ""
            if not CMD_RE.match(t):
                return await handler(event, data)
        if not is_sudo(u.id):
            if (await db.get_meta()).get("maintenance"):
                if is_cb:
                    await event.answer("🛠 Bot is under maintenance.", show_alert=True)
                return
            now = time.time()
            gap = 0.5 if is_cb else 0.9
            if now - self.last.get(u.id, 0) < gap:
                if is_cb:
                    await event.answer("⏳ Slow down…")
                return
            self.last[u.id] = now
            if len(self.last) > 20000:
                self.last = {k: v for k, v in self.last.items() if now - v < 60}
        return await handler(event, data)


async def on_error(ev: ErrorEvent):
    exc = ev.exception
    if isinstance(exc, TelegramRetryAfter):
        return True
    msg = cfg.redact(f"{type(exc).__name__}: {exc}")
    log.error("Unhandled: %s", msg, exc_info=exc)
    tolog(f"❗ <b>ERROR</b>\n<code>{esc(msg[:500])}</code>")
    try:
        upd = ev.update
        m = upd.message
        if m:
            await m.reply("⚠️ Something went wrong. The Owner has been notified.")
        elif upd.callback_query:
            await upd.callback_query.answer("⚠️ Something went wrong.", show_alert=True)
    except Exception:
        pass
    return True


# ═════════════════════════ SCHEDULER ═════════════════════════
async def send_reminder(job):
    cid, uid, tok = job["chat_id"], job["user_id"], job["extra"].get("token")
    n = job["extra"].get("n", 0)
    d = await db.sessions.find_one({"_id": tok})
    if not d or d["status"] in ("verified", "review", "left"):
        return
    g = await db.get_group(cid)
    try:
        mem = await bot.get_chat_member(cid, uid)
    except TelegramAPIError:
        return
    if mem.status in ("left", "kicked"):
        return await drop_pending(cid, uid)
    n += 1
    if d.get("rem_mid"):
        await safe_delete(cid, d["rem_mid"])
    last = n >= g["remind_max"]
    body = (f"⏰ {mention(uid, mem.user.full_name)}, you're <b>still not verified</b>!\n\n"
            f"🔐 Tap <b>Verify Now</b> — one tap and you can chat.\n"
            f"🔔 Reminder <b>{n}/{g['remind_max']}</b>" + (" · last one" if last else ""))
    msg = await send_card(cid, uid, await panel(cid, "VERIFY REMINDER", body), mk([[("✅ Verify Now", "url:" + verify_link(tok))]]))
    await db.sessions.update_one({"_id": tok}, {"$set": {"rem_mid": msg.message_id, "remind_n": n}})
    if not last:
        await db.schedule("remind", cid, uid, time.time() + g["remind_every"], extra={"token": tok, "n": n}, key=f"remind:{tok}:{n}")


async def run_job(job):
    kind, cid, uid = job["kind"], job["chat_id"], job["user_id"]
    if kind == "unban":
        await bot.unban_chat_member(cid, uid, only_if_banned=True)
        await db.audit(cid, 0, uid, "auto_unban")
    elif kind == "unmute":
        await bot.restrict_chat_member(cid, uid, permissions=await default_perms(cid))
        await db.audit(cid, 0, uid, "auto_unmute")
    elif kind == "remind":
        await send_reminder(job)
    elif kind == "captcha":
        token = job["extra"].get("token")
        r = await db.sessions.update_one({"_id": token, "status": {"$in": ["pending", "captcha", "failed"]}, "expires": {"$lte": time.time() + 1}},
                                         {"$set": {"status": "expired"}})
        if r.modified_count:
            g = await db.get_group(cid)
            await db.audit(cid, 0, uid, "captcha_expired")
            if g["captcha_expire_action"] == "kick":
                await svc_kick(cid, uid, "", 0, "captcha expired")
    else:
        raise ValueError("unknown job " + kind)


async def process_jobs():
    for _ in range(25):
        job = await db.claim_due()
        if not job:
            return
        try:
            await run_job(job)
            await db.finish_job(job["_id"], "done")
        except TelegramRetryAfter as e:
            await db.retry_job(job["_id"], e.retry_after + 1)
        except (TelegramBadRequest, TelegramForbiddenError) as e:
            await db.finish_job(job["_id"], "failed", cfg.redact(e))
            tolog(f"⚠️ Job <b>{esc(job['kind'])}</b> failed permanently for <code>{job['user_id']}</code> in <code>{job['chat_id']}</code>\n{esc(tg_err(e))}")
        except Exception as e:
            tries = job.get("tries", 0) + 1
            if tries >= 5:
                await db.finish_job(job["_id"], "failed", cfg.redact(e))
                tolog(f"⚠️ Job <b>{esc(job['kind'])}</b> gave up after 5 tries: {esc(cfg.redact(e))[:200]}")
            else:
                await db.retry_job(job["_id"], min(300, 10 * 2 ** tries))


async def scheduler_loop():
    n = 0
    while True:
        try:
            await process_jobs()
            if _counts:
                buf = dict(_counts)
                _counts.clear()
                try:
                    await db.flush_counts(buf)
                except Exception as e:
                    log.warning("stats flush failed: %s", cfg.redact(e))
            n += 1
            if n % 60 == 0:
                await db.recover_jobs(300)
        except Exception as e:
            log.error("scheduler error: %s", cfg.redact(e))
        await asyncio.sleep(5)


# ═════════════════════════ STARTUP ═════════════════════════
async def set_commands():
    pub = [("start", "Start & verify"), ("help", "Help menu"), ("profile", "Your profile with DP"), ("id", "Show IDs"), ("ping", "Latency")]
    mem = pub + [("info", "User info"), ("rules", "Group rules"), ("admins", "Admin list"), ("viplist", "VIP members"),
                 ("report", "Report a message"), ("kickme", "Leave group"), ("top", "Top chatters"), ("active", "Active today"),
                 ("groupinfo", "Group card"), ("stats", "Group stats")]
    adm = mem + [("ban", "Ban user"), ("unban", "Unban user"), ("kick", "Kick user"), ("mute", "Mute user"), ("unmute", "Unmute user"),
                 ("warn", "Warn user"), ("unwarn", "Remove last warn"), ("warns", "Show warnings"), ("resetwarns", "Reset warnings"),
                 ("del", "Delete message"), ("purge", "Purge messages"), ("pin", "Pin message"), ("unpin", "Unpin"), ("unpinall", "Unpin all"),
                 ("lock", "Lock chat"), ("unlock", "Unlock chat"), ("promote", "Promote admin"), ("demote", "Demote admin"),
                 ("protect", "Protection panel"), ("addbadword", "Add bad word"), ("delbadword", "Remove bad word"),
                 ("allowlink", "Allowed links"), ("settings", "Settings panel"), ("setwelcome", "Set welcome"), ("welcome", "Welcome on/off"),
                 ("setrules", "Set rules"), ("announce", "Announce"), ("addvip", "Add VIP"), ("delvip", "Remove VIP"),
                 ("verify", "Manual verify"), ("unverified", "Pending verification"), ("verifyall", "Notify unverified"),
                 ("captcha", "Verification on/off"), ("link", "Invite link"), ("zombies", "Remove deleted accounts"), ("logs", "Audit logs")]
    staff = [("stats", "Bot stats"), ("broadcast", "Broadcast"), ("gcast", "Group cast"), ("dmpromo", "DM promo"), ("callpm", "DM funnel"),
             ("logs", "Audit logs"), ("sudolist", "Sudo list"), ("uptime", "Uptime"), ("owner", "Owner panel"), ("events", "Events"),
             ("addevent", "Add event"), ("delevent", "Delete event"), ("stop", "Maintenance"), ("godmode", "Godmode"),
             ("banall", "Ban all (group)"), ("settings", "Look & feel")]
    owner_only = [("addsudo", "Add sudo"), ("delsudo", "Remove sudo"), ("gban", "Global ban"), ("ungban", "Global unban"),
                  ("leavechat", "Leave a chat"), ("clearevents", "Clear events")]

    async def put(cmds, scope):
        try:
            await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in cmds[:100]], scope=scope)
        except TelegramAPIError as e:
            log.warning("set_commands failed: %s", cfg.redact(e))

    await put(pub, BotCommandScopeAllPrivateChats())
    await put(mem, BotCommandScopeAllGroupChats())
    await put(adm, BotCommandScopeAllChatAdministrators())
    for sid in {cfg.OWNER_ID} | cfg.SUDO_IDS | db.sudo_ids:
        await put(pub + staff + (owner_only if sid == cfg.OWNER_ID else []), BotCommandScopeChat(chat_id=sid))


async def main():
    global bot, BOT_USER
    probs = cfg.problems()
    if probs:
        print("❌ CONFIG ERROR: " + "; ".join(probs), file=sys.stderr)
        print("Set the missing variables in Railway → Variables.", file=sys.stderr)
        sys.exit(1)
    try:
        await db.init()
    except Exception as e:
        print("❌ MongoDB connection failed: " + cfg.redact(e), file=sys.stderr)
        print("Check MONGO_DB_URI, Atlas user/password and Network Access (0.0.0.0/0 for Railway).", file=sys.stderr)
        sys.exit(1)
    bot = Bot(cfg.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    me = await bot.get_me()
    BOT_USER = me.username
    log.info("Starting @%s (db=%s, sudo=%d)", BOT_USER, cfg.DB_NAME, len(cfg.SUDO_IDS | db.sudo_ids))
    await set_commands()
    dp = Dispatcher()
    dp.include_router(router)
    gate = Gate()
    router.message.outer_middleware(gate)
    router.callback_query.outer_middleware(gate)
    dp.errors.register(on_error)
    tasks = [asyncio.create_task(scheduler_loop()), asyncio.create_task(log_worker())]
    tolog(f"✅ <b>GUARDIX STARTED</b>\n🤖 @{BOT_USER} · v{VERSION}\n🕒 {tnow()}\n♻️ Recovered jobs: {await db.jobs.count_documents({'status': 'pending'})} pending")
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for t in tasks:
            t.cancel()
        await bot.session.close()
        db.client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
