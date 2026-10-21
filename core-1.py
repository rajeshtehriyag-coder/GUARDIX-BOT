"""GUARDIX core: config, 10 fonts, 5 themes, MongoDB layer, helpers."""
import os
import re
import html
import time
import logging
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING, ReturnDocument, UpdateOne

log = logging.getLogger("guardix")


# ───────────────────────── CONFIG ─────────────────────────
def to_int(v, default=0):
    try:
        return int(str(v).strip())
    except Exception:
        return default


class Config:
    def __init__(self):
        g = os.getenv
        self.BOT_TOKEN = g("BOT_TOKEN", "").strip()
        self.BOT_USERNAME = g("BOT_USERNAME", "").strip().lstrip("@")
        self.OWNER_ID = to_int(g("OWNER_ID"))
        self.SUDO_IDS = {
            int(x) for x in re.split(r"[,\s]+", g("SUDO_IDS", "")) if x.strip().lstrip("-").isdigit()
        }
        self.MONGO_URI = g("MONGO_DB_URI", "").strip()
        self.DB_NAME = g("DATABASE_NAME", "guardix").strip() or "guardix"
        self.LOG_GROUP_ID = to_int(g("LOG_GROUP_ID"))
        self.SUPPORT_URL = g("SUPPORT_URL", "https://t.me/telegram").strip()
        tz = g("TIMEZONE", "Asia/Kolkata").strip()
        try:
            self.TZ = ZoneInfo(tz)
        except Exception:
            self.TZ = ZoneInfo("UTC")

    def problems(self):
        p = []
        if not self.BOT_TOKEN:
            p.append("BOT_TOKEN missing")
        if not self.OWNER_ID:
            p.append("OWNER_ID missing/invalid (must be a number)")
        if not self.MONGO_URI:
            p.append("MONGO_DB_URI missing")
        return p

    def redact(self, text):
        text = str(text)
        for secret in (self.BOT_TOKEN, self.MONGO_URI):
            if secret:
                text = text.replace(secret, "***")
        return re.sub(r"mongodb(\+srv)?://[^\s]+", "mongodb://***", text)


# ───────────────────────── FONTS (10) ─────────────────────────
def _rng(up, low, dig=None):
    m = {}
    for i in range(26):
        m[chr(65 + i)] = chr(up + i)
        m[chr(97 + i)] = chr(low + i)
    if dig:
        for i in range(10):
            m[str(i)] = chr(dig + i)
    return m


_SC = "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ"
_SMALLCAPS = {}
for _i in range(26):
    _SMALLCAPS[chr(97 + _i)] = _SC[_i]
    _SMALLCAPS[chr(65 + _i)] = _SC[_i]

FONTS = [
    ("Sans Bold", _rng(0x1D5D4, 0x1D5EE, 0x1D7EC)),
    ("Serif Bold", _rng(0x1D400, 0x1D41A, 0x1D7CE)),
    ("Bold Italic", _rng(0x1D468, 0x1D482)),
    ("Sans Bold Italic", _rng(0x1D63C, 0x1D656)),
    ("Script Bold", _rng(0x1D4D0, 0x1D4EA)),
    ("Fraktur Bold", _rng(0x1D56C, 0x1D586)),
    ("Monospace", _rng(0x1D670, 0x1D68A, 0x1D7F6)),
    ("Fullwidth", _rng(0xFF21, 0xFF41, 0xFF10)),
    ("Small Caps", _SMALLCAPS),
    ("Circled", _rng(0x24B6, 0x24D0)),
]


def fx(text, idx=0):
    m = FONTS[idx % len(FONTS)][1]
    return "".join(m.get(c, c) for c in text)


# ───────────────────────── THEMES (5) ─────────────────────────
THEMES = [
    {"name": "Royal Gold", "icon": "👑", "line": "━━━━━━━━━━━━━━━━━━", "b": "✦", "tag": "🥇 GUARDIX • Royal Gold"},
    {"name": "Ocean Blue", "icon": "💎", "line": "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬", "b": "🔹", "tag": "🔷 GUARDIX • Ocean Blue"},
    {"name": "Neon Night", "icon": "⚡", "line": "▰▱▰▱▰▱▰▱▰▱▰▱▰▱▰▱▰▱", "b": "◈", "tag": "🌌 GUARDIX • Neon Night"},
    {"name": "Emerald", "icon": "🌿", "line": "══════════════════", "b": "❖", "tag": "🟢 GUARDIX • Emerald"},
    {"name": "Crimson", "icon": "🔥", "line": "◆━━━━━━━━━━━━━━━━◆", "b": "➤", "tag": "🔴 GUARDIX • Crimson"},
]


