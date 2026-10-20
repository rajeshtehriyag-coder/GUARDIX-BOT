# 🛡️ GUARDIX — Telegram Group Protection Bot

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
