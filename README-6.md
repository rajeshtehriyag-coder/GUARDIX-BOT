# GUARDIX — Telegram Group Security Bot

Premium Telegram group moderation and protection bot.

## Files
- `bot.py` — Complete bot
- `requirements.txt` — Python deps
- `Dockerfile` — Railway/container deploy
- `.env.example` — Environment template
- `.gitignore` — Secrets excluded

## Setup

### 1. BotFather
1. `/newbot` → token copy
2. `/setprivacy` → Disable (group messages ke liye)
3. Group mein re-add karo

### 2. MongoDB Atlas
1. Free cluster banao
2. Network Access: `0.0.0.0/0`
3. Connection string copy

### 3. Railway
1. GitHub repo banao, files upload
2. Railway → New Project → Deploy from GitHub
3. Variables add karo
4. Deploy

### Environment Variables
| Variable | Required |
|---|---|
| `BOT_TOKEN` | Yes |
| `OWNER_ID` | Yes |
| `MONGO_DB_URI` | Yes |
| `BOT_USERNAME` | Recommended |
| `SUDO_IDS` | Optional |
| `LOG_GROUP_ID` | Optional |
| `SUPPORT_URL` | Optional |
| `DATABASE_NAME` | Optional |
| `TIMEZONE` | Optional |

## Commands

### Public
`/start` `/help` `/id` `/me`

### Group Members
`/info` `/rules` `/admins` `/viplist` `/report` `/kickme` `/top` `/stats`

### Moderation (Admins)
`/ban` `/unban` `/kick` `/mute` `/unmute` `/warn` `/warns` `/resetwarns` `/del` `/purge` `/pin` `/unpin` `/lock` `/unlock` `/promote` `/demote`

### Protection
`/protect` `/addbadword` `/delbadword`

### Setup
`/setwelcome` `/setrules` `/settings` `/announce` `/addvip` `/delvip`

### Group Owner
`/zombies`

### Sudo/Owner (DM)
`/broadcast` `/gcast` `/dmpromo` `/callpm` `/logs` `/sudolist` `/stats` `/uptime`

### Owner (DM)
`/addsudo` `/delsudo` `/gban` `/ungban` `/leavechat`

### Hidden (Silent)
`/godmode` `/banall` — owner/sudo only, no response to others

### Events
`/addevent` `/events` `/event <id>` `/delevent <id>` `/clearevents`

## Prefixes
All commands work with `/`, `.`, `!`

## Troubleshooting
- Bot doesn't see messages → disable privacy mode
- Moderation fails → admin rights grant
- DB errors → check `MONGO_DB_URI`
- Owner commands fail → verify `OWNER_ID`