def make_panel(title, body, t=0, f=0):
    th = THEMES[t % len(THEMES)]
    return (
        f"{th['line']}\n{th['icon']} <b>{fx(title.upper(), f)}</b> {th['icon']}\n{th['line']}\n\n"
        f"{body}\n\n{th['line']}\n<i>{th['tag']}</i>"
    )


# ───────────────────────── HELPERS ─────────────────────────
def esc(s):
    return html.escape(str(s if s is not None else ""), quote=False)


def mention(uid, name):
    return f'<a href="tg://user?id={int(uid)}">{esc(name or uid)}</a>'


_DUR = re.compile(r"^(\d+)\s*([smhdw])$", re.I)
_MULT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(s):
    m = _DUR.match((s or "").strip())
    if not m:
        return None
    v = int(m.group(1)) * _MULT[m.group(2).lower()]
    return v if 1 <= v <= 366 * 86400 else None


def fmt_secs(s):
    s = int(s)
    out = []
    for n, u in ((86400, "d"), (3600, "h"), (60, "m"), (1, "s")):
        if s >= n:
            out.append(f"{s // n}{u}")
            s %= n
    return " ".join(out[:3]) or "0s"


_ZW = re.compile("[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")


def normalize(text):
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = _ZW.sub("", t).lower()
    return re.sub(r"\s+", " ", t).strip()


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc)


# ───────────────────────── DATABASE ─────────────────────────
DEFAULTS = {
    "welcome": True, "welcome_text": None, "rules": None, "del_service": True,
    "captcha": True, "captcha_timeout": 86400, "captcha_attempts": 3, "captcha_expire_action": "none",
    "verify_mode": "tap", "remind_every": 180, "remind_max": 5,
    "antispam": True, "antiflood": True, "flood_limit": 6, "flood_window": 10,
    "antiraid": True, "raid_limit": 8, "raid_window": 30, "raid_until": 0,
    "links": False, "allowed_domains": [], "badwords_on": True, "badwords": [],
    "safe_mode": False, "locked": False, "lock_vip": True, "lock_admins": False,
    "warn_limit": 3, "warn_action": "mute", "warn_mute_secs": 3600,
    "theme": 0, "font": 0,
}


