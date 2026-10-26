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
from aiogram.types import (BotCommand, BufferedInputFile, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats,
                           BotCommandScopeAllChatAdministrators, BotCommandScopeChat,
                           CallbackQuery, ChatJoinRequest, ChatMemberUpdated, ChatPermissions, ErrorEvent,
                           InlineKeyboardButton, Message)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core import (Config, DB, FONTS, THEMES, esc, fmt_secs, fx, make_panel, mention, normalize,
                  parse_duration, utc)
from lines import pick_line
from words import builtin_has, canon, find_bad

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
VERSION = "3.0"

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


# ═════════════════════════ FUN LINES / SMALL UTILS ═════════════════════════
BAN_LINES = ["😂 Tera time pura ho gaya, nikal!", "🚪 Bhai, exit udhar hai!", "🤡 Aaj ka joker mil gaya!",
             "🚀 Bhai, doosre planet par jao!", "🧳 Bag pack karo, safar yahin tak tha!", "🎬 The End. Credits roll ho rahe hain!",
             "🛫 Next flight: Ghar wapas. Bye bye!", "🥳 Party khatam, ab ghar chalo!"]
MUTE_LINES = ["🎤 Bhai ka mic maintenance par hai! 😂", "🤐 Ab zip lag gayi, shaant raho!", "🔇 Thodi der ke liye silent mode ON!",
              "🧘 Chup raho, meditation time hai!", "📵 Network error: Zubaan not found!", "🍿 Ab sirf dekhne ka time hai!"]
UNBAN_LINES = ["🎉 Bhai ki ghar wapsi ho gayi!", "🏠 Welcome back, yahi to tera ghar hai!", "🥳 Mafi mil gayi, ab behave karna!",
               "🔓 Darwaza khul gaya, andar aa ja!"]
UNMUTE_LINES = ["🎤 Mic wapas chalu! Ab aaram se bol bhai 😂", "🔊 Awaaz wapas aa gayi, ab dhyaan se bolna!",
                "🗣 Zip khul gayi, ab bol de jo bolna hai!", "📢 Aap ab live hain!"]
SPAM_CALLOUTS = ["🚨 {user} — Admin/Moderator hoke group mein spam? 💀 Ye zimmedari hai ya timepass?",
                 "😳 {user}, tum to staff ho! Spam karoge to baaki members kya seekhenge? 💀",
                 "🧐 {user} ne spam kiya? Ek admin se ye umeed nahi thi! 💀",
                 "🛑 {user}, rule sab ke liye same hain — admin ho ya member. Spam band karo! 💀",
                 "🤦 Admin ban gaye, par spam ki aadat nahi gayi {user}! 💀"]

TAG_LINES = [
    "kahan busy ho? yahan bhi aao! 👀", "gayab ho gaye kya? group yaad kar raha hai 😂", "arre bhai, chai thandi ho rahi hai, aa jao ☕",
    "itni der se kahan the? sab pooch rahe hain 🕵️", "ek baar hello bol do, hum bhi khush ho jayenge 🥹", "online aao, offline to sab hain 😎",
    "tumhare bina group adhoora hai 💔", "neend se jaago, group bula raha hai ⏰", "mobile rakh do aur yahan aa jao 📱",
    "seen karke bhaag gaye the kya? 🏃", "aaj kya khaas? kuch to bolo! 🎤", "padhai se break lo, group mein aao 📚",
    "ghar ke kaam baad mein, pehle yahan aao 🧹", "kahan chhupe ho? hum dhundh rahe hain 🔍", "message to padh lo, ignore mat karo 😤",
    "badi mehfil hai, tumhari kami hai 🎉", "aa bhi jao ab, itna kya sharmana 😊", "reel baad mein, group pehle 🎬",
    "tumhare memes ka intezaar hai 🤣", "good vibes leke aao, group wait kar raha hai ✨", "chat mein zinda ho ya nahi? 🫠",
    "wifi on kar lo, group mein aao 📶", "kuch bolo to sahi, ye group tumhara bhi hai 🏠", "aaj ka attendance lagao! 🙋",
    "sab aa gaye, bas tum bache ho 🚪", "bahut busy ho gaye ho, hum yaad karte hain 🥲", "thoda time nikalo, group ko bhi do ⏳",
    "tum nahi aaoge to hum chale jayenge (mazaak) 😜", "yahan bhi aao, wahan bhi hum hi hain 😅", "ghoomne gaye the kya? wapas aa jao 🧳",
    "khaana khaya? ab yahan aao 🍛", "kitaab band, mobile on, group mein aao 📖", "arre yaar, ek reply to banta hai 🙏",
    "tumhari entry ki shaan alag hai, aao! 🌟", "duniya bahut badi hai, par group yahin hai 🌍", "bula rahe hain pyaar se, aa jao 💌",
    "chaliye ab to muskura do 😄", "raat ho ya din, group hamesha khula hai 🌙", "time pass karna hai? yahin aao 🎲",
    "tumse milne ko dil kar raha hai (group mein) 🤗", "sab pooch rahe hain: wo kahan hai? 🤔", "jaldi aao, mazaa miss ho raha hai 🎈",
    "tension nahi, bas ek hi message chahiye 📩", "tum jaise dost kahan milte hain, aao! 💎", "chai ready hai, tum ready ho? ☕",
    "aaj mood kaisa hai? batao zara 🎭", "lo, tag kar diya! ab to bolna padega 😈", "bahut sharif ban rahe ho, bolo kuch 😏",
    "group ki jaan ho tum, aao to sahi! ❤️", "ek smile bhejo aur nikal jao 😁", "hum yahan, tum kahan? 🧭",
    "dost, tumhari yaad aa rahi thi 🥰", "aao baat karein, sab mast hain 🎵", "alarm nahi, group bula raha hai 🔔",
    "kitni der se wait kar rahe hain! ⌛", "tag hua hai to reply banta hai 📝", "aaj to aana hi padega 🤝",
    "tumhare bina ye group sunsaan hai 🏜️", "dil se bula rahe hain, mana mat karna 💛", "kuch bhi likho, bas aao! ✍️",
]
LINES_DEFAULT = {"ban": BAN_LINES, "mute": MUTE_LINES, "unban": UNBAN_LINES, "unmute": UNMUTE_LINES,
                 "callout": SPAM_CALLOUTS, "tag": TAG_LINES}
LINE_TITLES = {"ban": "🔨 Ban lines", "mute": "🔇 Mute lines", "unban": "♻️ Unban lines", "unmute": "🔊 Unmute lines",
               "callout": "💀 Admin-spam callouts", "tag": "📣 /all funny lines"}
_custom_lines = {}


def rline(cat):
    return random.choice(LINES_DEFAULT[cat] + _custom_lines.get(cat, []))


_deleted_mids = set()
_grp_link_cache = {}
tl = None  # optional Telethon (MTProto) client for member sync


def verify_url(chat_id):
    return f"https://t.me/{BOT_USER}?start=v_{abs(chat_id)}"


async def group_link(chat_id):
    if chat_id in _grp_link_cache:
        return _grp_link_cache[chat_id]
    link = None
    try:
        ch = await bot.get_chat(chat_id)
        if ch.username:
            link = f"https://t.me/{ch.username}"
        elif getattr(ch, "invite_link", None):
            link = ch.invite_link
    except TelegramAPIError:
        pass
    if not link and str(chat_id).startswith("-100"):
        link = f"https://t.me/c/{str(chat_id)[4:]}/1"
    if link:
        _grp_link_cache[chat_id] = link
    return link


async def group_owner(chat_id):
    for uid, a in (await chat_admins(chat_id)).items():
        if a.status == "creator":
            return uid
    return 0


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
    """Server-side check: Telegram admin rights, or Owner / Sudo (always allowed)."""
    if is_sudo(uid):
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
async def _tell_banned(chat_id, uid):
    ud = await db.get_user(uid) or {}
    if not ud.get("started") or ud.get("blocked"):
        return
    try:
        title = (await db.get_group(chat_id))["_title"]
        await bot.send_message(uid, f"🚫 You were banned from <b>{esc(title)}</b>.\nIf you think it was a mistake, send /appeal here.")
    except TelegramAPIError:
        pass


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
    await db.note_ban(chat_id, uid, actor)
    if actor != bot.id:
        spawn(_tell_banned(chat_id, uid))
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
    await db.audit(chat_id, actor, uid, "unmute", "ok")
    return True, await mod_card(chat_id, "UNMUTED", uid, name, actor, [("🔊", "Action", "<b>Unmuted</b>"), ("🎤", "Message", rline("unmute"))], "", raw)


async def svc_unban(chat_id, uid, name, actor, raw=False):
    try:
        await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
    except TelegramAPIError as e:
        return False, "❌ " + tg_err(e)
    await db.cancel_job("unban", chat_id, uid)
    await db.audit(chat_id, actor, uid, "unban", "ok")
    await db.clear_ban(chat_id, uid)
    return True, await mod_card(chat_id, "UNBANNED", uid, name, actor, [("♻️", "Action", "<b>Unbanned</b>"), ("🎉", "Message", rline("unban"))], "", raw)


async def svc_kick(chat_id, uid, name, actor, reason="", raw=False):
    try:
        await bot.ban_chat_member(chat_id, uid)
        await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
    except TelegramAPIError as e:
        return False, "❌ " + tg_err(e)
    await db.audit(chat_id, actor, uid, "kick", "ok", reason)
    return True, await mod_card(chat_id, "KICKED", uid, name, actor, [("👢", "Action", "<b>Kicked</b>")], reason, raw)


_warn_lock = {}


async def issue_warn(chat_id, uid, name, actor, reason):
    key = (chat_id, uid)
    now = time.time()
    if now - _warn_lock.get(key, 0) < 2:  # duplicate processing guard
        return (await panel(chat_id, "WARNING", kv("⏳", "Duplicate", "Same warning ignored (already processed)")),
                await db.count_warns(chat_id, uid))
    _warn_lock[key] = now
    if len(_warn_lock) > 5000:
        _warn_lock.clear()
    g = await db.get_group(chat_id)
    n = await db.add_warn(chat_id, uid, actor, reason or "no reason")
    limit = g["warn_limit"]
    await db.audit(chat_id, actor, uid, "warn", "ok", f"{n}/{limit} {reason}")
    bar = "🟥" * min(n, limit) + "⬜" * max(0, limit - n)
    extra = [("⚠️", "Warning", f"<b>{n}/{limit}</b>  {bar}"), ("⏳", "Remaining", f"<b>{max(0, limit - n)}</b>"),
             ("📊", "Total active", f"<b>{n}</b>")]
    if limit > 1 and n == limit - 1:
        extra.append(("🚨", "Notice", "<b>FINAL WARNING</b> — next one = punishment"))
    body = await mod_card(chat_id, "WARNING", uid, name, actor, extra, reason, raw=True)
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
            done = {"ban": "banned", "kick": "kicked"}.get(act, f"muted for {fmt_secs(g['warn_mute_secs'])}")
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
    "pub": ("🌐 Members", "/start — welcome, verify &amp; start\n/help — this menu\n/profile — your profile with DP (also /me)\n/id — IDs\n/ping — speed &amp; uptime\n"
            "/info [user] — anyone's info\n/rules — group rules\n/admins — owner, admins &amp; VIPs\n/report (reply) or @admin — call staff\n"
            "/kickme — leave the group\n/active — today's active (button: all-time)\n/groupinfo — group card &amp; stats\n/appeal — ask for an unban (DM)"),
    "mod": ("🔨 Moderation (Admins)", "/ban · /unban · /kick — [reply|id|@user] [reason]\n/tban 1d — timed ban\n/mute [10m|1h|1d] · /unmute\n"
            "/warn · /warns — buttons: remove last / reset\n/del (reply)\n/purge (reply) · /purge 10|50|100\n/pin · /unpin · /unpin all\n"
            "/lock · /unlock\n/promote — pick rights with buttons · /demote\n/zombies — remove deleted accounts\n/sync — fetch all members"),
    "prot": ("🛡 Protection (always ON)", "Spam, same-message, flood, raids, links and abusive words are <b>automatic</b> (edited messages too).\n"
             "🔗 Links get ✅ Approve / 🚫 Reject buttons.\n\n/addbadword word1 word2\n/delbadword word\n/allowlink add|remove|list domain\n"
             "/verify on|off — tag unverified members\n/check — bot rights &amp; health"),
    "set": ("⚙️ Setup (Admins)", "/all — tag everyone 1 by 1 · /all text — N per message · /stopall\n"
            "/filter name (reply) — auto reply · /filters · /stop name\n/settings — lock, warnings, welcome, tag batch\n"
            "/setwelcome text — {name} {mention} {group} {id}\n/setrules text\n/addvip [user] [time] · /delvip\n/link — invite link\n/logs — audit log"),
    "sudo": ("👑 Owner + Sudo (DM)", "/owner — full control panel (buttons)\n/broadcast · /gcast — send now\n/gift — giveaway panel\n"
             "/godmode — in a group: silently gives you admin\n/banall yes — emergency ban of all non-admins"),
    "own": ("🔒 Owner Only (DM)", "/cmds [chat_id] — every command ON/OFF + who may use it\n/lines — your own funny lines\n/verifyall on|off\n"
            "/look [chat_id] — 10 themes, 20 fonts\n/sudo add|del|list\n/gban · /ungban\n/backup — settings file"),
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
    rows = [[("🔐 My Verification", "v:menu"), ("👤 My Profile", "p:me")],
            [("📖 Help", "h:home"), ("🌅 Daily Shayari", "dl:t")],
            [("🆘 Support", "url:" + cfg.SUPPORT_URL)]]
    if BOT_USER:
        rows.append([("➕ Add me to your group", f"url:https://t.me/{BOT_USER}?startgroup=true&admin=delete_messages+restrict_members+ban_users+pin_messages+invite_users+promote_members")])
    return mk(rows)


async def start_body(u):
    nver = len(await db.verified_chats(u.id))
    lines = [kv("👤", "Name", mention(u.id, u.full_name)), kv("🆔", "ID", f"<code>{u.id}</code>"),
             kv("🔗", "Username", ("@" + esc(u.username)) if u.username else "—"),
             kv("💎", "Telegram Premium", "Yes ✨" if getattr(u, "is_premium", False) else "No"),
             kv("🛡", "Status", f"🟢 Verified in {nver} group(s)" if nver else "⚪ Not verified yet")]
    body = ("🛡️ <b>Welcome to GUARDIX</b>\n<i>Premium group security assistant</i>\n\n" + "\n".join(lines)
            + "\n\n⚡ Choose an option below 👇")
    return body, bool(nver)


async def cmd_start(m: Message, args):
    if m.chat.type != "private":
        kb = mk([[("🚀 Open in DM", f"url:https://t.me/{BOT_USER}?start=hi")]])
        return await say(m, await panel(m.chat.id, "GUARDIX", "🛡️ Your group security assistant is <b>active</b>.\nOpen me in DM to verify &amp; see your status."), kb)
    u = m.from_user
    src = "DM /start" + (f" ({args[:20]})" if args and not args.startswith("v_") else "")
    new = await db.upsert_user(u, started=True, source=src)
    if new:
        await log_new_user(u, src)
    if args.startswith("v_") and args[2:].isdigit():
        return await do_verify(u, -int(args[2:]))
    if args == "gift":
        return await gift_join(m)
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
        if await db.is_verified(cid, uid):
            ver = "🟢 Verified ✅"
        elif await is_trusted(cid, uid):
            ver = "🟢 Trusted"
        else:
            ver = "⚪ Not verified"
        lines.append(kv("🔐", "Verification", ver))
        st = await db.stats.find_one({"_id": f"{cid}:{uid}"})
        g = await db.get_group(cid)
        lines.append(kv("💬", "Messages here", f"<b>{(st or {}).get('count', 0)}</b>"))
        lines.append(kv("⚠️", "Warnings", f"<b>{await db.count_warns(cid, uid)}/{g['warn_limit']}</b>"))
    else:
        nver = len(await db.verified_chats(uid))
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
    """Anyone can look up any member: /info [reply|id|@user]"""
    if not await need_group(m):
        return
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        uid, name = m.from_user.id, m.from_user.full_name
    cid = m.chat.id
    body = await profile_body(cid, uid, name)
    try:
        mem = await bot.get_chat_member(cid, uid)
        body += "\n" + kv("📌", "Telegram status", mem.status)
    except TelegramAPIError:
        pass
    if uid in db.gban_ids and await actor_can(cid, m.from_user.id):
        body += "\n" + kv("🚫", "Global ban", "Yes")
    await say_card(m, uid, await panel(cid, "USER INFO", body))


async def cmd_rules(m: Message, args):
    if not await need_group(m):
        return
    g = await db.get_group(m.chat.id)
    txt = esc(g["rules"]) if g["rules"] else "No rules set yet. Admins: use /setrules."
    await say(m, await panel(m.chat.id, "GROUP RULES", txt))


