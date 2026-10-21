# 🛡️ GUARDIX v2.0 — Telegram Group Protection Bot

Files: `main.py`, `core.py`, `requirements.txt`, `Dockerfile`, `README.md` (5 files).

## Railway Variables (Railway → your service → Variables)
| Name | Value |
|---|---|
| BOT_TOKEN | token from @BotFather |
| OWNER_ID | your numeric Telegram ID (get it from @userinfobot) |
| SUDO_IDS | optional, comma separated: 111,222 |
| MONGO_DB_URI | MongoDB Atlas connection string (mongodb+srv://...) |
| DATABASE_NAME | guardix |
| LOG_GROUP_ID | log group ID, like -100123456789 (add bot there as admin) |
| SUPPORT_URL | https://t.me/yourgroup |
| TIMEZONE | Asia/Kolkata |
| BOT_USERNAME | optional (bot reads it automatically) |

## Deploy steps
1. @BotFather → /newbot → copy token. Also /setprivacy → **Disable** (bot must read group messages).
2. MongoDB Atlas → free cluster → Database Access: create user + password → Network Access: allow 0.0.0.0/0 (Railway has no fixed IP) → Connect → copy URI, put your password in it.
3. GitHub → New **private** repo → upload the 5 files (Add file → Upload files).
4. Railway → New Project → Deploy from GitHub repo → add the Variables above → Deploy. Logs should show `Starting @yourbot`.
5. Create a log group, add the bot as admin, send /id there to get LOG_GROUP_ID, set it in Railway.
6. Add bot to your group → make it admin (Delete messages, Restrict/Ban, Pin) → /settings.

## Notes
- Run only ONE instance (polling). Pending temp mutes/bans/CAPTCHA expiries are stored in MongoDB and resume after restarts.
- /zombies and /banall only see members the bot has already seen (Telegram bots cannot list all members).
- Telegram buttons can't have custom colours; themes/fonts style the panel text, emojis and borders.
- Maintenance: `/stop` pauses the bot for everyone except Owner/Sudo, `/stop off` resumes.

## What's new in v2.0
- **One-tap verification**: group welcome (with member's profile photo) has a **Verify Now** button → opens DM → tap START → instantly *Verified* + bot started + start panel (with DP). Math CAPTCHA still available: /settings → CAPTCHA → *Verify mode* = `math`.
- Verify/welcome message stays in the group until the user verifies (then it turns into a ✅ verified card). Leaving the group removes it.
- **5 reminders** (gap and count editable in /settings → CAPTCHA) for new joiners who haven't verified. Nobody is auto-kicked (expiry action = none by default).
- `/verifyall` — notifies unverified / random known members with a Verify button, **no mute**.
- Premium cards (theme + font + lines) for ban, mute, unban, unmute, kick, warn, VIP, promote, demote etc.
- `/profile` (alias `/me`) with profile photo; DM `/start` shows your DP.
- `/zombies` now works for group admins (Restrict rights), scans up to 5000 known members; every join/leave is tracked through chat_member updates (**bot must be admin**).
- New commands: `/profile /unwarn /unpinall /link /active /groupinfo /welcome /captcha /verify /unverified /verifyall`. Full command menus are registered for members, admins, sudo and owner.

## Zombies note
Telegram bots cannot list all members of a group. /zombies finds deleted accounts among members the bot has *seen* (messages, joins, chat_member updates). Old silent members that the bot never saw cannot be detected without a userbot.
