# GUARDIX - Telegram Group Security Bot

Single-file bot (`bot.py`) built on aiogram 3.13.1 + MongoDB (motor). 100% free, no coins, no games.

## 1. BotFather setup
1. `/newbot` -> name + username -> copy the **token**.
2. `/setprivacy` -> **Disable** (bot must see all group messages for filters & stats).
3. `/setjoingroups` -> Enable. `/setinline` is not needed.
4. Menu commands are registered automatically at startup.

## 2. MongoDB
Create a free cluster on MongoDB Atlas, add a DB user, allow `0.0.0.0/0` in Network Access
(Railway IPs change) and copy the connection string into `MONGO_DB_URI`.

## 3. Railway deploy (Docker)
1. Push these files to a GitHub repo (`.env` is git-ignored).
2. Railway -> New Project -> Deploy from GitHub repo (it detects the Dockerfile).
3. Add variables (Settings -> Variables):

| Variable | Example / meaning |
|---|---|
| BOT_TOKEN | token from BotFather |
| BOT_USERNAME | bot username without @ |
| OWNER_ID | your numeric Telegram ID (use /id) |
| SUDO_IDS | comma separated IDs (optional) |
| LOG_GROUP_ID | log group/channel ID, bot must be a member (optional) |
| SUPPORT_URL | https://t.me/yourgroup |
| MONGO_DB_URI | Atlas connection string |
| DATABASE_NAME | guardix |
| TIMEZONE | Asia/Kolkata |

This is a worker (long polling) - no port or domain needed.

## 4. Group setup
1. Add the bot to your group and make it **admin** with: Delete messages, Ban users, Pin messages, Invite users.
2. Run `/settings` to pick theme, font, warn limit and toggles.
3. For the join-request captcha: Group settings -> enable "Approve new members" (join requests).
4. `/setrules`, `/setwelcome Welcome {name} to {group}!`

## 5. Notes & limits (Telegram rules)
- Prefixes `/ . !` all work.
- Telegram bots **cannot DM users who never started the bot**. Silent first-message DMs, welcome DMs and
  sticker captchas fall back to a group "Start Bot / Verify Karo" button when the DM fails.
  Join-request captchas work without a prior start (Telegram allows a DM for 5 minutes after the request).
- Bots cannot list group members: `/zombies` and message stats only cover members the bot has **seen**.
- Gaali filter uses leet/compact matching; short words match whole words only (avoids false positives).
- Scheduled message deletions (welcome 1 day, cards 10 min, service 1 hour) are stored in MongoDB and survive restarts.
- If MongoDB is down the bot keeps running with cached/default settings and logs warnings.

## 6. Troubleshooting
| Problem | Fix |
|---|---|
| Bot silent in group | Disable privacy mode in BotFather, then re-add the bot |
| "Not enough rights" | Give admin rights (Delete, Ban, Pin) |
| Messages not deleted | Bot needs Delete messages right |
| No DM welcome | User never started the bot - expected; group Start Bot button appears |
| DB errors in logs | Check `MONGO_DB_URI` and Atlas Network Access |
| Owner commands ignored | `OWNER_ID` wrong; they only work in the bot's DM |
| Stats empty | Privacy mode on, or no messages since bot joined |