async def cmd_admins(m: Message, args):
    if not await need_group(m):
        return
    cid = m.chat.id
    ad = await chat_admins(cid, force=True)
    owner = [a for a in ad.values() if a.status == "creator" and not a.user.is_bot]
    staff = [a for a in ad.values() if a.status != "creator" and not a.user.is_bot]
    lines = []
    for a in owner:
        lines.append(f"👑 {mention(a.user.id, a.user.full_name)} — <i>Owner</i>")
    if staff:
        lines.append("")
        for a in staff:
            t = getattr(a, "custom_title", None)
            lines.append(f"🛡 {mention(a.user.id, a.user.full_name)}" + (f" — <i>{esc(t)}</i>" if t else ""))
    vips = await db.list_vips(cid)
    if vips:
        lines.append("")
        for v in vips:
            lines.append(f"💎 {mention(v['user_id'], await name_of(cid, v['user_id']))}"
                         + (f" — {fmt_secs(v['until'] - time.time())} left" if v.get("until") else ""))
    await say(m, await panel(cid, "TEAM", "\n".join(lines).strip() or "No admins found."))


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
    cid, uid = m.chat.id, m.from_user.id
    if uid in await chat_admins(cid) or is_sudo(uid):
        return await say(m, "ℹ️ Admins can't be removed by me — please leave manually.")
    ok, t = await svc_kick(cid, uid, m.from_user.full_name, uid, "left with /kickme")
    await bot.send_message(cid, t if ok else "❌ Couldn't remove you — I may lack the Ban right.")


async def cmd_top(m: Message, args):
    if not await need_group(m):
        return
    rows = await db.top(m.chat.id)
    vs = await db.verified_among(m.chat.id, [r["user_id"] for r in rows])
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = [f"{medals[i]} {esc(r.get('name'))}{' ✅' if r['user_id'] in vs else ''} — <b>{r['count']}</b>" for i, r in enumerate(rows)]
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
    r = await _target_flow(m, args, "can_restrict_members", "Usage: /ban [reply|id|@user] [reason]\nTimed: /tban [user] 1d [reason]")
    if not r:
        return
    uid, name, reason = r
    reason = reason.strip() or rline("ban")
    ok, t = await svc_ban(m.chat.id, uid, name, m.from_user.id, None, reason)
    await say_card(m, uid, t, mk([[("♻️ Unban", f"mod:ub:{m.chat.id}:{uid}")]])) if ok else await say(m, t)


async def cmd_tban(m, args):
    r = await _target_flow(m, args, "can_restrict_members", "Usage: /tban [reply|id|@user] 1d [reason]")
    if not r:
        return
    uid, name, rest = r
    secs, reason = split_dur(rest)
    if not secs:
        return await say(m, "Usage: /tban [reply|id|@user] <b>1d</b> [reason]   (10m, 2h, 1d, 1w)")
    reason = reason.strip() or rline("ban")
    ok, t = await svc_ban(m.chat.id, uid, name, m.from_user.id, secs, reason)
    await say_card(m, uid, t, mk([[("♻️ Unban", f"mod:ub:{m.chat.id}:{uid}")]])) if ok else await say(m, t)


async def cmd_mute(m, args):
    r = await _target_flow(m, args, "can_restrict_members", "Usage: /mute [reply|id|@user] [10m|1h|1d] [reason]")
    if not r:
        return
    uid, name, rest = r
    secs, reason = split_dur(rest)
    reason = reason.strip() or rline("mute")
    ok, t = await svc_mute(m.chat.id, uid, name, m.from_user.id, secs, reason)
    await say_card(m, uid, t, mk([[("🔊 Unmute", f"mod:um:{m.chat.id}:{uid}")]])) if ok else await say(m, t)


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
    text, n = await issue_warn(m.chat.id, uid, name, m.from_user.id, reason.strip())
    await say_card(m, uid, text, mk([[("📋 Warnings", f"w:rf:{m.chat.id}:{uid}"), ("🗑 Remove last", f"w:rm:{m.chat.id}:{uid}")]]))


async def warns_view(cid, uid, name=None, staff=True):
    name = name or await name_of(cid, uid)
    g = await db.get_group(cid)
    ws = list(reversed(await db.list_warns(cid, uid)))
    n = await db.count_warns(cid, uid)
    limit = g["warn_limit"]
    lines = []
    for i, w in enumerate(ws):
        by = w.get("by")
        by_name = "🤖 GUARDIX" if by == bot.id else (mention(by, await name_of(cid, by)) if by else "—")
        when = datetime.fromtimestamp(w["ts"], cfg.TZ).strftime("%d %b %Y, %I:%M %p")
        lines.append(f"<b>{i + 1}.</b> {esc(w['reason'])}\n    👮 {by_name} · 🕒 {when}")
    status = "🔴 Limit reached" if n >= limit else ("🟠 Final warning" if limit > 1 and n == limit - 1 else "🟢 OK")
    act = {"mute": f"mute ({fmt_secs(g['warn_mute_secs'])})", "ban": "ban", "kick": "kick"}.get(g["warn_action"], "mute")
    head = "\n".join([kv("👤", "Member", mention(uid, name)), kv("🆔", "ID", f"<code>{uid}</code>"),
                      kv("⚠️", "Active warnings", f"<b>{n}/{limit}</b>"), kv("🚦", "Status", status),
                      kv("⚖️", "At limit", act)])
    body = head + "\n\n" + ("\n".join(lines) if lines else "No warnings 🎉")
    rows = []
    if staff:
        rows.append([("🗑 Remove Last Warning", f"w:rm:{cid}:{uid}"), ("♻️ Reset Warnings", f"w:rs:{cid}:{uid}")])
    rows.append([("🔄 Refresh", f"w:rf:{cid}:{uid}"), ("❌ Close", "x")])
    return await panel(cid, "WARNINGS", body), mk(rows)


async def cmd_warns(m, args):
    if not await need_group(m):
        return
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        uid, name = m.from_user.id, m.from_user.full_name
    staff = await actor_can(m.chat.id, m.from_user.id, "can_restrict_members")
    if uid != m.from_user.id and not staff:
        return await say(m, "⛔ Only staff can view other members' warnings.")
    text, kb = await warns_view(m.chat.id, uid, name, staff)
    await say_card(m, uid, text, kb)


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
    cid, cap = m.chat.id, cfg.PURGE_MAX
    a = (args or "").strip()
    if a and not a.isdigit():
        return await say(m, "Usage: <code>/purge</code> (reply) · <code>/purge 10</code> · <code>/purge 50</code> · <code>/purge 100</code>")
    n = int(a) if a else 0
    if a and not 1 <= n <= cap:
        return await say(m, f"❌ Count must be between <b>1</b> and <b>{cap}</b>.")
    r = m.reply_to_message
    if r:
        first = r.message_id
        ids = list(range(first, m.message_id))
        if n:
            ids = ids[:n]
        if len(ids) > cap:
            return await say(m, f"❌ That range is <b>{len(ids)}</b> messages — max <b>{cap}</b> at once. Reply to a newer message.")
    elif n:
        ids = list(range(max(1, m.message_id - n), m.message_id))
    else:
        return await say(m, "Reply to the first message to purge from, or use <code>/purge 10</code>.")
    if not ids:
        return await say(m, "ℹ️ Nothing to purge.")
    prog = await bot.send_message(cid, await panel(cid, "PURGE", f"🧹 Deleting <b>{len(ids)}</b> messages…"))
    accepted = rejected = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        for _ in range(3):
            try:
                await bot.delete_messages(cid, chunk)
                accepted += len(chunk)
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramAPIError:
                rejected += len(chunk)
                break
        await asyncio.sleep(0.2)
    await db.audit(cid, m.from_user.id, 0, "purge", "ok" if not rejected else "partial",
                   f"group={cid} requested={len(ids)} accepted={accepted} rejected={rejected}")
    body = "\n".join([kv("📋", "Requested", f"<b>{len(ids)}</b>"), kv("✅", "Sent for deletion", f"<b>{accepted}</b>"),
                      kv("⚠️", "Rejected by Telegram", f"<b>{rejected}</b>"),
                      kv("👮", "Executed by", mention(m.from_user.id, m.from_user.full_name)), kv("🏘", "Group", f"<b>{esc(m.chat.title)}</b>"),
                      "\n<i>Telegram silently skips messages that are too old / already deleted / service messages.</i>"])
    await safe_delete(cid, prog.message_id)
    await safe_delete(cid, m.message_id)
    await temp(cid, await panel(cid, "PURGE COMPLETE", body), 20)


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
        if (args or "").strip().lower() == "all":
            await bot.unpin_all_chat_messages(m.chat.id)
            return await say(m, await panel(m.chat.id, "UNPINNED", "📍 All pinned messages removed."))
        if m.reply_to_message:
            await bot.unpin_chat_message(m.chat.id, m.reply_to_message.message_id)
        else:
            await bot.unpin_chat_message(m.chat.id)
        await say(m, await panel(m.chat.id, "UNPINNED", "📍 Unpinned by " + mention(m.from_user.id, m.from_user.full_name) + "\n<i>/unpin all removes every pin.</i>"))
    except TelegramAPIError as e:
        await say(m, "❌ " + tg_err(e))


PROMO_RIGHTS = [("can_delete_messages", "Delete messages", "🗑"), ("can_restrict_members", "Ban / restrict", "🔨"),
                ("can_pin_messages", "Pin messages", "📌"), ("can_invite_users", "Invite users", "🔗"),
                ("can_change_info", "Change group info", "🏷"), ("can_manage_video_chats", "Video chats", "🎥"),
                ("can_promote_members", "Add new admins", "👑")]
PROMO_DEFAULT = {"can_delete_messages", "can_restrict_members", "can_pin_messages", "can_invite_users"}


def _rights_of(member):
    if member is None:
        return set()
    if member.status == "creator":
        return {r for r, _, _ in PROMO_RIGHTS}
    if member.status == "administrator":
        return {r for r, _, _ in PROMO_RIGHTS if getattr(member, r, False)}
    return set()


async def promo_allowed(chat_id, actor):
    """Rights this actor may give = rights the actor holds ∩ rights the bot holds."""
    try:
        botr = _rights_of(await bot.get_chat_member(chat_id, bot.id))
    except TelegramAPIError:
        botr = set()
    mine = {r for r, _, _ in PROMO_RIGHTS} if is_sudo(actor) else _rights_of((await chat_admins(chat_id, True)).get(actor))
    return mine & botr, mine, botr