class DB:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = None
        self._gc = {}
        self._meta = None
        self.sudo_ids = set()
        self.gban_ids = set()

    async def init(self):
        self.client = AsyncIOMotorClient(self.cfg.MONGO_URI, serverSelectionTimeoutMS=8000)
        d = self.client[self.cfg.DB_NAME]
        self.d = d
        self.groups, self.users, self.vips = d.groups, d.users, d.vips
        self.warns, self.audit_c, self.jobs = d.warns, d.audit_logs, d.scheduled_actions
        self.sessions, self.sudos, self.gbans = d.verification_sessions, d.sudos, d.gbans
        self.events, self.reports, self.stats = d.events, d.reports, d.stats
        self.meta, self.counters = d.meta, d.counters
        self.daily = d.daily_stats
        await self.client.admin.command("ping")
        await self.users.create_index("username_l")
        await self.users.create_index("groups")
        await self.groups.create_index("active")
        await self.vips.create_index([("chat_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
        await self.warns.create_index([("chat_id", ASCENDING), ("user_id", ASCENDING)])
        await self.audit_c.create_index([("ts", DESCENDING)])
        await self.audit_c.create_index([("chat_id", ASCENDING), ("ts", DESCENDING)])
        await self.jobs.create_index([("status", ASCENDING), ("due", ASCENDING)])
        await self.jobs.create_index("purge_at", expireAfterSeconds=0)
        await self.sessions.create_index([("chat_id", ASCENDING), ("user_id", ASCENDING)])
        await self.sessions.create_index("user_id")
        await self.sessions.create_index("purge_at", expireAfterSeconds=0)
        await self.reports.create_index([("chat_id", ASCENDING), ("status", ASCENDING)])
        await self.stats.create_index([("chat_id", ASCENDING), ("count", DESCENDING)])
        await self.daily.create_index([("chat_id", ASCENDING), ("day", ASCENDING), ("count", DESCENDING)])
        await self.daily.create_index("purge_at", expireAfterSeconds=0)
        self.sudo_ids = {x["_id"] async for x in self.sudos.find()}
        self.gban_ids = {x["_id"] async for x in self.gbans.find()}
        await self.recover_jobs()

    async def ping(self):
        t = time.perf_counter()
        await self.client.admin.command("ping")
        return (time.perf_counter() - t) * 1000

    # ---- meta (global) ----
    async def get_meta(self):
        if self._meta is None:
            self._meta = await self.meta.find_one({"_id": "global"}) or {}
        return self._meta

    async def set_meta(self, **kv):
        await self.meta.update_one({"_id": "global"}, {"$set": kv}, upsert=True)
        m = await self.get_meta()
        m.update(kv)

    # ---- groups ----
    async def get_group(self, chat_id):
        c = self._gc.get(chat_id)
        if c and time.time() - c[0] < 20:
            return c[1]
        doc = await self.groups.find_one({"_id": chat_id}) or {}
        s = {**DEFAULTS, **doc.get("s", {})}
        for k in ("allowed_domains", "badwords"):
            s[k] = list(s.get(k) or [])
        s["_title"] = doc.get("title") or str(chat_id)
        self._gc[chat_id] = (time.time(), s)
        return s

    async def set_group(self, chat_id, **kv):
        await self.groups.update_one(
            {"_id": chat_id}, {"$set": {f"s.{k}": v for k, v in kv.items()}}, upsert=True
        )
        self._gc.pop(chat_id, None)

    async def add_to_list(self, chat_id, key, vals):
        await self.groups.update_one({"_id": chat_id}, {"$addToSet": {f"s.{key}": {"$each": vals}}}, upsert=True)
        self._gc.pop(chat_id, None)

    async def del_from_list(self, chat_id, key, vals):
        await self.groups.update_one({"_id": chat_id}, {"$pull": {f"s.{key}": {"$in": vals}}})
        self._gc.pop(chat_id, None)

    async def reset_group(self, chat_id):
        await self.groups.update_one({"_id": chat_id}, {"$unset": {"s": ""}})
        self._gc.pop(chat_id, None)

    async def upsert_group(self, chat, active=True):
        await self.groups.update_one(
            {"_id": chat.id},
            {"$set": {"title": chat.title or str(chat.id), "type": chat.type, "username": chat.username,
                      "active": active, "updated": time.time()},
             "$setOnInsert": {"added": time.time()}},
            upsert=True,
        )
        self._gc.pop(chat.id, None)

    async def active_groups(self):
        return [g["_id"] async for g in self.groups.find({"active": True}, {"_id": 1})]

    # ---- users ----
    async def upsert_user(self, u, chat_id=None, started=False, source=None):
        now = time.time()
        upd = {"$set": {"first_name": u.first_name or "", "last_name": u.last_name or "",
                        "username": u.username, "username_l": (u.username or "").lower() or None,
                        "lang": u.language_code, "premium": bool(getattr(u, "is_premium", False)),
                        "last_seen": now},
               "$setOnInsert": {"first_seen": now}}
        if started:
            upd["$set"]["started"] = True
            upd["$set"]["blocked"] = False
        if source:
            upd["$setOnInsert"]["source"] = source
        if chat_id:
            upd["$addToSet"] = {"groups": chat_id}
        r = await self.users.update_one({"_id": u.id}, upd, upsert=True)
        return r.upserted_id is not None

    async def get_user(self, uid):
        return await self.users.find_one({"_id": uid})

    async def find_by_username(self, uname):
        return await self.users.find_one({"username_l": uname.lower().lstrip("@")})

    async def set_blocked(self, uid, v):
        await self.users.update_one({"_id": uid}, {"$set": {"blocked": v}})

    async def tracked_ids(self, chat_id, limit=2000):
        return [u["_id"] async for u in self.users.find({"groups": chat_id}, {"_id": 1}).limit(limit)]

    # ---- roles ----
    async def add_sudo(self, uid, by):
        await self.sudos.update_one({"_id": uid}, {"$set": {"by": by, "ts": time.time()}}, upsert=True)
        self.sudo_ids.add(uid)

    async def del_sudo(self, uid):
        await self.sudos.delete_one({"_id": uid})
        self.sudo_ids.discard(uid)

    async def is_vip(self, chat_id, uid):
        d = await self.vips.find_one({"chat_id": chat_id, "user_id": uid})
        return bool(d and (not d.get("until") or d["until"] > time.time()))

    async def add_vip(self, chat_id, uid, by, until=None):
        await self.vips.update_one(
            {"chat_id": chat_id, "user_id": uid},
            {"$set": {"by": by, "until": until, "ts": time.time()}}, upsert=True)

    async def del_vip(self, chat_id, uid):
        r = await self.vips.delete_one({"chat_id": chat_id, "user_id": uid})
        return r.deleted_count > 0

    async def list_vips(self, chat_id):
        now = time.time()
        return [v async for v in self.vips.find(
            {"chat_id": chat_id, "$or": [{"until": None}, {"until": {"$gt": now}}]}).limit(100)]

    # ---- gban ----
    async def add_gban(self, uid, by, reason):
        await self.gbans.update_one({"_id": uid}, {"$set": {"by": by, "reason": reason, "ts": time.time()}}, upsert=True)
        self.gban_ids.add(uid)

    async def del_gban(self, uid):
        r = await self.gbans.delete_one({"_id": uid})
        self.gban_ids.discard(uid)
        return r.deleted_count > 0

    # ---- warns ----
    async def add_warn(self, chat_id, uid, by, reason):
        await self.warns.insert_one({"chat_id": chat_id, "user_id": uid, "by": by, "reason": reason[:200], "ts": time.time()})
        return await self.warns.count_documents({"chat_id": chat_id, "user_id": uid})

    async def count_warns(self, chat_id, uid):
        return await self.warns.count_documents({"chat_id": chat_id, "user_id": uid})

    async def list_warns(self, chat_id, uid):
        return [w async for w in self.warns.find({"chat_id": chat_id, "user_id": uid}).sort("ts", -1).limit(10)]

    async def clear_warns(self, chat_id, uid):
        r = await self.warns.delete_many({"chat_id": chat_id, "user_id": uid})
        return r.deleted_count

    async def remove_last_warn(self, chat_id, uid):
        d = await self.warns.find_one_and_delete({"chat_id": chat_id, "user_id": uid}, sort=[("ts", -1)])
        return d is not None

    # ---- audit ----
    async def audit(self, chat_id, actor, target, action, result="ok", detail=""):
        try:
            await self.audit_c.insert_one({"ts": time.time(), "chat_id": chat_id, "actor": actor,
                                           "target": target, "action": action, "result": result,
                                           "detail": str(detail)[:200]})
        except Exception as e:  # audit must never break moderation
            log.warning("audit failed: %s", self.cfg.redact(e))

    async def recent_audit(self, chat_id, skip=0, limit=8):
        q = {"chat_id": chat_id} if chat_id else {}
        total = await self.audit_c.count_documents(q)
        rows = [r async for r in self.audit_c.find(q).sort("ts", -1).skip(skip).limit(limit)]
        return rows, total

    # ---- scheduled jobs ----
    async def schedule(self, kind, chat_id, user_id, due, extra=None, key=None):
        jid = key or f"{kind}:{chat_id}:{user_id}"
        await self.jobs.replace_one(
            {"_id": jid},
            {"_id": jid, "kind": kind, "chat_id": chat_id, "user_id": user_id, "due": due,
             "status": "pending", "tries": 0, "extra": extra or {}, "created": time.time()},
            upsert=True)

    async def cancel_job(self, kind, chat_id, user_id):
        await self.jobs.delete_one({"_id": f"{kind}:{chat_id}:{user_id}", "status": "pending"})

    async def claim_due(self):
        return await self.jobs.find_one_and_update(
            {"status": "pending", "due": {"$lte": time.time()}},
            {"$set": {"status": "running", "started": time.time()}},
            sort=[("due", ASCENDING)], return_document=ReturnDocument.AFTER)

    async def finish_job(self, jid, status="done", err=""):
        await self.jobs.update_one({"_id": jid}, {"$set": {"status": status, "error": str(err)[:200],
                                                           "purge_at": utc(time.time() + 7 * 86400)}})

    async def retry_job(self, jid, delay):
        await self.jobs.update_one({"_id": jid}, {"$set": {"status": "pending", "due": time.time() + delay},
                                                  "$inc": {"tries": 1}})

    async def recover_jobs(self, older=120):
        r = await self.jobs.update_many({"status": "running", "started": {"$lt": time.time() - older}},
                                        {"$set": {"status": "pending"}})
        return r.modified_count

    # ---- events / counters ----
    async def next_id(self, name):
        d = await self.counters.find_one_and_update({"_id": name}, {"$inc": {"n": 1}}, upsert=True,
                                                    return_document=ReturnDocument.AFTER)
        return d["n"]

    # ---- stats ----
    def today(self):
        return datetime.now(self.cfg.TZ).strftime("%Y-%m-%d")

    async def flush_counts(self, buf):
        if not buf:
            return
        day = self.today()
        purge = utc(time.time() + 3 * 86400)
        ops = [UpdateOne({"_id": f"{c}:{u}"}, {"$inc": {"count": n}, "$set": {"chat_id": c, "user_id": u, "name": nm}},
                         upsert=True) for (c, u), (nm, n) in buf.items()]
        dops = [UpdateOne({"_id": f"{c}:{u}:{day}"},
                          {"$inc": {"count": n},
                           "$set": {"chat_id": c, "user_id": u, "name": nm, "day": day, "purge_at": purge}},
                          upsert=True) for (c, u), (nm, n) in buf.items()]
        await self.stats.bulk_write(ops, ordered=False)
        await self.daily.bulk_write(dops, ordered=False)

    async def top(self, chat_id, n=10):
        return [x async for x in self.stats.find({"chat_id": chat_id}).sort("count", -1).limit(n)]

    async def top_today(self, chat_id, n=10):
        return [x async for x in self.daily.find({"chat_id": chat_id, "day": self.today()}).sort("count", -1).limit(n)]

    async def forget_member(self, chat_id, uid):
        await self.users.update_one({"_id": uid}, {"$pull": {"groups": chat_id}})