async def promo_view(tok):
    d = PENDING[tok]
    rows, pair = [], []
    for i, (r, lab, ic) in enumerate(PROMO_RIGHTS):
        if r in d["allowed"]:
            txt = f"{'✅' if r in d['sel'] else '⬜'} {ic} {lab}"
        else:
            txt = f"🔒 {ic} {lab}"
        pair.append((txt, f"pm:t:{tok}:{i}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([("☑️ Select All", f"pm:a:{tok}"), ("🧹 Clear All", f"pm:c:{tok}")])
    rows.append([("✅ Confirm Promotion", f"pm:y:{tok}"), ("❌ Cancel", f"pm:n:{tok}")])
    body = "\n".join([kv("👤", "Member", mention(d["target"], d["name"])), kv("🆔", "ID", f"<code>{d['target']}</code>"),
                      kv("✅", "Selected", f"<b>{len(d['sel'])}</b> / {len(d['allowed'])}"),
                      "\nPick the rights to give 👇\n<i>🔒 = you (or the bot) don't hold that right, so it can't be given.</i>"])
    return await panel(d["chat"], "PROMOTE", body), mk(rows)


async def cmd_promote(m, args):
    if not await need_group(m):
        return
    if not m.from_user or m.from_user.id == ANON or m.sender_chat:
        return await say(m, "⚠️ Anonymous admin detected. Turn off <i>Remain Anonymous</i> to use this command.")
    cid, actor = m.chat.id, m.from_user.id
    admins = await chat_admins(cid, True)
    adm = admins.get(actor)
    if not is_sudo(actor):
        if adm is None:
            return await say(m, "🚫 <b>Abhi aap eligible nahi hain.</b>\nSirf group admins (jinke paas <i>Add new admins</i> right ho) promote kar sakte hain.")
        if adm.status != "creator" and not getattr(adm, "can_promote_members", False):
            return await say(m, "🚫 <b>Abhi aap eligible nahi hain.</b>\nAapke paas <b>Add new admins</b> ka right nahi hai — group owner se ye right maango.")
    err = await bot_need(cid, "can_promote_members")
    if err:
        return await say(m, "❌ " + err)
    uid, name, _ = await resolve_target(m, args)
    if not uid:
        return await say(m, "Usage: /promote [reply|id|@user]")
    if uid == bot.id:
        return await say(m, "🙂 Main pehle se bot hoon.")
    tm = admins.get(uid)
    if tm and tm.status == "creator":
        return await say(m, "👑 That's the group owner — already has every right.")
    allowed, mine, botr = await promo_allowed(cid, actor)
    if not allowed:
        return await say(m, "🚫 Aap koi bhi right de nahi sakte (aapke paas ya mere paas wo rights nahi hain).")
    tok = secrets.token_urlsafe(5)
    PENDING[tok] = {"type": "promo", "actor": actor, "chat": cid, "target": uid, "name": name,
                    "sel": set(allowed & PROMO_DEFAULT), "allowed": allowed, "mine": mine, "botr": botr}
    text, kb = await promo_view(tok)
    await say_card(m, uid, text, kb)


async def cb_promote(cb: CallbackQuery, p):
    act, tok = p[1], p[2]
    d = PENDING.get(tok)
    if not d or d.get("type") != "promo":
        return await cb.answer("⌛ Expired — run /promote again.", show_alert=True)
    if cb.from_user.id != d["actor"]:
        return await cb.answer("⛔ Only the admin who ran /promote can use these buttons.", show_alert=True)
    if act == "n":
        PENDING.pop(tok, None)
        await cb.answer("Cancelled")
        return await edit(cb, "❌ Promotion cancelled.")
    if act == "t":
        r, lab, _ = PROMO_RIGHTS[int(p[3])]
        if r not in d["allowed"]:
            why = "You don't hold this right yourself." if r not in d["mine"] else "I (the bot) don't hold this right."
            return await cb.answer("🔒 " + why, show_alert=True)
        d["sel"] ^= {r}
    elif act == "a":
        d["sel"] = set(d["allowed"])
    elif act == "c":
        d["sel"] = set()
    elif act == "y":
        if not d["sel"]:
            return await cb.answer("Select at least one right first.", show_alert=True)
        allowed, _, _ = await promo_allowed(d["chat"], d["actor"])
        sel = d["sel"] & allowed
        if not sel:
            return await cb.answer("🚫 You are no longer eligible to give these rights.", show_alert=True)
        try:
            await bot.promote_chat_member(d["chat"], d["target"], **{r: (r in sel) for r, _, _ in PROMO_RIGHTS})
        except TelegramAPIError as e:
            return await cb.answer(re.sub("<.*?>", "", tg_err(e))[:180], show_alert=True)
        PENDING.pop(tok, None)
        _admin_cache.pop(d["chat"], None)
        await db.audit(d["chat"], d["actor"], d["target"], "promote", "ok", ",".join(sorted(sel)))
        given = "\n".join(f"✅ {ic} {lab}" for r, lab, ic in PROMO_RIGHTS if r in sel)
        body = "\n".join([kv("⬆️", "Action", "<b>Promoted to admin</b>"), kv("👤", "User", mention(d["target"], d["name"])),
                          kv("👮", "By", mention(cb.from_user.id, cb.from_user.full_name)), "\n<b>Rights given</b>\n" + given])
        await cb.answer("✅ Promoted")
        return await edit(cb, await panel(d["chat"], "PROMOTED", body))
    text, kb = await promo_view(tok)
    await edit(cb, text, kb)
    await cb.answer()


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


async def active_view(cid, mode="t"):
    rows = await (db.top_today(cid, 10) if mode == "t" else db.top(cid, 10))
    vs = await db.verified_among(cid, [r["user_id"] for r in rows])
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = [f"{medals[i]} {mention(r['user_id'], r.get('name'))}{' ✅' if r['user_id'] in vs else ''} — <b>{r['count']}</b> msgs" for i, r in enumerate(rows)]
    empty = "No activity tracked yet today — start chatting! 💬" if mode == "t" else "No data yet — start chatting!"
    kb = mk([[("🏆 All-time" if mode == "t" else "📅 Today", f"ac:{'a' if mode == 't' else 't'}:{cid}"), ("❌ Close", "x")]])
    return await panel(cid, "ACTIVE TODAY" if mode == "t" else "TOP CHATTERS", "\n".join(lines) or empty), kb


async def cmd_active(m, args):
    if not await need_group(m):
        return
    text, kb = await active_view(m.chat.id, "t")
    await say(m, text, kb)


async def cb_active(cb: CallbackQuery, p):
    cid = int(p[2])
    if cb.message.chat.id != cid:
        return await cb.answer()
    text, kb = await active_view(cid, p[1])
    await edit(cb, text, kb)
    await cb.answer()


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
    agg = await db.stats.aggregate([{"$match": {"chat_id": cid}}, {"$group": {"_id": None, "n": {"$sum": "$count"}}}]).to_list(1)
    body = "\n".join([kv("🏘", "Name", f"<b>{esc(chat.title)}</b>"), kv("🆔", "ID", f"<code>{cid}</code>"),
                      kv("🔗", "Link", ("@" + esc(chat.username)) if chat.username else "Private"),
                      kv("👥", "Members", f"<b>{count}</b>"),
                      kv("👮", "Admins", f"<b>{len([a for a in admins.values() if not a.user.is_bot])}</b>"),
                      kv("💎", "VIPs", f"<b>{len(await db.list_vips(cid))}</b>"),
                      kv("💬", "Messages tracked", f"<b>{agg[0]['n'] if agg else 0}</b>"),
                      kv("⚠️", "Warnings stored", f"<b>{await db.warns.count_documents({'chat_id': cid})}</b>"),
                      kv("🔐", "Verify tags", onoff(g["verify_on"])), kv("🔒", "Lock", onoff(g["locked"])),
                      kv("🎨", "Theme", THEMES[g["theme"]]["name"])])
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
    await db.set_group(m.chat.id, **{key: a == "on"})
    await db.audit(m.chat.id, m.from_user.id, 0, "setting", "ok", f"{key}={a}")
    await say(m, await panel(m.chat.id, label.upper(), f"{onoff(a == 'on')} {label} is now <b>{a.upper()}</b>"))


async def cmd_welcome(m, args):
    await _toggle_cmd(m, args, "welcome", "Welcome message")








_last_notice = {}





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
    "welcome": ("t", "Welcome message"), "del_service": ("t", "Hide join/leave notices"),
    "locked": ("t", "Lock Mode"), "lock_vip": ("t", "VIP can chat in lock"), "lock_admins": ("t", "Admins can chat in lock"),
    "warn_limit": ("n", "Warn limit", 1, 1, 10, 1), "warn_mute_secs": ("n", "Warn mute (min)", 600, 60, 86400, 60),
    "warn_action": ("c", "Warn action", ["mute", "ban", "kick"]),
    "all_batch": ("c", "Tag batch with text (/all text)", [1, 5, 10, 15, 20]),
}
SECTIONS = {
    "lock": ("🔒 Lock Mode", ["locked", "lock_vip", "lock_admins"]),
    "warn": ("⚠️ Warnings", ["warn_limit", "warn_action", "warn_mute_secs"]),
    "wel": ("👋 Welcome", ["welcome", "del_service"]),
    "all": ("📣 Tag-all", ["all_batch"]),
}


def onoff(v):
    return "🟢" if v else "🔴"


async def render_home(cid):
    g = await db.get_group(cid)
    body = ("🛡 <b>Protection</b> ➜ 🟢 <b>ALWAYS ON</b>\n<i>spam · same-msg · flood · raid · links · abuse</i>\n\n"
            f"🔐 Verify tags: {onoff(g['verify_on'])} <i>(/verify on|off)</i>\n🔒 Lock: {onoff(g['locked'])}\n"
            f"👋 Welcome: {onoff(g['welcome'])}\n⚠️ Warn limit: <b>{g['warn_limit']}</b> → <b>{g['warn_action']}</b>\n\n"
            "🎨 Theme &amp; font are managed by the Bot Owner.\n\nSelect a module below.")
    kb = mk([[("🔒 Lock", f"s:o:{cid}:lock"), ("⚠️ Warnings", f"s:o:{cid}:warn")],
             [("👋 Welcome", f"s:o:{cid}:wel"), ("📣 Tag-all", f"s:o:{cid}:all")],
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


async def render_look(cid, page="t"):
    t, f = await look(cid)
    rows = [[(("🎨 Themes ✅" if page == "t" else "🎨 Themes"), f"s:o:{cid}:lookt"),
             (("🔤 Fonts ✅" if page == "f" else "🔤 Fonts"), f"s:o:{cid}:lookf")]]
    row = []
    if page == "t":
        for i, th in enumerate(THEMES):
            row.append((("✅ " if i == t else "") + f"{th['icon']} {th['name']}", f"s:th:{cid}:{i}"))
            if len(row) == 2:
                rows.append(row)
                row = []
    else:
        for i, (nm, _) in enumerate(FONTS):
            row.append((("✅ " if i == f else "") + fx("Abc", i) + " " + nm, f"s:ft:{cid}:{i}"))
            if len(row) == 2:
                rows.append(row)
                row = []
    if row:
        rows.append(row)
    rows.append([("🏠 Owner panel", "ow:home"), ("❌ Close", "x")])
    scope = "🌍 <b>All groups + default</b>" if cid == 0 else f"🏘 <b>{esc((await db.get_group(cid))['_title'])}</b> <code>{cid}</code>"
    body = (f"{scope}\n\n🎨 Theme: <b>{THEMES[t]['name']}</b> ({len(THEMES)} available)\n🔤 Font: <b>{FONTS[f][0]}</b> ({len(FONTS)} available)\n\n"
            f"Preview: {fx('Guardix Security 2026', f)}\n\nTap to change 👇")
    return await panel(cid, "LOOK CONTROL", body), mk(rows)


async def cmd_settings(m, args):
    if m.chat.type == "private":
        if not is_owner(m.from_user.id):
            return await say(m, "⚙️ Settings are per-group. Run /settings inside your group (admins only).")
        return await cmd_look(m, "")
    if not await mod_guard(m, "can_change_info"):
        return
    text, kb = await render_home(m.chat.id)
    await say(m, text, kb)


async def cmd_look(m, args):
    """Owner only, DM: control theme (10) and font (20) for all groups, or one group: /look [chat_id]"""
    if m.chat.type != "private":
        return await say(m, "🎨 Theme &amp; font are controlled by the Bot Owner from DM.")
    if not is_owner(m.from_user.id):
        return await say(m, "⛔ Owner only.")
    a = (args or "").strip()
    cid = int(a) if a.lstrip("-").isdigit() and a not in ("0", "-0") else 0
    text, kb = await render_look(cid, "t")
    await say(m, text, kb)


async def settings_auth(cb: CallbackQuery, cid):
    if cb.message.chat.type == "private":
        return is_owner(cb.from_user.id)
    if cid == 0 or cb.message.chat.id != cid:
        return False
    return await actor_can(cid, cb.from_user.id, "can_change_info")


async def cb_settings(cb: CallbackQuery, p):
    act = p[1]
    if act == "noop":
        return await cb.answer()
    cid = int(p[2])
    private = cb.message.chat.type == "private"
    if not await settings_auth(cb, cid):
        return await cb.answer("⛔ Owner only." if private else "⛔ Admins with 'Change group info' only.", show_alert=True)
    if private and act not in ("th", "ft", "o"):
        return await cb.answer("Open /settings inside the group.", show_alert=True)
    if act == "home":
        text, kb = await render_home(cid)
    elif act == "o":
        if p[3] in ("lookt", "lookf"):
            if not private:
                return await cb.answer("🎨 Theme & font are controlled by the Bot Owner from DM.", show_alert=True)
            text, kb = await render_look(cid, p[3][-1])
        elif p[3] in SECTIONS and not private:
            text, kb = await render_section(cid, p[3])
        else:
            return await cb.answer("Invalid section", show_alert=True)
    elif act in ("t", "n", "c"):
        key = p[3]
        sp = SPEC.get(key)
        if not sp:
            return await cb.answer("Invalid setting", show_alert=True)
        g = await db.get_group(cid)
        if act == "t" and sp[0] == "t":
            nv = not g[key]
            if key == "locked" and nv:
                err = await bot_need(cid, "can_delete_messages")
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
        if not private:
            return await cb.answer("🎨 Theme & font are controlled by the Bot Owner from DM.", show_alert=True)
        i = int(p[3])
        if not 0 <= i < (len(THEMES) if act == "th" else len(FONTS)):
            return await cb.answer("Invalid", show_alert=True)
        key = "theme" if act == "th" else "font"
        if cid:
            await db.set_group(cid, **{key: i})
        else:
            await db.set_meta(**{key: i})
            await db.groups.update_many({}, {"$set": {f"s.{key}": i}})
            db._gc.clear()
        await db.audit(cid, cb.from_user.id, 0, "look", "ok", f"{key}={i}")
        text, kb = await render_look(cid, "t" if act == "th" else "f")
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


# ═════════════════════════ VERIFICATION (one tap, per group) ═════════════════════════
_ver_cache = {}   # (chat, uid) -> (ts, bool)
_vtag_last = {}   # (chat, uid) -> (ts, message_id)


async def is_ver(chat_id, uid):
    k = (chat_id, uid)
    c = _ver_cache.get(k)
    if c and time.time() - c[0] < 120:
        return c[1]
    v = await db.is_verified(chat_id, uid)
    if len(_ver_cache) > 50000:
        _ver_cache.clear()
    _ver_cache[k] = (time.time(), v)
    return v


def ver_kb_group(chat_id):
    return mk([[("✅ Verify Now", "url:" + verify_url(chat_id))]])


async def settle_group(chat_id, user):
    """After verification: tidy the group — turn the welcome card into a ✅ card, drop the tag message."""
    tag = _vtag_last.pop((chat_id, user.id), None)
    if tag and tag[1]:
        await safe_delete(chat_id, tag[1])
    w = await db.pop_welcome(chat_id, user.id)
    if not w:
        return
    text = await panel(chat_id, "VERIFIED", f"✅ {mention(user.id, user.full_name)} is now <b>verified</b>\n🎉 Welcome to the family!")
    try:
        if w.get("photo"):
            await bot.edit_message_caption(chat_id=chat_id, message_id=w["mid"], caption=text, reply_markup=None)
        else:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=w["mid"], reply_markup=None)
        spawn(_del_later(chat_id, w["mid"], 90))
    except TelegramAPIError:
        pass


async def do_verify(user, chat_id, target=None):
    """One tap: verified in this group + bot started + welcome card with DP + return button."""
    uid = user.id
    g = await db.get_group(chat_id)
    await db.upsert_user(user, started=True)
    new = await db.set_verified(chat_id, uid)
    _ver_cache[(chat_id, uid)] = (time.time(), True)
    if new:
        await db.audit(chat_id, uid, uid, "verify", "ok")
        spawn(settle_group(chat_id, user))
    link = await group_link(chat_id)
    body = "\n".join(["🛡️ <b>Welcome to GUARDIX</b>", "",
                      kv("✅", "Verification", "<b>Successful</b>"), kv("📍", "Group", f"<b>{esc(g['_title'])}</b>"),
                      kv("🛡", "Account", "🟢 Verified"), kv("🚀", "Bot", "Started &amp; ready"),
                      "\n🎉 You can chat freely now."])
    rows = []
    if link:
        rows.append([("↩️ Return to Group", "url:" + link)])
    rows.append([("👤 My Profile", "p:me"), ("📖 Help", "h:home")])
    rows.append([("🆘 Support", "url:" + cfg.SUPPORT_URL)])
    if isinstance(target, CallbackQuery):
        await safe_delete(target.message.chat.id, target.message.message_id)
    await send_card(uid, uid, await panel(0, "VERIFIED", body), mk(rows))


async def cb_verify(cb: CallbackQuery, p):
    uid = cb.from_user.id
    if p[1] in ("menu", "st"):
        lines = []
        for c in (await db.verified_chats(uid))[:15]:
            lines.append(f"🟢 <b>{esc((await db.get_group(c))['_title'])}</b>")
        body = "\n".join(lines) or "⚪ Not verified in any group yet.\nJoin a group and tap <b>Verify Now</b> — one tap."
        body += f"\n\n🏷 Role: {await role_label(0, uid)}"
        await cb.answer()
        return await edit(cb, await panel(0, "YOUR STATUS", body), mk([[("🏠 Home", "h:start")]]))
    await cb.answer("This button is old — use the newest Verify button.", show_alert=True)


async def cb_vr(cb: CallbackQuery, p):
    """vr:<abs chat id> — Verify button inside the bot DM."""
    await cb.answer("⏳ Verifying…")
    await do_verify(cb.from_user, -int(p[1]), cb)


async def maybe_tag_verify(m: Message, g):
    """/verify on: an unverified member writes → bot tags them to verify (no mute, no delete)."""
    u, cid = m.from_user, m.chat.id
    if not g["verify_on"] or u.id in (ANON, TG_SERVICE) or m.sender_chat:
        return
    if await is_ver(cid, u.id):
        return
    k = (cid, u.id)
    last = _vtag_last.get(k)
    if last and time.time() - last[0] < 120:
        return
    if last and last[1]:
        await safe_delete(cid, last[1])
    body = (f"⚠️ {mention(u.id, u.full_name)}, <b>security ke liye verify karo</b>, phir chat karo 🔐\n"
            "Button dabao — bot DM mein start hoga aur turant verify ho jaoge.")
    try:
        msg = await bot.send_message(cid, await panel(cid, "VERIFY REQUIRED", body), reply_markup=ver_kb_group(cid))
    except TelegramAPIError:
        return
    _vtag_last[k] = (time.time(), msg.message_id)
    spawn(_del_later(cid, msg.message_id, 600))


async def cmd_verify(m, args):
    """/verify on|off — the only verification command."""
    if not await mod_guard(m, "can_change_info"):
        return
    a = (args or "").strip().lower()
    g = await db.get_group(m.chat.id)
    if a not in ("on", "off"):
        return await say(m, await panel(m.chat.id, "VERIFY", f"🔐 Verify tags: {onoff(g['verify_on'])} <b>{'ON' if g['verify_on'] else 'OFF'}</b>\n\n"
                                        "When <b>ON</b>, a member who hasn't verified is tagged on their message: <i>verify first, then chat</i>. "
                                        "The button opens the bot DM — one tap = verified + bot started.\n\nUsage: <code>/verify on</code> · <code>/verify off</code>"))
    await db.set_group(m.chat.id, verify_on=(a == "on"), verify_by_all=False)
    await db.audit(m.chat.id, m.from_user.id, 0, "setting", "ok", f"verify_on={a}")
    await say(m, await panel(m.chat.id, "VERIFY", f"{onoff(a == 'on')} Verification tags are now <b>{a.upper()}</b>"))


def render_welcome(tpl, user, chat):
    t = esc(tpl or "")
    for k, v in (("{name}", esc(user.full_name)), ("{mention}", mention(user.id, user.full_name)),
                 ("{group}", esc(chat.title)), ("{id}", str(user.id))):
        t = t.replace(k, v)
    return t


join_times = defaultdict(deque)


async def welcome_card(chat, u, g, verified):
    n = None
    try:
        n = await bot.get_chat_member_count(chat.id)
    except TelegramAPIError:
        pass
    if g["welcome_text"]:
        body = render_welcome(g["welcome_text"], u, chat)
    else:
        rows = [f"👋 <b>Welcome</b> {mention(u.id, u.full_name)}", "",
                kv("🏘", "Group", f"<b>{esc(chat.title)}</b>"), kv("👤", "Name", esc(u.full_name)),
                kv("🆔", "ID", f"<code>{u.id}</code>"), kv("🔗", "Username", ("@" + esc(u.username)) if u.username else "—"),
                kv("💎", "Premium", "Yes ✨" if getattr(u, "is_premium", False) else "No")]
        if n:
            rows.append(kv("👥", "Member", f"<b>#{n}</b>"))
        body = "\n".join(rows)
    body += f"\n\n<i>{esc(pick_line())}</i>"
    body += "\n\n🟢 <b>Verified member</b> ✅" if verified else "\n\n🔐 <b>Verify</b> — tap the button below (one tap)."
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
        if not is_sudo(u.id):
            dq = join_times[chat.id]
            now = time.time()
            dq.append(now)
            while dq and now - dq[0] > g["raid_window"]:
                dq.popleft()
            if len(dq) >= g["raid_limit"] and g["raid_until"] < now:
                await db.set_group(chat.id, raid_until=now + 600)
                g = await db.get_group(chat.id)
                await db.audit(chat.id, bot.id, 0, "raid_detected", "ok", f"{len(dq)} joins")
                tolog(f"🚨 <b>RAID ALERT</b> in {esc(chat.title)} (<code>{chat.id}</code>)\n{len(dq)} joins / {g['raid_window']}s → new members muted 10 min")
                await temp(chat.id, "🚨 <b>Raid protection active</b> — new members are muted for 10 minutes.", 30)
            if g["raid_until"] > time.time():
                await svc_mute(chat.id, u.id, u.full_name, bot.id, 600, "raid protection")
        verified = await db.is_verified(chat.id, u.id)
        if g["welcome"]:
            try:
                msg = await send_card(chat.id, u.id, await welcome_card(chat, u, g, verified), None if verified else ver_kb_group(chat.id))
            except TelegramAPIError:
                msg = None
            if msg:
                if verified:
                    spawn(_del_later(chat.id, msg.message_id, 300))
                else:
                    await db.put_welcome(chat.id, u.id, msg.message_id, msg.photo)
        if not verified:  # reachable users also get the verify button in DM
            ud = await db.get_user(u.id) or {}
            if ud.get("started") and not ud.get("blocked"):
                try:
                    await send_card(u.id, u.id, await panel(0, "VERIFY", f"🛡️ Welcome to <b>{esc(chat.title)}</b>!\n"
                                                           "Please verify — one tap and you're done."),
                                    mk([[("✅ Verify & Start", f"vr:{abs(chat.id)}")]]))
                except TelegramAPIError:
                    pass


async def drop_pending(chat_id, uid):
    """Member left: remove their welcome card / verify tag from the group."""
    w = await db.pop_welcome(chat_id, uid)
    if w:
        await safe_delete(chat_id, w["mid"])
    tag = _vtag_last.pop((chat_id, uid), None)
    if tag and tag[1]:
        await safe_delete(chat_id, tag[1])


async def on_leave(m: Message):
    g = await db.get_group(m.chat.id)
    if g["del_service"]:
        await safe_delete(m.chat.id, m.message_id)
    if m.left_chat_member:
        await drop_pending(m.chat.id, m.left_chat_member.id)
        await db.forget_member(m.chat.id, m.left_chat_member.id)


async def protect_sudo(ev: ChatMemberUpdated):
    """Another admin/bot removed, muted or demoted an Owner/Sudo → undo what we can and tell them in DM."""
    chat, new, old, u, by = ev.chat, ev.new_chat_member, ev.old_chat_member, ev.new_chat_member.user, ev.from_user
    who = f"{esc(by.full_name)}{' 🤖' if by.is_bot else ''} (<code>{by.id}</code>)"
    note = None
    try:
        if new.status in ("kicked", "left"):
            if new.status == "kicked":
                await bot.unban_chat_member(chat.id, u.id, only_if_banned=True)
            link = None
            try:
                link = (await bot.create_chat_invite_link(chat.id, member_limit=1, expire_date=int(time.time()) + 3600)).invite_link
            except TelegramAPIError:
                link = await group_link(chat.id)
            note = (f"🛡 <b>Protection</b>\nYou were removed from <b>{esc(chat.title)}</b> by {who}.\n"
                    + ("♻️ I unbanned you.\n" if new.status == "kicked" else "") + (f"🔗 Rejoin: {link}" if link else "Ask an admin to add you back."))
        elif new.status == "restricted" and not getattr(new, "can_send_messages", True):
            await bot.restrict_chat_member(chat.id, u.id, permissions=await default_perms(chat.id))
            note = f"🛡 <b>Protection</b>\n{who} muted you in <b>{esc(chat.title)}</b> — I lifted the mute."
        elif old.status == "administrator" and new.status == "member":
            note = f"🛡 <b>Heads-up</b>\nYour admin rights in <b>{esc(chat.title)}</b> were removed by {who}. Use /godmode in the group to get admin back."
    except TelegramAPIError as e:
        note = f"🛡 Protection tried to help in <b>{esc(chat.title)}</b> but failed: {esc(tg_err(e))}"
    if note:
        await db.audit(chat.id, by.id, u.id, "protect_sudo", "ok", new.status)
        tolog(f"🛡 <b>SUDO PROTECTED</b> in {esc(chat.title)} — {mention(u.id, u.full_name)} ({new.status}) by {who}")
        try:
            await bot.send_message(u.id, note)
        except TelegramAPIError:
            pass


@router.chat_member()
async def on_chat_member(ev: ChatMemberUpdated):
    """Tracks every join/leave (needs bot admin) and protects Owner/Sudo."""
    chat, new = ev.chat, ev.new_chat_member
    if chat.type not in GROUPS or new.user.is_bot:
        return
    if is_sudo(new.user.id) and ev.from_user and ev.from_user.id not in (new.user.id, bot.id):
        spawn(protect_sudo(ev))
    gone = new.status in ("left", "kicked") or (new.status == "restricted" and not getattr(new, "is_member", True))
    if gone:
        await db.forget_member(chat.id, new.user.id)
        await drop_pending(chat.id, new.user.id)
    else:
        await db.upsert_user(new.user, chat.id)


@router.chat_join_request()
async def on_join_request(ev: ChatJoinRequest):
    """Approval-groups: DM the requester a Verify button (we never approve — admins decide)."""
    u, chat = ev.from_user, ev.chat
    if u.is_bot:
        return
    await db.upsert_user(u)
    if await db.is_verified(chat.id, u.id):
        return
    body = (f"🛡️ <b>Join request received</b> for <b>{esc(chat.title)}</b>\n\n"
            "Please verify — one tap and the bot starts.\n<i>The admins will approve your request separately.</i>")
    try:
        await send_card(ev.user_chat_id, u.id, await panel(0, "VERIFY", body), mk([[("✅ Verify & Start", f"vr:{abs(chat.id)}")]]))
    except TelegramAPIError:
        pass


@router.edited_message()
async def on_edited(m: Message):
    """A clean message edited into abuse/link still gets caught."""
    if m.chat.type not in GROUPS or not m.from_user or m.from_user.is_bot or m.from_user.id in (ANON, TG_SERVICE):
        return
    if (await db.get_meta()).get("maintenance") and not is_sudo(m.from_user.id):
        return
    uid = m.from_user.id
    g = await db.get_group(m.chat.id)
    if uid in db.gban_ids:
        return
    if not await is_trusted(m.chat.id, uid):
        await run_protection(m, g, edited=True)
    elif uid in await chat_admins(m.chat.id) and not is_sudo(uid) and not await is_group_creator(m.chat.id, uid):
        await run_protection(m, g, admin=True, edited=True)


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


async def admin_callout(m: Message, why="spam"):
    u, cid = m.from_user, m.chat.id
    k = (cid, u.id)
    if time.time() - _callout_last.get(k, 0) < 60:
        return
    _callout_last[k] = time.time()
    line = rline("callout")
    body = (line if "{user}" in line else "{user} — " + line).replace("{user}", mention(u.id, u.full_name))
    if why != "spam":
        body += f"\n🧼 <i>Reason: {esc(why)}</i>"
    owner = await group_owner(cid)
    if owner and owner != u.id:
        body += f"\n\n👑 {mention(owner, await name_of(cid, owner))} — group owner, please dekhiye."
    await temp(cid, await panel(cid, "ADMIN SPAM", body), 30)


_callout_last = {}


async def violation(m: Message, g, reason, kind, admin=False, extra_mids=()):
    u = m.from_user
    err = await bot_need(m.chat.id, "can_delete_messages")
    if err:
        return await notify_missing(m.chat.id, "Auto-protection needs <b>Delete messages</b>. " + err)
    for mid in (m.message_id, *extra_mids):
        await safe_delete(m.chat.id, mid)
        _deleted_mids.add((m.chat.id, mid))
    if len(_deleted_mids) > 3000:
        _deleted_mids.clear()
    await db.audit(m.chat.id, bot.id, u.id, ("admin_" if admin else "auto_") + kind, "ok", reason)
    if admin:
        return await admin_callout(m, "spam" if kind in ("spam", "flood") else reason)
    if kind == "flood":
        ok, t = await svc_mute(m.chat.id, u.id, u.full_name, bot.id, 300, reason)
        await temp(m.chat.id, t if ok else f"🌊 Flood from {mention(u.id, u.full_name)} deleted.", 12)
    else:
        t, _ = await issue_warn(m.chat.id, u.id, u.full_name, bot.id, reason)
        await temp(m.chat.id, t, 14)


MENTION_RE = re.compile(r"@\w{4,}")


async def run_protection(m: Message, g, admin=False, edited=False):
    """Permanent auto-protection. admin=True -> only flood / same-message spam (delete + callout, no warn)."""
    u = m.from_user
    now = time.time()
    key = (m.chat.id, u.id)
    text = m.text or m.caption or ""
    if not edited:
        dq = flood_hist[key]
        dq.append(now)
        while dq and now - dq[0] > g["flood_window"]:
            dq.popleft()
        if len(dq) > g["flood_limit"]:
            dq.clear()
            return await violation(m, g, f"flood (> {g['flood_limit']} msgs / {g['flood_window']}s)", "flood", admin)
    if not text:
        return
    if admin:  # owner decides from the bot DM whether admins' abusive words are deleted too
        on = g["admin_bw"] if g["admin_bw"] is not None else bool((await db.get_meta()).get("admin_bw"))
        if on and (find_bad(text, {canon(w) for w in g["bad_exclude"]}) or (g["badwords"] and bw_match(m.chat.id, g["badwords"], text))):
            return await violation(m, g, "abusive language", "badword", True)
    if not admin:
        hit = find_bad(text, {canon(w) for w in g["bad_exclude"]})
        if not hit and g["badwords"]:
            hit = bw_match(m.chat.id, g["badwords"], text)
        if hit:
            return await violation(m, g, "abusive language", "badword")
        bad = [h for h in extract_hosts(m) if not any(h == d or h.endswith("." + d) for d in g["allowed_domains"])]
        if bad:
            return await link_violation(m, g, bad)
        if len(MENTION_RE.findall(text)) > 5:
            return await violation(m, g, "mass mentions", "mention")
    if not edited and not re.match(r"^[/.!]\w", text) and len(text) > 3:
        n = normalize(text)
        sq = spam_hist[key]
        sq.append((now, n, m.message_id))
        while sq and now - sq[0][0] > 30:
            sq.popleft()
        same = [mid for _, x, mid in sq if x == n]
        if len(same) >= 3:
            sq.clear()
            return await violation(m, g, "same message spam", "spam", admin, [x for x in same if x != m.message_id])


# ───────── anti-link: delete + Approve / Reject buttons ─────────
_link_cool = {}


async def link_violation(m: Message, g, hosts):
    u, chat = m.from_user, m.chat
    err = await bot_need(chat.id, "can_delete_messages")
    if err:
        return await notify_missing(chat.id, "Auto-protection needs <b>Delete messages</b>. " + err)
    await safe_delete(chat.id, m.message_id)
    _deleted_mids.add((chat.id, m.message_id))
    k = (chat.id, u.id)
    if time.time() - _link_cool.get(k, 0) < 60:
        return
    _link_cool[k] = time.time()
    rid = secrets.token_urlsafe(6)
    hosts = sorted(hosts)[:3]
    await db.lrev.insert_one({"_id": rid, "chat_id": chat.id, "user_id": u.id, "name": u.full_name,
                              "text": (m.text or m.caption or "")[:2500], "hosts": hosts, "status": "open",
                              "ts": time.time(), "purge_at": utc(time.time() + 86400)})
    body = "\n".join([kv("🔗", "Link", "<b>removed</b>"), kv("👤", "From", mention(u.id, u.full_name)),
                      kv("🌐", "Domain", ", ".join(esc(h) for h in hosts)),
                      "\n<i>Staff: ✅ Approve posts it back · 🚫 Reject warns the sender.</i>"])
    msg = await bot.send_message(chat.id, await panel(chat.id, "LINK BLOCKED", body),
                                 reply_markup=mk([[("✅ Approve", f"lk:ok:{rid}"), ("🚫 Reject + Warn", f"lk:no:{rid}")]]))
    spawn(_del_later(chat.id, msg.message_id, 3600))
    await db.audit(chat.id, bot.id, u.id, "auto_link", "ok", ",".join(hosts))


async def cb_link(cb: CallbackQuery, p):
    d = await db.lrev.find_one({"_id": p[2]})
    if not d or d["chat_id"] != cb.message.chat.id:
        return await cb.answer("Not found / expired.", show_alert=True)
    if not await actor_can(d["chat_id"], cb.from_user.id, "can_delete_messages"):
        return await cb.answer("⛔ Staff only.", show_alert=True)
    ok = p[1] == "ok"
    r = await db.lrev.update_one({"_id": p[2], "status": "open"}, {"$set": {"status": "approved" if ok else "rejected", "by": cb.from_user.id}})
    if not r.modified_count:
        return await cb.answer("Already handled.", show_alert=True)
    staff = mention(cb.from_user.id, cb.from_user.full_name)
    await db.audit(d["chat_id"], cb.from_user.id, d["user_id"], "link_approve" if ok else "link_reject", "ok")
    if ok:
        await bot.send_message(d["chat_id"], await panel(d["chat_id"], "LINK APPROVED",
                               f"✅ Approved by {staff}\n👤 {mention(d['user_id'], d['name'])} wrote:\n\n{esc(d['text'])}"))
        await cb.answer("✅ Approved")
        return await edit(cb, await panel(d["chat_id"], "LINK APPROVED", f"✅ Approved by {staff} — message posted back."))
    t, _ = await issue_warn(d["chat_id"], d["user_id"], d["name"], cb.from_user.id, "posted a blocked link")
    await cb.answer("🚫 Rejected")
    await edit(cb, t)


# ═════════════════════════ REPORT / @admin ═════════════════════════
_last_report = {}


def msg_link(chat_id, username, mid):
    if username:
        return f"https://t.me/{username}/{mid}"
    return f"https://t.me/c/{str(chat_id)[4:]}/{mid}" if str(chat_id).startswith("-100") else None


async def cmd_report(m: Message, args):
    if not await need_group(m):
        return
    uid, cid = m.from_user.id, m.chat.id
    ud = await db.get_user(uid) or {}
    if ud.get("fake_reports", 0) >= 3:
        return await say(m, "⛔ Aapka report access band hai — aapke kai reports fake nikle.")
    if time.time() - _last_report.get(uid, 0) < 60:
        return await say(m, "⏳ Please wait a minute before reporting again.")
    r = m.reply_to_message
    target = r.from_user if r and r.from_user else None
    if target and (target.id == uid or target.is_bot or await is_trusted(cid, target.id)):
        return await say(m, "ℹ️ You can't report that user.")
    _last_report[uid] = time.time()
    reason = re.sub(r"(?i)^@admins?\s*", "", args or "").strip()[:200]
    if target and r:  # same message reported again -> merge, no extra noise
        dup = await db.reports.find_one({"chat_id": cid, "msg_id": r.message_id, "status": "open"})
        if dup:
            await db.reports.update_one({"_id": dup["_id"]}, {"$addToSet": {"reporters": uid}})
            await safe_delete(cid, m.message_id)
            return await temp(cid, "ℹ️ Ye message pehle se report ho chuka hai — staff dekh lega.", 6)
    rid = secrets.token_urlsafe(6)
    await db.reports.insert_one({"_id": rid, "chat_id": cid, "reporter": uid, "reporters": [uid], "kind": "report" if target else "call",
                                 "target": target.id if target else 0, "msg_id": r.message_id if r else 0,
                                 "reason": reason, "status": "open", "ts": time.time()})
    admins = [a for a in (await chat_admins(cid)).values() if not a.user.is_bot][:5]
    pings = " ".join(f'<a href="tg://user?id={a.user.id}">\u2063</a>' for a in admins)
    link = msg_link(cid, m.chat.username, r.message_id) if r else None
    rows = [[("✅ Resolve", f"rp:ok:{rid}"), ("🗑 Delete msg", f"rp:del:{rid}")], [("⚠️ Warn", f"rp:warn:{rid}"), ("🚫 Fake report", f"rp:fake:{rid}")]]
    if link:
        rows.append([("👁 View message", "url:" + link)])
    body = (f"🚨 <b>Report</b> by {mention(uid, m.from_user.full_name)}\n🎯 Target: {mention(target.id, target.full_name) if target else '—'}\n"
            f"📝 {esc(reason) or 'No reason given'}{pings}")
    await bot.send_message(cid, body, reply_markup=mk(rows))
    tolog(f"🚨 <b>REPORT</b> in {esc(m.chat.title)}\nBy {mention(uid, m.from_user.full_name)} → {mention(target.id, target.full_name) if target else '—'}\n{esc(reason)}")
    await safe_delete(cid, m.message_id)


async def cb_report(cb: CallbackQuery, p):
    if p[1] == "all":  # close every open report of this chat (from the digest)
        cid = int(p[2])
        if cb.message.chat.id != cid or not await actor_can(cid, cb.from_user.id, "can_restrict_members"):
            return await cb.answer("⛔ Staff only.", show_alert=True)
        r = await db.reports.update_many({"chat_id": cid, "status": "open"}, {"$set": {"status": "closed", "outcome": "reviewed (digest)", "by": cb.from_user.id}})
        await cb.answer(f"✅ {r.modified_count} closed")
        return await edit(cb, cb.message.html_text + f"\n\n✅ <b>Reviewed</b> by {mention(cb.from_user.id, cb.from_user.full_name)}")
    d = await db.reports.find_one({"_id": p[2]})
    if not d or cb.message.chat.id != d["chat_id"]:
        return await cb.answer("Report not found.", show_alert=True)
    need = "can_delete_messages" if p[1] == "del" else "can_restrict_members" if p[1] == "warn" else None
    if not await actor_can(d["chat_id"], cb.from_user.id, need):
        return await cb.answer("⛔ Staff only.", show_alert=True)
    if d["status"] != "open":
        return await cb.answer("Already handled.", show_alert=True)
    status, res = "closed", "dismissed/resolved"
    if p[1] == "del":
        if d["msg_id"]:
            await safe_delete(d["chat_id"], d["msg_id"])
        res = "message deleted"
    elif p[1] == "warn":
        if d["target"]:
            await issue_warn(d["chat_id"], d["target"], await name_of(d["chat_id"], d["target"]), cb.from_user.id, "report")
        res = "target warned"
    elif p[1] == "fake":
        status, res = "fake", "marked FAKE"
        for rep in d.get("reporters") or [d["reporter"]]:
            await db.users.update_one({"_id": rep}, {"$inc": {"fake_reports": 1}})
    r = await db.reports.update_one({"_id": p[2], "status": "open"}, {"$set": {"status": status, "outcome": res, "by": cb.from_user.id}})
    if r.modified_count:
        await db.audit(d["chat_id"], cb.from_user.id, d["target"], "report_" + p[1], "ok")
        await edit(cb, cb.message.html_text + f"\n\n{'🚫' if status == 'fake' else '✅'} <b>{res.title()}</b> by {mention(cb.from_user.id, cb.from_user.full_name)}"
                   + ("\n<i>Reporter flagged — 3 fake reports = /report blocked.</i>" if status == "fake" else ""))
    await cb.answer("Done")


async def send_report_digest():
    """Twice a day: scan open reports and post them to each group."""
    now = time.time()
    await db.reports.update_many({"status": "open", "ts": {"$lt": now - 3 * 86400}}, {"$set": {"status": "expired"}})
    q = {"status": "open", "kind": {"$ne": "call"}, "target": {"$gt": 0}}
    for cid in await db.reports.distinct("chat_id", q):
        try:
            docs = [d async for d in db.reports.find({**q, "chat_id": cid}).sort("ts", 1).limit(10)]
            if not docs:
                continue
            gd = await db.groups.find_one({"_id": cid}) or {}
            lines = []
            for i, d in enumerate(docs, 1):
                lk = msg_link(cid, gd.get("username"), d["msg_id"]) if d.get("msg_id") else None
                lines.append(f"<b>{i}.</b> {mention(d['target'], await name_of(cid, d['target']))} — {esc(d['reason']) or 'no reason'} · 👥{len(d.get('reporters') or [1])}"
                             + (f" · <a href=\"{lk}\">view</a>" if lk else ""))
            admins = [a for a in (await chat_admins(cid)).values() if not a.user.is_bot][:5]
            pings = " ".join(f'<a href="tg://user?id={a.user.id}">\u2063</a>' for a in admins)
            body = (f"📋 <b>{len(docs)} open report(s)</b> need review\n\n" + "\n".join(lines)
                    + "\n\n<i>Fake reports are auto-filtered: duplicates merged, staff/bots can't be reported.</i>" + pings)
            await bot.send_message(cid, await panel(cid, "REPORT DIGEST", body),
                                   reply_markup=mk([[("✅ Mark all reviewed", f"rp:all:{cid}")]]))
        except TelegramAPIError as e:
            log.info("digest skipped for %s: %s", cid, cfg.redact(e))
        await asyncio.sleep(0.3)


# ═════════════════════════ ZOMBIES / BANALL / CALLPM ═════════════════════════
PENDING = {}  # token -> dict (zombies, banall, broadcast)


async def start_telethon():
    global tl
    if not (cfg.API_ID and cfg.API_HASH):
        log.info("Member sync disabled (set API_ID + API_HASH to enable /sync)")
        return
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        c = TelegramClient(StringSession(), cfg.API_ID, cfg.API_HASH, receive_updates=False, flood_sleep_threshold=30)
        await c.start(bot_token=cfg.BOT_TOKEN)
        tl = c
        log.info("Member sync ready (Telethon / MTProto, bot token)")
    except Exception as e:
        tl = None
        log.warning("Member sync unavailable: %s", cfg.redact(e))


async def sync_members(chat_id):
    """Fetch the member list over MTProto (bot must be admin). Returns dict or None when disabled."""
    if tl is None:
        return None
    rows, deleted = [], []
    try:
        async for u in tl.iter_participants(chat_id):
            if getattr(u, "bot", False):
                continue
            dele = bool(getattr(u, "deleted", False))
            rows.append((u.id, "Deleted Account" if dele else u.first_name, u.last_name, u.username, getattr(u, "premium", False), dele))
            if dele:
                deleted.append(u.id)
        await db.bulk_members(chat_id, rows)
    except Exception as e:
        log.warning("sync_members(%s) failed: %s", chat_id, cfg.redact(e))
        return {"error": cfg.redact(e)[:160], "fetched": len(rows), "deleted": deleted}
    return {"fetched": len(rows), "deleted": deleted}


async def kick_many(chat_id, ids, ban=False):
    ok = fail = 0
    for u in ids:
        try:
            await bot.ban_chat_member(chat_id, u)
            if not ban:
                await bot.unban_chat_member(chat_id, u, only_if_banned=True)
            await db.forget_member(chat_id, u)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            fail += 1
        except TelegramAPIError:
            fail += 1
        await asyncio.sleep(0.05)
    return ok, fail


async def scan_zombies(chat_id, progress=None):
    """Returns (scanned, deleted_ids, already_left, complete_list)."""
    res = await sync_members(chat_id)
    if res and not res.get("error"):
        return res["fetched"], res["deleted"], 0, True
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
    return len(ids), found, gone[0], False


async def cmd_zombies(m: Message, args):
    if not await mod_guard(m, "can_restrict_members", "can_restrict_members"):
        return
    msg = await say(m, await panel(m.chat.id, "ZOMBIE SCAN", "🧟 Scanning members for deleted accounts…"))

    async def prog(done, total):
        try:
            await msg.edit_text(await panel(m.chat.id, "ZOMBIE SCAN", f"🧟 Scanning… <b>{done}/{total}</b>"))
        except TelegramAPIError:
            pass

    total, found, gone, full = await scan_zombies(m.chat.id, prog)
    note = ("\n\n<i>✅ Full member list scanned.</i>" if full else
            "\n\n<i>Only members the bot has seen could be checked (no API_ID/API_HASH, or sync failed).</i>")
    head = kv("🔎", "Scanned", f"<b>{total}</b>") + "\n"
    if not found:
        return await msg.edit_text(await panel(m.chat.id, "ZOMBIE SCAN", head + kv("🧟", "Deleted accounts", "<b>0</b> ✅") + note))
    await msg.edit_text(await panel(m.chat.id, "ZOMBIE CLEAN", head + kv("🧟", "Deleted accounts", f"<b>{len(found)}</b>") + "\n🧹 Removing them now…"))
    ok, fail = await kick_many(m.chat.id, found)
    await db.audit(m.chat.id, m.from_user.id, 0, "zombies", "ok", f"{ok} removed / {fail} failed")
    await msg.edit_text(await panel(m.chat.id, "ZOMBIES CLEANED", head + kv("🧟", "Deleted accounts", f"<b>{len(found)}</b>") + "\n"
                                    + kv("✅", "Removed", f"<b>{ok}</b>") + "\n" + kv("❌", "Failed", f"<b>{fail}</b>") + note))


async def cmd_sync(m: Message, args):
    if not await mod_guard(m, "can_restrict_members"):
        return
    if tl is None:
        return await say(m, await panel(m.chat.id, "MEMBER SYNC", "ℹ️ Member sync is <b>not enabled</b>.\nOwner: add <code>API_ID</code> and <code>API_HASH</code> "
                                        "(from my.telegram.org) in Railway Variables and redeploy."))
    msg = await say(m, await panel(m.chat.id, "MEMBER SYNC", "🔄 Fetching the member list over MTProto…"))
    res = await sync_members(m.chat.id)
    try:
        total = await bot.get_chat_member_count(m.chat.id)
    except TelegramAPIError:
        total = "?"
    rows = [kv("👥", "Fetched", f"<b>{res['fetched']} / {total}</b>"), kv("🧟", "Deleted accounts", f"<b>{len(res['deleted'])}</b>")]
    if res.get("error"):
        rows.append(kv("⚠️", "Stopped early", esc(res["error"])))
    elif isinstance(total, int) and res["fetched"] < total * 0.9:
        rows.append("\n<i>Telegram returned fewer members than the group has (it hides some in very large groups). The rest are tracked as they chat/join.</i>")
    await msg.edit_text(await panel(m.chat.id, "MEMBER SYNC", "\n".join(rows)))


async def cmd_banall(m: Message, args):
    """Emergency: /banall yes  (Owner / Sudo only, runs immediately)."""
    if not is_sudo(m.from_user.id):
        return
    if not await need_group(m):
        return
    err = await bot_need(m.chat.id, "can_restrict_members")
    if err:
        return await say(m, "❌ " + err)
    admins = await chat_admins(m.chat.id)
    ids = [u for u in await db.tracked_ids(m.chat.id, 20000) if u not in admins and not is_sudo(u) and u != bot.id]
    if (args or "").strip().lower() != "yes":
        return await say(m, await panel(m.chat.id, "BAN ALL", f"☢️ This bans <b>{len(ids)}</b> tracked non-admin members at once.\n"
                                        "To run it send:\n<code>/banall yes</code>"))
    if not ids:
        return await say(m, "ℹ️ No tracked non-admin members.")
    msg = await say(m, await panel(m.chat.id, "BAN ALL", f"☢️ Banning <b>{len(ids)}</b> members…"))
    ok, fail = await kick_many(m.chat.id, ids, ban=True)
    await db.audit(m.chat.id, m.from_user.id, 0, "banall", "ok", f"{ok} ok / {fail} failed")
    await msg.edit_text(await panel(m.chat.id, "BAN ALL DONE", kv("✅", "Banned", f"<b>{ok}</b>") + "\n" + kv("❌", "Failed", f"<b>{fail}</b>")))


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





GIFT_DEFAULT = ("🧸 Teddy aur bhi mast gifts jeetne ka mauka!\n\n1️⃣ Neeche <b>Participate Now</b> dabao\n"
                "2️⃣ Bot ke DM mein <b>START</b> dabao\n3️⃣ Bas! Aap giveaway mein shamil 🎉\n\n⏳ Jaldi karo — winner jald announce hoga!")


async def gift_join(m: Message):
    u = m.from_user
    r = await db.users.update_one({"_id": u.id, "gift": {"$ne": True}}, {"$set": {"gift": True, "gift_ts": time.time()}})
    n = await db.users.count_documents({"gift": True})
    body = "\n".join([kv("🎁", "Giveaway", "<b>You're in!</b>"), kv("✅", "Status", "Participant"),
                      kv("🎟", "Entry", f"<b>#{n}</b>" if r.modified_count else "already registered"),
                      "\nWinner will be announced in the groups — stay tuned! 🍀"])
    await say_card(m, u.id, await panel(0, "GIVEAWAY", body), mk([[("📖 Help", "h:home")]]))


async def cmd_giftcast(m, args):
    await _start_bc(m, args, "gift")


async def cmd_giftlist(m, args):
    if not await need_dm_sudo(m):
        return
    n = await db.users.count_documents({"gift": True})
    rows = [u async for u in db.users.find({"gift": True}).sort("gift_ts", -1).limit(15)]
    lines = [f"🎟 {mention(u['_id'], u.get('first_name'))}" + (f" · @{esc(u['username'])}" if u.get("username") else "") for u in rows]
    await say(m, await panel(0, "GIVEAWAY ENTRIES", f"👥 Participants: <b>{n}</b>\n\n" + ("\n".join(lines) or "No entries yet.") + "\n\n/giftwinner [n] · /giftreset"))


async def cmd_giftwinner(m, args):
    if not await need_dm_sudo(m):
        return
    n = int(args) if (args or "").strip().isdigit() else 1
    n = max(1, min(n, 10))
    win = [u async for u in db.users.aggregate([{"$match": {"gift": True, "blocked": {"$ne": True}}}, {"$sample": {"size": n}}])]
    lines = [f"🏆 {mention(u['_id'], u.get('first_name'))} · <code>{u['_id']}</code>" + (f" · @{esc(u['username'])}" if u.get("username") else "") for u in win]
    await say(m, await panel(0, "WINNER" + ("S" if n > 1 else ""), "\n".join(lines) or "No participants yet."))


async def cmd_giftreset(m, args):
    if not is_owner(m.from_user.id) or not await need_dm_sudo(m):
        return
    r = await db.users.update_many({"gift": True}, {"$unset": {"gift": "", "gift_ts": ""}})
    await say(m, f"♻️ Giveaway reset — {r.modified_count} entries cleared.")


async def cb_daily(cb: CallbackQuery, p):
    uid = cb.from_user.id
    d = await db.get_user(uid) or {}
    nv = not d.get("daily_line", False)
    await db.users.update_one({"_id": uid}, {"$set": {"daily_line": nv}})
    await cb.answer("🌅 Daily shayari / joke: ON ✅ (every morning)" if nv else "🌅 Daily shayari: OFF", show_alert=True)


async def send_daily_lines():
    async for u in db.users.find({"daily_line": True, "started": True, "blocked": {"$ne": True}}, {"_id": 1}):
        try:
            await bot.send_message(u["_id"], await panel(0, "GOOD MORNING", f"🌅 <i>{esc(pick_line())}</i>"))
        except TelegramForbiddenError:
            await db.set_blocked(u["_id"], True)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramAPIError:
            pass
        await asyncio.sleep(0.06)


async def maybe_scheduled():
    now = datetime.now(cfg.TZ)
    meta = await db.get_meta()
    if now.hour in cfg.DIGEST_HOURS and now.minute < 10:
        key = now.strftime("%Y-%m-%d-%H")
        if meta.get("digest_last") != key:
            await db.set_meta(digest_last=key)
            spawn(send_report_digest())
    if now.hour == 9 and now.minute < 10:
        key = now.strftime("%Y-%m-%d")
        if meta.get("daily_last") != key:
            await db.set_meta(daily_last=key)
            spawn(send_daily_lines())


# ═════════════════════════ /all  (tag everyone) ═════════════════════════
_all_last, _all_stop, _all_running = {}, set(), set()
_ROLE_ICON = {"owner": "👑", "vip": "💎", "admin": "🛡", "member": "▫️"}


def tagname(uid, first, username):
    return ("@" + esc(username)) if username else mention(uid, first or "Member")


async def all_targets(cid, sender):
    admins = await chat_admins(cid)
    vips = {v["user_id"] for v in await db.list_vips(cid)}
    owner_id = next((u for u, a in admins.items() if a.status == "creator"), 0)
    rows = []
    async for d in db.users.find({"groups": cid, "deleted": {"$ne": True}}, {"first_name": 1, "username": 1}).limit(20000):
        uid = d["_id"]
        if uid in (sender, bot.id):
            continue
        role = ("owner" if (uid == owner_id or is_sudo(uid)) else "vip" if uid in vips else "admin" if uid in admins else "member")
        rows.append((uid, d.get("first_name") or "Member", d.get("username"), role))
    rows.sort(key=lambda r: ("owner", "vip", "admin", "member").index(r[3]))
    return rows


async def _send_tag(cid, text):
    for _ in range(2):
        try:
            await bot.send_message(cid, text)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramAPIError:
            return False
    return False


async def tag_single(cid, role, tag, f):
    ln = esc(fx(rline("tag"), f))  # funny line in a random one of the 20 fonts
    if role == "owner":
        return await panel(cid, "ROYAL CALL", f"👑 {tag}\n<i>{ln}</i>")
    if role == "vip":
        return await panel(cid, "VIP CALL", f"💎 {tag}\n<i>{ln}</i>")
    if role == "admin":
        return f"🛡 {tag}\n<i>{ln}</i>"
    return f"👀 {tag} — {ln}"


async def run_all(cid, rows, text, batch, note_mid):
    _all_running.add(cid)
    _all_stop.discard(cid)
    sent = 0
    try:
        if text:
            for i in range(0, len(rows), batch):
                if cid in _all_stop:
                    break
                chunk = rows[i:i + batch]
                f = random.randrange(len(FONTS))
                body = f"📣 <b>{esc(fx(text, f))}</b>\n\n" + "\n".join(f"{_ROLE_ICON[r]} {tagname(u, n, un)}" for u, n, un, r in chunk)
                if not await _send_tag(cid, body):
                    break
                sent += len(chunk)
                await asyncio.sleep(3)
        else:
            for uid, first, un, role in rows:
                if cid in _all_stop:
                    break
                msg = await tag_single(cid, role, tagname(uid, first, un), random.randrange(len(FONTS)))
                if not await _send_tag(cid, msg):
                    break
                sent += 1
                await asyncio.sleep(3)
    finally:
        _all_running.discard(cid)
        stopped = cid in _all_stop
        _all_stop.discard(cid)
    await safe_delete(cid, note_mid)
    await temp(cid, await panel(cid, "TAG ALL", kv("✅" if not stopped else "🛑", "Tagged", f"<b>{sent}</b> / {len(rows)}")
                                + ("\n<i>Stopped by admin.</i>" if stopped else "")), 15)


async def cmd_all(m: Message, args):
    """/all  → random funny tag, one member at a time.   /all <text> → your text, N members per message (settings)."""
    if not await need_group(m):
        return
    if not m.from_user or m.from_user.id == ANON or m.sender_chat:
        return await say(m, "⚠️ Anonymous admin detected. Turn off <i>Remain Anonymous</i> to use this command.")
    cid, uid = m.chat.id, m.from_user.id
    if not await actor_can(cid, uid):
        return await say(m, "⛔ Only admins can use /all.")
    if cid in _all_running:
        return await say(m, "⏳ A tag is already running. /stopall to stop it.")
    left = 600 - (time.time() - _all_last.get(cid, 0))
    if left > 0 and not is_sudo(uid):
        return await say(m, f"⏳ Cooldown — try again in <b>{fmt_secs(left)}</b>.")
    text = (args or "").strip()[:600]
    g = await db.get_group(cid)
    batch = g["all_batch"]
    note = await say(m, await panel(cid, "TAG ALL", "🔄 Syncing members…"))
    if tl is not None:
        await sync_members(cid)
    rows = await all_targets(cid, uid)
    if not rows:
        return await note.edit_text(await panel(cid, "TAG ALL", "ℹ️ No members known yet. Admins: /sync (needs API_ID + API_HASH) or wait until people chat."))
    _all_last[cid] = time.time()
    eta = (len(rows) * 3) if not text else (-(-len(rows) // batch) * 3)
    await note.edit_text(await panel(cid, "TAG ALL", kv("👥", "Members", f"<b>{len(rows)}</b>")
                                     + "\n" + kv("📦", "Mode", "1 by 1 (random lines)" if not text else f"{batch} per message")
                                     + "\n" + kv("⏱", "ETA", f"<b>{fmt_secs(eta)}</b>") + "\n\n/stopall to stop"))
    await safe_delete(cid, m.message_id)
    spawn(run_all(cid, rows, text, batch, note.message_id))


async def cmd_stopall(m: Message, args):
    if not await need_group(m):
        return
    if not await actor_can(m.chat.id, m.from_user.id):
        return await say(m, "⛔ Admins only.")
    if m.chat.id not in _all_running:
        return await say(m, "ℹ️ No tag-all is running.")
    _all_stop.add(m.chat.id)
    await say(m, "🛑 Stopping after the current message…")


# ═════════════════════════ /check  (group health) ═════════════════════════
async def cmd_check(m: Message, args):
    if not await need_group(m):
        return
    if not await actor_can(m.chat.id, m.from_user.id):
        return await say(m, "⛔ Admins only.")
    cid = m.chat.id
    g = await db.get_group(cid)
    try:
        me = await bot.get_chat_member(cid, bot.id)
    except TelegramAPIError as e:
        return await say(m, "❌ " + tg_err(e))
    adm = me.status == "administrator"

    def right(attr, label):
        return kv("✅" if adm and getattr(me, attr, False) else "❌", label, "OK" if adm and getattr(me, attr, False) else "<b>missing</b>")

    n = await db.users.count_documents({"groups": cid})
    rows = [kv("✅" if adm else "❌", "Bot is admin", "yes" if adm else "<b>NO — make me admin</b>"),
            right("can_delete_messages", "Delete messages"), right("can_restrict_members", "Restrict / ban"),
            right("can_pin_messages", "Pin messages"), right("can_invite_users", "Invite users (chat_member updates)"),
            right("can_promote_members", "Add new admins (/promote)"), "",
            kv("🔐", "Verify tags", onoff(g["verify_on"]) + (" (via /verifyall)" if g.get("verify_by_all") else "")),
            kv("👋", "Welcome card", onoff(g["welcome"])), kv("👥", "Members known", f"<b>{n}</b>"),
            kv("🔄", "Full member sync", "ready (/sync)" if tl else "off — set API_ID + API_HASH"),
            kv("🛡", "Protection", "🟢 always on")]
    if not adm:
        rows.append("\n⚠️ Without admin rights verification tags, protection and member tracking can't work.")
    await say(m, await panel(cid, "GROUP CHECK", "\n".join(rows)))


# ═════════════════════════ /verifyall  (Owner, DM) ═════════════════════════
async def set_verifyall(on):
    if on:
        r = await db.groups.update_many({"$or": [{"active": True}, {"active": {"$exists": False}}], "s.verify_on": {"$ne": True}},
                                        {"$set": {"s.verify_on": True, "s.verify_by_all": True}})
    else:
        r = await db.groups.update_many({"s.verify_by_all": True}, {"$set": {"s.verify_on": False}, "$unset": {"s.verify_by_all": ""}})
    await db.set_meta(verifyall=bool(on))
    db._gc.clear()
    return r.modified_count


async def cmd_verifyall(m: Message, args):
    if m.chat.type != "private":
        return await say(m, "ℹ️ /verifyall works in the bot DM (Owner only). In a group use /verify on|off.")
    if not is_owner(m.from_user.id):
        return await say(m, "⛔ Owner only.")
    a = (args or "").strip().lower()
    meta = await db.get_meta()
    if a in ("on", "off"):
        n = await set_verifyall(a == "on")
        if a == "on":
            return await say(m, await panel(0, "VERIFY ALL", kv("🟢", "Verify tags", "<b>ON everywhere</b>") + "\n" + kv("🆕", "Switched on now", f"<b>{n}</b>")
                                            + "\n\n<i>New groups also start with verify ON.\n/verifyall off only turns OFF the groups it switched on.</i>"))
        kept = await db.groups.count_documents({"s.verify_on": True})
        return await say(m, await panel(0, "VERIFY ALL", kv("⚪", "Verify tags", "<b>OFF</b> (groups /verifyall switched on)") + "\n" + kv("🔕", "Switched off now", f"<b>{n}</b>")
                                        + "\n" + kv("🔐", "Still ON (set by group admins)", f"<b>{kept}</b>")))
    on_groups = await db.groups.count_documents({"s.verify_on": True})
    await say(m, await panel(0, "VERIFY ALL", kv("🌍", "Global switch", "🟢 ON" if meta.get("verifyall") else "⚪ OFF") + "\n"
                             + kv("🔐", "Groups with verify ON", f"<b>{on_groups}</b>") + "\n\n<code>/verifyall on</code> · <code>/verifyall off</code>"))


# ═════════════════════════ /cmds  (Owner: switch commands on/off + who can use them) ═════════════════════════
PROTECTED_CMDS = {"start", "help", "cmds", "lines", "look", "godmode", "verifyall", "owner", "sudo", "backup", "gift"}
_PERM_NEXT = {"default": "admin", "admin": "owner", "owner": "default"}
_PERM_ICON = {"default": "🌐", "admin": "🛡", "owner": "👑"}


async def cmd_rule(cid, cmd):
    meta = await db.get_meta()
    r = dict((meta.get("cmdcfg") or {}).get(cmd, {}))
    if cid:
        r.update((await db.get_group(cid)).get("_cmdcfg", {}).get(cmd, {}))
    return bool(r.get("off")), r.get("perm", "default")


async def rule_blocks(m: Message, name):
    """Returns a message when the command is switched off / restricted for this user, else None."""
    if name in PROTECTED_CMDS or not m.from_user:
        return None
    uid = m.from_user.id
    if is_owner(uid):
        return None
    cid = m.chat.id if m.chat.type in GROUPS else 0
    off, perm = await cmd_rule(cid, name)
    if off:
        return f"⛔ /{name} is turned off."
    if perm == "owner" and not is_sudo(uid):
        return "⛔ Owner / Sudo only."
    if perm == "admin" and not (is_sudo(uid) or (cid and await actor_can(cid, uid))):
        return "⛔ Admins only."
    return None


async def cmds_view(scope, page=0):
    cid = 0 if scope == "g" else int(scope)
    names = sorted(k for k in COMMANDS if k not in PROTECTED_CMDS)
    per = 10
    pages = max(1, -(-len(names) // per))
    page = max(0, min(page, pages - 1))
    rows = []
    for nm in names[page * per:(page + 1) * per]:
        off, perm = await cmd_rule(cid, nm)
        rows.append([(f"{'❌' if off else '✅'} /{nm}", f"cc:t:{scope}:{nm}"), (f"{_PERM_ICON[perm]} {perm}", f"cc:p:{scope}:{nm}")])
    nav = []
    if page > 0:
        nav.append(("⬅️", f"cc:pg:{scope}:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(("➡️", f"cc:pg:{scope}:{page + 1}"))
    rows += [nav, [("🏠 Owner panel", "ow:home"), ("❌ Close", "x")]]
    where = "🌍 <b>All groups</b>" if not cid else f"🏘 <b>{esc((await db.get_group(cid))['_title'])}</b>"
    body = (f"{where}\n\nTap the left button = ✅ ON / ❌ OFF.\nTap the right button = who may use it: 🌐 default · 🛡 admins only · 👑 owner/sudo only.\n"
            "<i>These can only restrict a command, never open an admin command to members. /start /help /look /cmds /lines /godmode are locked ON.</i>")
    return await panel(0, "COMMAND CONTROL", body), mk(rows)


async def cmd_cmds(m: Message, args):
    if m.chat.type != "private":
        return await say(m, "🎛 Command control is in the bot DM (Owner only).")
    if not is_owner(m.from_user.id):
        return await say(m, "⛔ Owner only.")
    a = (args or "").strip()
    scope = a if a.lstrip("-").isdigit() and a not in ("0", "-0") else "g"
    text, kb = await cmds_view(scope, 0)
    await say(m, text, kb)


async def cb_cmds(cb: CallbackQuery, p):
    if cb.message.chat.type != "private" or not is_owner(cb.from_user.id):
        return await cb.answer("⛔ Owner only (bot DM).", show_alert=True)
    act, scope = p[1], p[2]
    page = int(p[3]) if act == "pg" else 0
    if act in ("t", "p"):
        nm = p[3]
        if nm not in COMMANDS or nm in PROTECTED_CMDS:
            return await cb.answer("Locked command.", show_alert=True)
        cid = 0 if scope == "g" else int(scope)
        off, perm = await cmd_rule(cid, nm)
        rule = {"off": (not off) if act == "t" else off, "perm": perm if act == "t" else _PERM_NEXT[perm]}
        if cid:
            await db.groups.update_one({"_id": cid}, {"$set": {f"cmdcfg.{nm}": rule}}, upsert=True)
            db._gc.pop(cid, None)
        else:
            cfg_all = dict((await db.get_meta()).get("cmdcfg") or {})
            cfg_all[nm] = rule
            await db.set_meta(cmdcfg=cfg_all)
        await db.audit(cid, cb.from_user.id, 0, "cmd_rule", "ok", f"{nm} {rule}")
        names = sorted(k for k in COMMANDS if k not in PROTECTED_CMDS)
        page = names.index(nm) // 10
    text, kb = await cmds_view(scope, page)
    await edit(cb, text, kb)
    await cb.answer()


# ═════════════════════════ /lines  (Owner: customise funny lines) ═════════════════════════
_await_line = {}


async def lines_home():
    rows = [[(f"{LINE_TITLES[k]} ({len(LINES_DEFAULT[k])}+{len(_custom_lines.get(k, []))})", f"ln:c:{k}")] for k in LINES_DEFAULT]
    rows.append([("🏠 Owner panel", "ow:home"), ("❌ Close", "x")])
    return await panel(0, "FUNNY LINES", "Pick a list to add your own lines.\nNumbers = built-in + yours."), mk(rows)


async def lines_view(cat):
    mine = _custom_lines.get(cat, [])
    body = (f"<b>{LINE_TITLES[cat]}</b>\nBuilt-in: <b>{len(LINES_DEFAULT[cat])}</b> · Yours: <b>{len(mine)}</b>\n\n"
            + ("\n".join(f"• {x}" for x in mine[-8:]) or "<i>No custom lines yet.</i>")
            + ("\n\n<i>Use {user} where the name should go.</i>" if cat == "callout" else ""))
    return await panel(0, "FUNNY LINES", body), mk([[("➕ Add line", f"ln:a:{cat}"), ("↩️ Remove last", f"ln:r:{cat}")],
                                                    [("♻️ Clear mine", f"ln:x:{cat}"), ("⬅️ Back", "ln:h:-")]])


async def cmd_lines(m: Message, args):
    if m.chat.type != "private":
        return await say(m, "✍️ Funny-line editor is in the bot DM (Owner only).")
    if not is_owner(m.from_user.id):
        return await say(m, "⛔ Owner only.")
    text, kb = await lines_home()
    await say(m, text, kb)


async def _save_lines():
    await db.set_meta(lines=_custom_lines)


async def cb_lines(cb: CallbackQuery, p):
    if cb.message.chat.type != "private" or not is_owner(cb.from_user.id):
        return await cb.answer("⛔ Owner only (bot DM).", show_alert=True)
    act, cat = p[1], p[2]
    if act == "h":
        _await_line.pop(cb.from_user.id, None)
        text, kb = await lines_home()
    elif cat not in LINES_DEFAULT:
        return await cb.answer("Invalid", show_alert=True)
    else:
        if act == "a":
            _await_line[cb.from_user.id] = cat
            await cb.answer("Send the new line as your next message.", show_alert=True)
            return await edit(cb, await panel(0, "FUNNY LINES", f"✍️ Send me the new line for <b>{LINE_TITLES[cat]}</b> now (one message, max 200 chars).\n"
                                              + ("Use <code>{user}</code> for the name.\n" if cat == "callout" else "") + "\nSend any /command to cancel."),
                              mk([[("⬅️ Back", f"ln:c:{cat}")]]))
        if act == "r" and _custom_lines.get(cat):
            _custom_lines[cat].pop()
            await _save_lines()
        elif act == "x":
            _custom_lines.pop(cat, None)
            await _save_lines()
        _await_line.pop(cb.from_user.id, None)
        text, kb = await lines_view(cat)
    await edit(cb, text, kb)
    await cb.answer()


async def take_line(m: Message):
    """Owner's next DM message after pressing ➕ Add line."""
    cat = _await_line.pop(m.from_user.id, None)
    t = (m.text or "").strip()
    if not cat or not t:
        return
    if len(t) > 200:
        _await_line[m.from_user.id] = cat
        return await say(m, "❌ Too long (max 200 characters). Send a shorter one.")
    _custom_lines.setdefault(cat, []).append(esc(t) if cat in ("callout", "tag") else t)
    await _save_lines()
    text, kb = await lines_view(cat)
    await say(m, "✅ Added!\n\n" + text, kb)


# ═════════════════════════ FILTERS (/filter  /filters  /stop) ═════════════════════════
FILTER_MAX = 50
_filter_cache, _filter_cool = {}, {}
_MEDIA_KINDS = (("photo", lambda r: r.photo[-1].file_id if r.photo else None), ("video", lambda r: r.video.file_id if r.video else None),
                ("animation", lambda r: r.animation.file_id if r.animation else None), ("document", lambda r: r.document.file_id if r.document else None),
                ("sticker", lambda r: r.sticker.file_id if r.sticker else None), ("voice", lambda r: r.voice.file_id if r.voice else None),
                ("audio", lambda r: r.audio.file_id if r.audio else None))


def _fname(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())[:30]


def _split_name(args):
    a = (args or "").strip()
    mo = re.match(r'^"([^"]{1,30})"\s*(.*)$', a, re.S)
    if mo:
        return _fname(mo.group(1)), mo.group(2).strip()
    parts = a.split(None, 1)
    return (_fname(parts[0]), parts[1].strip() if len(parts) > 1 else "") if parts else ("", "")


async def cmd_filter(m: Message, args):
    """/filter hello (reply to a message)  ·  /filter hello Hi there!  ·  /filter "good morning" text"""
    if not await mod_guard(m, None):
        return
    name, rest = _split_name(args)
    if not name:
        return await say(m, "Usage: reply to a message with <code>/filter name</code>\nor <code>/filter name your reply text</code>\n"
                            "Several words: <code>/filter \"good morning\" Hello!</code>\nSee all: /filters · remove: <code>/stop name</code>")
    r = m.reply_to_message
    doc = {"text": "", "media": None, "by": m.from_user.id, "ts": time.time()}
    if r:
        doc["text"] = (r.html_text if (r.text or r.caption) else "") or ""
        for kind, getter in _MEDIA_KINDS:
            fid = getter(r)
            if fid:
                doc["media"] = {"type": kind, "file_id": fid}
                break
    elif rest:
        doc["text"] = esc(rest)
    if not doc["text"] and not doc["media"]:
        return await say(m, "❌ Reply to a message (text or media) or write the reply text after the name.")
    existing = {f["name"] for f in await db.list_filters(m.chat.id)}
    if name not in existing and len(existing) >= FILTER_MAX:
        return await say(m, f"❌ Filter limit reached ({FILTER_MAX}). Remove one with /stop name.")
    await db.set_filter(m.chat.id, name, doc)
    _filter_cache.pop(m.chat.id, None)
    await db.audit(m.chat.id, m.from_user.id, 0, "filter_set", "ok", name)
    await say(m, await panel(m.chat.id, "FILTER SAVED", kv("🧷", "Trigger", f"<b>{esc(name)}</b>") + "\n"
                             + kv("📎", "Reply", (doc["media"]["type"] + " + text") if doc["media"] and doc["text"] else (doc["media"]["type"] if doc["media"] else "text"))
                             + "\n\n<i>Anyone writing it gets this reply · /filters · /stop " + esc(name) + "</i>"))


async def cmd_filters(m: Message, args):
    if not await need_group(m):
        return
    fl = await db.list_filters(m.chat.id)
    lines = [f"🧷 <b>{esc(f['name'])}</b>" + (f" · 📎{f['media']['type']}" if f.get("media") else "") for f in fl]
    await say(m, await panel(m.chat.id, "FILTERS", (f"<b>{len(fl)}</b> / {FILTER_MAX}\n\n" + "\n".join(lines)) if lines
                             else "No filters yet.\nAdmins: reply to a message with <code>/filter name</code>."))


async def cmd_stopfilter(m: Message, args):
    """/stop name — switch a filter off in this group."""
    if not await mod_guard(m, None):
        return
    name, _ = _split_name(args)
    if not name:
        return await say(m, "Usage: <code>/stop name</code> — see names with /filters")
    ok = await db.del_filter(m.chat.id, name)
    _filter_cache.pop(m.chat.id, None)
    if ok:
        await db.audit(m.chat.id, m.from_user.id, 0, "filter_stop", "ok", name)
    await say(m, await panel(m.chat.id, "FILTER STOPPED" if ok else "FILTER", f"{'🛑' if ok else 'ℹ️'} <b>{esc(name)}</b> " + ("is stopped." if ok else "— no such filter.")))


async def run_filters(m: Message):
    text = (m.text or m.caption or "").lower()
    if not text or (m.chat.id, m.message_id) in _deleted_mids:
        return
    c = _filter_cache.get(m.chat.id)
    if not c or time.time() - c[0] > 60:
        c = (time.time(), await db.list_filters(m.chat.id))
        _filter_cache[m.chat.id] = c
    for f in c[1]:
        if re.search(r"(?<!\w)" + re.escape(f["name"]) + r"(?!\w)", text):
            k = (m.chat.id, f["name"])
            if time.time() - _filter_cool.get(k, 0) < 8:
                return
            _filter_cool[k] = time.time()
            med = f.get("media")
            try:
                if med:
                    send = getattr(bot, "send_" + med["type"])
                    kw = {"caption": f["text"] or None} if med["type"] in ("photo", "video", "animation", "document", "audio", "voice") else {}
                    return await send(m.chat.id, med["file_id"], reply_to_message_id=m.message_id, **kw)
                return await bot.send_message(m.chat.id, f["text"], reply_to_message_id=m.message_id)
            except TelegramAPIError:
                return


# ═════════════════════════ /appeal ═════════════════════════
_await_appeal = {}


async def cmd_appeal(m: Message, args):
    if m.chat.type != "private":
        return await say(m, "✉️ Open my DM and send /appeal there.")
    uid = m.from_user.id
    rows = []
    for c in await db.user_bans(uid):
        try:
            mem = await bot.get_chat_member(c, uid)
        except TelegramAPIError:
            continue
        if mem.status == "kicked":
            rows.append([(f"🏘 {(await db.get_group(c))['_title'][:40]}", f"ap:s:{abs(c)}")])
    if not rows:
        return await say(m, await panel(0, "APPEAL", "✅ You are not banned in any group I manage."))
    await say(m, await panel(0, "APPEAL", "Choose the group you want to appeal in 👇"), mk(rows + [[("❌ Close", "x")]]))


async def take_appeal(m: Message):
    cid = _await_appeal.pop(m.from_user.id, None)
    reason = (m.text or "").strip()
    if not cid or not reason:
        return
    if len(reason) > 500:
        _await_appeal[m.from_user.id] = cid
        return await say(m, "❌ Too long (max 500 characters). Send a shorter reason.")
    u = m.from_user
    recent = await db.appeals.find_one({"chat_id": cid, "user_id": u.id, "ts": {"$gt": time.time() - 86400}})
    if recent:
        return await say(m, "⏳ You already appealed in the last 24 hours. Please wait for the admins.")
    aid = secrets.token_urlsafe(6)
    await db.appeals.insert_one({"_id": aid, "chat_id": cid, "user_id": u.id, "name": u.full_name, "reason": reason, "status": "open",
                                 "ts": time.time(), "purge_at": utc(time.time() + 14 * 86400)})
    admins = [a for a in (await chat_admins(cid)).values() if not a.user.is_bot][:5]
    pings = " ".join(f'<a href="tg://user?id={a.user.id}">\u2063</a>' for a in admins)
    body = (f"✉️ {mention(u.id, u.full_name)} asks for an <b>unban</b>\n🆔 <code>{u.id}</code>\n\n📝 {esc(reason)}{pings}")
    try:
        await bot.send_message(cid, await panel(cid, "UNBAN APPEAL", body), reply_markup=mk([[("✅ Accept", f"ap:ok:{aid}"), ("❌ Reject", f"ap:no:{aid}")]]))
    except TelegramAPIError:
        return await say(m, "❌ I couldn't reach that group right now. Try again later.")
    await say(m, await panel(0, "APPEAL SENT", "✅ Your appeal reached the admins. You will get a message when they decide."))


async def cb_appeal(cb: CallbackQuery, p):
    if p[1] == "s":  # user picked a group (DM)
        _await_appeal[cb.from_user.id] = -int(p[2])
        await cb.answer()
        return await edit(cb, await panel(0, "APPEAL", "✍️ Write your appeal now — <b>one message</b>, max 500 characters.\nBe honest and polite."))
    d = await db.appeals.find_one({"_id": p[2]})
    if not d or cb.message.chat.id != d["chat_id"]:
        return await cb.answer("Appeal not found.", show_alert=True)
    if not await actor_can(d["chat_id"], cb.from_user.id, "can_restrict_members"):
        return await cb.answer("⛔ Admins only.", show_alert=True)
    r = await db.appeals.update_one({"_id": p[2], "status": "open"}, {"$set": {"status": "accepted" if p[1] == "ok" else "rejected", "by": cb.from_user.id}})
    if not r.modified_count:
        return await cb.answer("Already handled.", show_alert=True)
    staff = mention(cb.from_user.id, cb.from_user.full_name)
    if p[1] == "ok":
        ok, t = await svc_unban(d["chat_id"], d["user_id"], d["name"], cb.from_user.id)
        await cb.answer("✅ Unbanned" if ok else "Failed")
        try:
            await bot.send_message(d["user_id"], f"✅ Your appeal in <b>{esc((await db.get_group(d['chat_id']))['_title'])}</b> was <b>accepted</b>. You can join again!")
        except TelegramAPIError:
            pass
        return await edit(cb, t if ok else "❌ Couldn't unban — check my rights.")
    await cb.answer("Rejected")
    try:
        await bot.send_message(d["user_id"], "❌ Your appeal was <b>rejected</b> by the admins.")
    except TelegramAPIError:
        pass
    await edit(cb, await panel(d["chat_id"], "APPEAL REJECTED", f"❌ Rejected by {staff}\n👤 {mention(d['user_id'], d['name'])}"))


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


_owner_wait = {}   # uid -> ("bc", mode) | ("gift",) | ("sudo",) | ("gban",)
SUDO_OK = {"home", "grp", "g", "bc", "gift", "st"}


def _onoff(v):
    return "ON" if v else "OFF"


async def owner_home(uid):
    meta = await db.get_meta()
    try:
        dbms = f"🟢 {await db.ping():.0f} ms"
    except Exception:
        dbms = "🔴 down"
    body = "\n".join([f"🤖 @{esc(BOT_USER)} · v{VERSION}", f"⏱ Uptime: <b>{fmt_secs(time.time() - START)}</b> · 🗄 DB: {dbms}",
                      f"👥 Users: <b>{await db.users.estimated_document_count()}</b> · 🏘 Groups: <b>{len(await db.active_groups())}</b>",
                      f"🔐 Verify all: {onoff(meta.get('verifyall'))} · 🛡 Admin gaali: {onoff(meta.get('admin_bw'))}",
                      f"🛠 Maintenance: {onoff(meta.get('maintenance'))} · 🔄 Member sync: {'ready' if tl else 'off'}",
                      f"⏳ Pending jobs: <b>{await db.jobs.count_documents({'status': 'pending'})}</b>"])
    rows = []
    if is_owner(uid):
        rows += [[("🎛 Commands", "ow:cmds"), ("🎨 Look", "ow:look")],
                 [("✍️ Funny lines", "ow:lines"), (f"🔐 Verify all: {_onoff(meta.get('verifyall'))}", "ow:vall")],
                 [(f"🛡 Admin gaali: {_onoff(meta.get('admin_bw'))}", "ow:abw"), (f"🛠 Maintenance: {_onoff(meta.get('maintenance'))}", "ow:mnt")]]
    rows += [[("🏘 Groups", "ow:grp:0"), ("🎁 Giveaway", "ow:gift")], [("📣 Broadcast", "ow:bc:users"), ("📣 Group cast", "ow:bc:groups")]]
    if is_owner(uid):
        rows += [[("👑 Sudo", "ow:sudo"), ("🚫 Global bans", "ow:gb")], [("💾 Backup", "ow:bk"), ("📊 Stats", "ow:st")]]
    else:
        rows += [[("📊 Stats", "ow:st")]]
    rows.append([("🔄 Refresh", "ow:home"), ("❌ Close", "x")])
    return await panel(0, "CONTROL PANEL", body), mk(rows)


async def cmd_owner(m, args):
    if not await need_dm_sudo(m):
        return
    text, kb = await owner_home(m.from_user.id)
    await say(m, text, kb)


async def stats_view():
    users = await db.users.estimated_document_count()
    started = await db.users.count_documents({"started": True, "blocked": {"$ne": True}})
    body = "\n".join([kv("👥", "Users", f"<b>{users}</b>"), kv("📩", "DM-reachable", f"<b>{started}</b>"),
                      kv("🏘", "Active groups", f"<b>{len(await db.active_groups())}</b>"), kv("🚫", "Global bans", f"<b>{len(db.gban_ids)}</b>"),
                      kv("🔱", "Sudo (DB)", f"<b>{len(db.sudo_ids)}</b>"), kv("🎁", "Giveaway entries", f"<b>{await db.users.count_documents({'gift': True})}</b>"),
                      kv("⏱", "Uptime", f"<b>{fmt_secs(time.time() - START)}</b>")])
    return await panel(0, "BOT STATISTICS", body), mk([[("🏠 Panel", "ow:home"), ("❌ Close", "x")]])


async def groups_view(page):
    ids = await db.active_groups()
    per = 8
    pages = max(1, -(-len(ids) // per))
    page = max(0, min(page, pages - 1))
    rows = [[(f"🏘 {(await db.get_group(c))['_title'][:34]}", f"ow:g:{c}")] for c in ids[page * per:(page + 1) * per]]
    nav = []
    if page > 0:
        nav.append(("⬅️", f"ow:grp:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop"))
    if page < pages - 1:
        nav.append(("➡️", f"ow:grp:{page + 1}"))
    rows += [nav, [("🏠 Panel", "ow:home"), ("❌ Close", "x")]]
    return await panel(0, "GROUPS", f"🏘 <b>{len(ids)}</b> active groups — tap one to manage it."), mk(rows)


async def group_view(cid, owner):
    g = await db.get_group(cid)
    ab = g["admin_bw"]
    body = "\n".join([kv("🏘", "Group", f"<b>{esc(g['_title'])}</b>"), kv("🆔", "ID", f"<code>{cid}</code>"),
                      kv("🔐", "Verify tags", onoff(g["verify_on"]) + (" (via /verifyall)" if g.get("verify_by_all") else "")),
                      kv("🛡", "Admin gaali", "follows global" if ab is None else onoff(ab)), kv("🔒", "Lock", onoff(g["locked"])),
                      kv("🎨", "Theme / font", f"{THEMES[g['theme']]['name']} / {FONTS[g['font']][0]}")])
    rows = []
    if owner:
        rows += [[(f"🔐 Verify: {_onoff(g['verify_on'])}", f"ow:gv:{cid}"), ("🛡 Admin gaali: " + ("global" if ab is None else _onoff(ab)), f"ow:ga:{cid}")],
                 [("🎛 Commands", f"ow:gc:{cid}"), ("🎨 Look", f"ow:gk:{cid}")], [("🚪 Leave group", f"ow:gx:{cid}")]]
    rows.append([("⬅️ Groups", "ow:grp:0"), ("🏠 Panel", "ow:home")])
    return await panel(0, "GROUP CONTROL", body), mk(rows)


async def sudo_view():
    ids = {cfg.OWNER_ID} | cfg.SUDO_IDS | db.sudo_ids
    lines, rows = [], []
    for i in ids:
        d = await db.get_user(i) or {}
        lines.append(f"{'👑' if i == cfg.OWNER_ID else '🔱'} {mention(i, d.get('first_name') or i)} — <code>{i}</code>")
        if i != cfg.OWNER_ID and i in db.sudo_ids and i not in cfg.SUDO_IDS:
            rows.append([(f"➖ Remove {str(d.get('first_name') or i)[:20]}", f"ow:sd:{i}")])
    rows += [[("➕ Add sudo (send ID)", "ow:sa")], [("🏠 Panel", "ow:home")]]
    return await panel(0, "SUDO", "\n".join(lines) + "\n\n<i>Sudo from the SUDO_IDS variable can only be removed in Railway.</i>"), mk(rows)


async def gban_view():
    ids = list(db.gban_ids)[:15]
    lines, rows = [], []
    for i in ids:
        d = await db.get_user(i) or {}
        lines.append(f"🚫 {mention(i, d.get('first_name') or i)} — <code>{i}</code>")
        rows.append([(f"♻️ Unban {str(d.get('first_name') or i)[:20]}", f"ow:gu:{i}")])
    rows += [[("➕ Global ban (send ID + reason)", "ow:gba")], [("🏠 Panel", "ow:home")]]
    return await panel(0, "GLOBAL BANS", (f"<b>{len(db.gban_ids)}</b> banned\n\n" + "\n".join(lines)) if lines else "No global bans."), mk(rows)


async def cb_owner(cb: CallbackQuery, p):
    uid = cb.from_user.id
    if cb.message.chat.type != "private" or not is_sudo(uid):
        return await cb.answer("⛔ Owner / Sudo only (bot DM).", show_alert=True)
    act = p[1]
    if act not in SUDO_OK and not is_owner(uid):
        return await cb.answer("⛔ Owner only.", show_alert=True)
    _owner_wait.pop(uid, None)
    toast = None
    if act == "home":
        text, kb = await owner_home(uid)
    elif act == "cmds":
        text, kb = await cmds_view("g", 0)
    elif act == "look":
        text, kb = await render_look(0, "t")
    elif act == "lines":
        text, kb = await lines_home()
    elif act == "vall":
        cur = bool((await db.get_meta()).get("verifyall"))
        n = await set_verifyall(not cur)
        toast = f"Verify {'ON' if not cur else 'OFF'} in {n} group(s)"
        text, kb = await owner_home(uid)
    elif act == "abw":
        cur = bool((await db.get_meta()).get("admin_bw"))
        await db.set_meta(admin_bw=not cur)
        toast = f"Admin gaali filter {'ON' if not cur else 'OFF'}"
        text, kb = await owner_home(uid)
    elif act == "mnt":
        cur = bool((await db.get_meta()).get("maintenance"))
        await db.set_meta(maintenance=not cur)
        await db.audit(0, uid, 0, "maintenance", "ok", "on" if not cur else "off")
        toast = "Maintenance ON — bot ignores everyone but Owner/Sudo" if not cur else "Maintenance OFF"
        text, kb = await owner_home(uid)
    elif act == "grp":
        text, kb = await groups_view(int(p[2]))
    elif act == "g":
        text, kb = await group_view(int(p[2]), is_owner(uid))
    elif act in ("gv", "ga", "gc", "gk", "gx"):
        cid = int(p[2])
        if act == "gv":
            g = await db.get_group(cid)
            await db.set_group(cid, verify_on=not g["verify_on"], verify_by_all=False)
        elif act == "ga":
            g = await db.get_group(cid)
            await db.set_group(cid, admin_bw={None: True, True: False, False: None}[g["admin_bw"]])
        elif act == "gc":
            text, kb = await cmds_view(str(cid), 0)
            await edit(cb, text, kb)
            return await cb.answer()
        elif act == "gk":
            text, kb = await render_look(cid, "t")
            await edit(cb, text, kb)
            return await cb.answer()
        elif act == "gx":
            try:
                await bot.leave_chat(cid)
                await db.groups.update_one({"_id": cid}, {"$set": {"active": False}})
                await db.audit(cid, uid, 0, "leavechat")
                toast = "👋 Left the group"
            except TelegramAPIError as e:
                return await cb.answer(re.sub("<.*?>", "", tg_err(e))[:180], show_alert=True)
            text, kb = await groups_view(0)
            await edit(cb, text, kb)
            return await cb.answer(toast)
        db._gc.pop(cid, None)
        text, kb = await group_view(cid, True)
    elif act == "bc":
        _owner_wait[uid] = ("bc", p[2])
        text = await panel(0, "BROADCAST" if p[2] == "users" else "GROUP CAST", f"✍️ Send me the message now — text, photo, video, anything.\nIt goes to <b>{'all users' if p[2] == 'users' else 'all groups'}</b> immediately.")
        kb = mk([[("❌ Cancel", "ow:home")]])
    elif act == "gift":
        text, kb = await gift_view()
    elif act == "sudo":
        text, kb = await sudo_view()
    elif act == "sa":
        _owner_wait[uid] = ("sudo",)
        text, kb = await panel(0, "SUDO", "✍️ Send the user's numeric ID (or forward nothing — just the ID)."), mk([[("❌ Cancel", "ow:sudo")]])
    elif act == "sd":
        target = int(p[2])
        if target in db.sudo_ids and target not in cfg.SUDO_IDS and target != cfg.OWNER_ID:
            await db.del_sudo(target)
            await db.audit(0, uid, target, "delsudo")
            toast = "Sudo removed"
        text, kb = await sudo_view()
    elif act == "gb":
        text, kb = await gban_view()
    elif act == "gba":
        _owner_wait[uid] = ("gban",)
        text, kb = await panel(0, "GLOBAL BAN", "✍️ Send: <code>user_id reason</code>"), mk([[("❌ Cancel", "ow:gb")]])
    elif act == "gu":
        target = int(p[2])
        await db.del_gban(target)
        spawn(_bg_gban(target, False, cb.message.chat.id, uid))
        toast = "Unbanned everywhere (running in background)"
        text, kb = await gban_view()
    elif act == "bk":
        await cb.answer("💾 Preparing backup…")
        return await send_backup(cb.message.chat.id)
    elif act == "st":
        text, kb = await stats_view()
    else:
        return await cb.answer()
    await edit(cb, text, kb)
    await cb.answer(toast)


async def take_owner_input(m: Message):
    uid = m.from_user.id
    w = _owner_wait.pop(uid, None)
    if not w:
        return
    back = mk([[("🏠 Panel", "ow:home")]])
    txt = (m.text or "").strip()
    if w[0] == "bc":
        return await start_broadcast(m.chat.id, uid, w[1], {"kind": "copy", "from": m.chat.id, "mid": m.message_id})
    if w[0] == "gift":
        return await start_broadcast(m.chat.id, uid, "groups", await gift_payload(txt))
    if not is_owner(uid):
        return
    if w[0] == "sudo":
        if not txt.lstrip("-").isdigit():
            _owner_wait[uid] = w
            return await say(m, "❌ Send only the numeric user ID.")
        t = int(txt)
        if t != cfg.OWNER_ID:
            await db.add_sudo(t, uid)
            await db.audit(0, uid, t, "addsudo")
        text, kb = await sudo_view()
        return await say(m, "✅ Added.\n\n" + text, kb)
    if w[0] == "gban":
        parts = txt.split(None, 1)
        if not parts or not parts[0].lstrip("-").isdigit():
            _owner_wait[uid] = w
            return await say(m, "❌ Send like: <code>123456789 spam</code>")
        t = int(parts[0])
        if is_sudo(t):
            return await say(m, "🛡 Can't gban Owner/Sudo.", back)
        await db.add_gban(t, uid, parts[1] if len(parts) > 1 else "no reason")
        spawn(_bg_gban(t, True, m.chat.id, uid))
        text, kb = await gban_view()
        await say(m, "🚫 Global ban applied (running in background).\n\n" + text, kb)


# ───────── giveaway panel ─────────
async def gift_view():
    n = await db.users.count_documents({"gift": True})
    body = (kv("👥", "Participants", f"<b>{n}</b>") + "\n\nCast sends the giveaway message with a <b>Participate Now</b> button to every group.\n"
            "Pressing it opens the bot, START = entry.")
    kb = mk([[("📣 Cast default", "gf:cast"), ("✍️ Cast my text", "gf:text")], [("🎟 Entries", "gf:list"), ("🏆 Winner", "gf:w1"), ("🏆 ×3", "gf:w3")],
             [("♻️ Reset entries", "gf:rs")], [("🏠 Panel", "ow:home")]])
    return await panel(0, "GIVEAWAY", body), kb


async def cmd_gift(m: Message, args):
    if not await need_dm_sudo(m):
        return
    if (args or "").strip():
        return await start_broadcast(m.chat.id, m.from_user.id, "groups", await gift_payload(args))
    text, kb = await gift_view()
    await say(m, text, kb)


async def cb_gift(cb: CallbackQuery, p):
    uid = cb.from_user.id
    if cb.message.chat.type != "private" or not is_sudo(uid):
        return await cb.answer("⛔ Owner / Sudo only (bot DM).", show_alert=True)
    act = p[1]
    if act == "cast":
        await cb.answer("📣 Sending…")
        return await start_broadcast(cb.message.chat.id, uid, "groups", await gift_payload())
    if act == "text":
        _owner_wait[uid] = ("gift",)
        await cb.answer()
        return await edit(cb, await panel(0, "GIVEAWAY", "✍️ Send the giveaway text now. It goes to all groups with the button."), mk([[("❌ Cancel", "ow:gift")]]))
    if act == "list":
        rows = [u async for u in db.users.find({"gift": True}).sort("gift_ts", -1).limit(15)]
        lines = [f"🎟 {mention(u['_id'], u.get('first_name'))}" + (f" · @{esc(u['username'])}" if u.get("username") else "") for u in rows]
        await cb.answer()
        return await edit(cb, await panel(0, "ENTRIES", "\n".join(lines) or "No entries yet."), mk([[("⬅️ Back", "ow:gift")]]))
    if act in ("w1", "w3"):
        n = 1 if act == "w1" else 3
        win = [u async for u in db.users.aggregate([{"$match": {"gift": True, "blocked": {"$ne": True}}}, {"$sample": {"size": n}}])]
        lines = [f"🏆 {mention(u['_id'], u.get('first_name'))} · <code>{u['_id']}</code>" + (f" · @{esc(u['username'])}" if u.get("username") else "") for u in win]
        await cb.answer()
        return await edit(cb, await panel(0, "WINNER" + ("S" if n > 1 else ""), "\n".join(lines) or "No participants yet."), mk([[("🎲 Pick again", f"gf:{act}"), ("⬅️ Back", "ow:gift")]]))
    if act == "rs":
        if not is_owner(uid):
            return await cb.answer("⛔ Owner only.", show_alert=True)
        r = await db.users.update_many({"gift": True}, {"$unset": {"gift": "", "gift_ts": ""}})
        await cb.answer(f"♻️ {r.modified_count} entries cleared")
        text, kb = await gift_view()
        return await edit(cb, text, kb)
    await cb.answer()


# ───────── /sudo and /backup ─────────
async def cmd_sudo(m: Message, args):
    if not is_owner(m.from_user.id):
        return
    parts = (args or "").split(None, 1)
    sub = parts[0].lower() if parts else "list"
    rest = parts[1] if len(parts) > 1 else ""
    if sub == "add":
        return await cmd_addsudo(m, rest)
    if sub in ("del", "remove", "rm"):
        return await cmd_delsudo(m, rest)
    text, kb = await sudo_view()
    await say(m, text + "\n\n<code>/sudo add id</code> · <code>/sudo del id</code>", kb)


async def send_backup(chat_id):
    import json
    out = {"bot": "GUARDIX", "version": VERSION, "created": datetime.now(cfg.TZ).isoformat(),
           "meta": {k: v for k, v in (await db.get_meta()).items() if k not in ("digest_last", "daily_last")}}
    for name, col in (("groups", db.groups), ("vips", db.vips), ("filters", db.flt), ("sudos", db.sudos), ("gbans", db.gbans)):
        out[name] = [d async for d in col.find({}).limit(20000)]
    data = json.dumps(out, ensure_ascii=False, indent=1, default=str).encode("utf-8")
    fname = f"guardix-backup-{datetime.now(cfg.TZ).strftime('%Y%m%d-%H%M')}.json"
    await bot.send_document(chat_id, BufferedInputFile(data, filename=fname),
                            caption=f"💾 <b>Backup</b> · {len(out['groups'])} groups · {len(out['vips'])} VIPs · {len(out['filters'])} filters\n<i>Keep this file private.</i>")


async def cmd_backup(m: Message, args):
    if m.chat.type != "private" or not is_owner(m.from_user.id):
        return await say(m, "💾 Backup is Owner-only, in the bot DM.")
    await send_backup(m.chat.id)


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


async def godmode_promote(chat_id, uid, title):
    try:
        me = await bot.get_chat_member(chat_id, bot.id)
        you = await bot.get_chat_member(chat_id, uid)
    except TelegramAPIError as e:
        return f"❌ Godmode failed in {esc(title)}: " + tg_err(e)
    if you.status == "creator":
        return f"👑 You are the group owner of <b>{esc(title)}</b> — you already have every right."
    if me.status != "administrator" or not getattr(me, "can_promote_members", False):
        return f"❌ Godmode in <b>{esc(title)}</b>: I don't have the <b>Add new admins</b> right here."
    rights = {r: bool(getattr(me, r, False)) for r, _, _ in PROMO_RIGHTS}
    try:
        await bot.promote_chat_member(chat_id, uid, **rights)
    except TelegramAPIError as e:
        return f"❌ Godmode in <b>{esc(title)}</b>: " + tg_err(e)
    _admin_cache.pop(chat_id, None)
    await db.audit(chat_id, uid, uid, "godmode", "ok", ",".join(r for r, v in rights.items() if v))
    given = ", ".join(lab for r, lab, _ in PROMO_RIGHTS if rights[r]) or "none"
    return f"⚡ <b>Godmode ON</b> in <b>{esc(title)}</b>\n🔑 Rights: {given}"


async def cmd_godmode(m, args):
    uid = m.from_user.id if m.from_user else 0
    if not is_sudo(uid):
        return
    if m.chat.type in GROUPS:  # silent: command deleted, answer only in DM
        await safe_delete(m.chat.id, m.message_id)
        res = await godmode_promote(m.chat.id, uid, m.chat.title)
        try:
            await bot.send_message(uid, res)
        except TelegramAPIError:
            pass
        return
    await say(m, await panel(0, "GODMODE", "⚡ <b>Owner &amp; Sudo always have admin power</b> in every group where the bot has rights "
                             "(ban, mute, warn, purge … no switch needed).\n\n🤫 In a group send <code>/godmode</code> — the bot deletes the "
                             "command and <b>silently</b> gives you admin with as many rights as the bot itself has.\n\n"
                             "🛡 You are also protected: the bot never acts on Owner/Sudo, and if another bot removes you it brings you back."))


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
async def start_broadcast(chat_id, uid, mode, payload):
    n = (await db.users.count_documents({"started": True, "blocked": {"$ne": True}})) if mode != "groups" else len(await db.active_groups())
    msg = await bot.send_message(chat_id, f"📣 Sending to <b>{n}</b> {'groups' if mode == 'groups' else 'users'}… 0%")
    spawn(run_broadcast({"mode": mode, "payload": payload, "uid": uid}, chat_id, msg.message_id))


async def gift_payload(text=None):
    t = esc(text.strip()[:3500]) if text and text.strip() else GIFT_DEFAULT
    return {"kind": "text", "text": await panel(0, "GIVEAWAY ALERT", t), "gift": True}


async def _start_bc(m: Message, args, mode):
    if not await need_dm_sudo(m):
        return
    r = m.reply_to_message
    if mode == "gift":
        payload, mode = await gift_payload(args), "groups"
    elif r:
        payload = {"kind": "copy", "from": m.chat.id, "mid": r.message_id}
    elif (args or "").strip():
        payload = {"kind": "text", "text": esc(args.strip())}
    else:
        return await say(m, f"Usage: /{'gcast' if mode == 'groups' else 'broadcast'} text — or reply to any message (media works too).\nOr use the buttons in /owner.")
    await start_broadcast(m.chat.id, m.from_user.id, mode, payload)


async def cmd_broadcast(m, args):
    await _start_bc(m, args, "users")


async def cmd_gcast(m, args):
    await _start_bc(m, args, "groups")





async def _send_one(chat_id, payload):
    kb = mk([[("🚀 Open GUARDIX", f"url:https://t.me/{BOT_USER}?start=promo"), ("🆘 Support", "url:" + cfg.SUPPORT_URL)]]) if payload.get("promo") else (mk([[("🎁 Participate Now", f"url:https://t.me/{BOT_USER}?start=gift")]]) if payload.get("gift") else None)
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
        await cb.answer("🗑 Removed" if ok else "No warnings left")
    elif act in ("rs", "ry"):
        n = await db.clear_warns(cid, uid)
        await db.audit(cid, cb.from_user.id, uid, "resetwarns", "ok", n)
        await cb.answer(f"♻️ Cleared {n}")
    else:
        await cb.answer("🔄")
    text, kb = await warns_view(cid, uid)
    await edit(cb, text, kb)


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
                    "bc": cb_bc, "lg": cb_logs, "mod": cb_mod, "w": cb_warn, "k": cb_kickme, "p": cb_profile,
                    "pm": cb_promote, "lk": cb_link, "vr": cb_vr, "dl": cb_daily, "cc": cb_cmds, "ln": cb_lines,
                    "ow": cb_owner, "gf": cb_gift, "ap": cb_appeal, "ac": cb_active}
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
    # members
    "start": cmd_start, "help": cmd_help, "profile": cmd_profile, "me": cmd_profile, "id": cmd_id, "ping": cmd_ping, "info": cmd_info,
    "rules": cmd_rules, "admins": cmd_admins, "report": cmd_report, "kickme": cmd_kickme, "active": cmd_active,
    "groupinfo": cmd_groupinfo, "appeal": cmd_appeal,
    # admins
    "ban": cmd_ban, "tban": cmd_tban, "unban": cmd_unban, "kick": cmd_kick, "mute": cmd_mute, "unmute": cmd_unmute,
    "warn": cmd_warn, "warns": cmd_warns, "del": cmd_del, "purge": cmd_purge, "pin": cmd_pin, "unpin": cmd_unpin,
    "lock": cmd_lock, "unlock": cmd_unlock, "promote": cmd_promote, "demote": cmd_demote, "link": cmd_link,
    "all": cmd_all, "stopall": cmd_stopall, "verify": cmd_verify, "check": cmd_check, "zombies": cmd_zombies, "sync": cmd_sync,
    "addbadword": cmd_addbadword, "delbadword": cmd_delbadword, "allowlink": cmd_allowlink,
    "filter": cmd_filter, "filters": cmd_filters, "stop": cmd_stopfilter,
    "settings": cmd_settings, "setwelcome": cmd_setwelcome, "setrules": cmd_setrules, "addvip": cmd_addvip, "delvip": cmd_delvip, "logs": cmd_logs,
    # owner + sudo
    "broadcast": cmd_broadcast, "gcast": cmd_gcast, "gift": cmd_gift, "owner": cmd_owner, "godmode": cmd_godmode, "banall": cmd_banall,
    # owner only (DM)
    "cmds": cmd_cmds, "lines": cmd_lines, "verifyall": cmd_verifyall, "look": cmd_look, "sudo": cmd_sudo, "gban": cmd_gban,
    "ungban": cmd_ungban, "backup": cmd_backup,
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
            _await_line.pop(uid, None)
            _owner_wait.pop(uid, None)
            _await_appeal.pop(uid, None)
            blk = await rule_blocks(m, cmd[0])
            if blk:
                return await say(m, blk)
            await COMMANDS[cmd[0]](m, cmd[1])
        else:
            if uid in _owner_wait and is_sudo(uid):
                return await take_owner_input(m)
            if text and uid in _await_line and is_owner(uid):
                return await take_line(m)
            if text and uid in _await_appeal:
                return await take_appeal(m)
            if text:
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
        blk = await rule_blocks(m, cmd[0])
        if blk:
            return await temp(m.chat.id, blk, 6)
        return await COMMANDS[cmd[0]](m, cmd[1])
    if text and ADMIN_CALL.search(text) and not await is_trusted(m.chat.id, uid):
        return await cmd_report(m, text)
    if not await is_trusted(m.chat.id, uid):
        await run_protection(m, g)
        await maybe_tag_verify(m, g)
    elif uid in await chat_admins(m.chat.id) and not is_sudo(uid) and not await is_group_creator(m.chat.id, uid):
        await run_protection(m, g, admin=True)
    await run_filters(m)


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
            if new == "administrator" and tl is not None:
                spawn(sync_members(chat.id))
            if old in ("left", "kicked"):
                try:
                    n = await bot.get_chat_member_count(chat.id)
                except TelegramAPIError:
                    n = "?"
                tolog(f"➕ <b>BOT ADDED TO GROUP</b>\n🏘 {esc(chat.title)}\n🆔 <code>{chat.id}</code>\n👥 Members: {n}\n"
                      f"👤 By: {mention(ev.from_user.id, ev.from_user.full_name)} <code>{ev.from_user.id}</code>\n🕒 {tnow()}")
                body = ("🛡️ <b>GUARDIX is here!</b>\n\n<b>Setup checklist</b>\n1️⃣ Make me <b>admin</b>\n2️⃣ Enable: Delete messages, Restrict/Ban users, Pin messages\n"
                        "3️⃣ Protection is <b>always ON</b> — nothing to set up\n4️⃣ /verify on to tag unverified members · /help for commands")
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



async def run_job(job):
    kind, cid, uid = job["kind"], job["chat_id"], job["user_id"]
    if kind == "unban":
        await bot.unban_chat_member(cid, uid, only_if_banned=True)
        await db.audit(cid, 0, uid, "auto_unban")
    elif kind == "unmute":
        await bot.restrict_chat_member(cid, uid, permissions=await default_perms(cid))
        await db.audit(cid, 0, uid, "auto_unmute")
    elif kind in ("remind", "captcha"):
        return  # retired jobs left over from an older version
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
            if n % 6 == 0:
                await maybe_scheduled()
            if n % 60 == 0:
                await db.recover_jobs(300)
        except Exception as e:
            log.error("scheduler error: %s", cfg.redact(e))
        await asyncio.sleep(5)


# ═════════════════════════ STARTUP ═════════════════════════
async def set_commands():
    mem = [("start", "Start & verify"), ("help", "Help menu"), ("profile", "Your profile with DP"), ("id", "Show IDs"), ("ping", "Speed & uptime"),
           ("info", "Anyone's info"), ("rules", "Group rules"), ("admins", "Owner, admins & VIPs"), ("report", "Report a message"),
           ("kickme", "Leave group"), ("active", "Active members"), ("groupinfo", "Group card & stats"), ("appeal", "Ask for an unban")]
    adm = mem + [("ban", "Ban user"), ("tban", "Timed ban"), ("unban", "Unban user"), ("kick", "Kick user"), ("mute", "Mute user"),
                 ("unmute", "Unmute user"), ("warn", "Warn user"), ("warns", "Warning history"), ("del", "Delete message"),
                 ("purge", "Purge messages"), ("pin", "Pin message"), ("unpin", "Unpin (all)"), ("lock", "Lock chat"), ("unlock", "Unlock chat"),
                 ("promote", "Promote admin"), ("demote", "Demote admin"), ("link", "Invite link"), ("all", "Tag everyone"),
                 ("stopall", "Stop tagging"), ("verify", "Verify tags on/off"), ("check", "Bot rights & health"), ("zombies", "Remove deleted accounts"),
                 ("sync", "Fetch all members"), ("addbadword", "Add bad word"), ("delbadword", "Remove bad word"), ("allowlink", "Allowed links"),
                 ("filter", "Add auto reply"), ("filters", "List auto replies"), ("stop", "Stop an auto reply"), ("settings", "Settings panel"),
                 ("setwelcome", "Set welcome"), ("setrules", "Set rules"), ("addvip", "Add VIP"), ("delvip", "Remove VIP"), ("logs", "Audit logs")]
    staff = [("owner", "Control panel"), ("broadcast", "Broadcast now"), ("gcast", "Group cast now"), ("gift", "Giveaway panel"),
             ("godmode", "Silent admin"), ("banall", "Emergency ban all")]
    owner_only = [("cmds", "Command control"), ("lines", "Funny lines"), ("verifyall", "Verify in all groups"), ("look", "Theme & font"),
                  ("sudo", "Manage sudo"), ("gban", "Global ban"), ("ungban", "Global unban"), ("backup", "Settings backup")]

    async def put(cmds, scope):
        try:
            await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in cmds[:100]], scope=scope)
        except TelegramAPIError as e:
            log.warning("set_commands failed: %s", cfg.redact(e))

    pub = mem[:5] + [("appeal", "Ask for an unban")]
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
    await start_telethon()
    _custom_lines.update((await db.get_meta()).get("lines") or {})
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
        if tl is not None:
            try:
                await tl.disconnect()
            except Exception:
                pass
        await bot.session.close()
        db.client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
