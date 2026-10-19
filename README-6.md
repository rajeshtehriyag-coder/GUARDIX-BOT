# GUARDIX — Telegram Group Security Bot

GUARDIX is a Telegram group moderation and protection bot built with Python 3.12, aiogram 3, and MongoDB. It includes moderation commands, group settings, theme/font controls, and automatic protection features defined in `bot.py`.

## Files

- `bot.py` — bot application
- `requirements.txt` — Python dependencies
- `Dockerfile` — Railway/container deployment
- `.env.example` — environment-variable template (do not put real secrets in GitHub)
- `.gitignore` — excludes local secrets and Python cache

## 1. Create the bot

1. Open [@BotFather](https://t.me/BotFather), run `/newbot`, and copy the token.
2. Run `/setprivacy` and disable privacy if the bot must inspect ordinary group messages for moderation. Re-add the bot to the group after changing privacy.
3. Keep your bot token private.

## 2. Configure MongoDB Atlas

1. Create a free MongoDB Atlas cluster and database user.
2. Configure Network Access for your deployment environment.
3. Copy the connection string and replace the placeholders. URL-encode special characters in the database password.

## 3. Environment variables

Add these in Railway → your service → Variables. Do not commit a real `.env` file.

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | Yes | Token from BotFather |
| `BOT_USERNAME` | Recommended | Username without `@` |
| `OWNER_ID` | Yes | Owner's numeric Telegram user ID |
| `SUDO_IDS` | Optional | Comma-separated sudo user IDs |
| `LOG_GROUP_ID` | Optional | Numeric ID of log group/channel |
| `SUPPORT_URL` | Optional | Support group/channel URL |
| `MONGO_DB_URI` | Yes | MongoDB Atlas URI |
| `DATABASE_NAME` | Optional | Defaults to `guardix` |
| `TIMEZONE` | Optional | Defaults to `Asia/Kolkata` |

## 4. Deploy on Railway using GitHub

1. Upload these files to a GitHub repository.
2. In Railway, create a project from that repository.
3. Add the environment variables above.
4. Railway will build using `Dockerfile`; check the deployment logs.
5. Add GUARDIX to your group and promote it to admin with only the rights needed for its enabled features (typically delete messages, restrict members, ban members, pin messages, and invite/manage members when relevant).

This uses long polling, so a public port/domain is not required.

## 5. Theme and font controls

In a group, an administrator can open `/settings`. Use **Theme** to choose Gold Luxury, Neon Cyber, or Dark Stealth. Use **Font** to choose Sans, Serif, Mono, Script, Small Caps, Fullwidth, Plain, or Auto. Selections are saved with the group's settings. Unicode font variants may render differently across Telegram clients; mentions, IDs, and links should remain readable.

## 6. Important Telegram limitations

- A bot generally cannot initiate a private conversation with a user who has never started it; DM-dependent features need a group fallback button.
- The bot must have the relevant Telegram admin rights for moderation actions.
- Telegram bots cannot enumerate every group member on demand; member-based statistics only cover users the bot has seen or stored.
- Some Telegram actions are limited by API permissions, rate limits, and the target chat's configuration.
- If MongoDB is unavailable, features that need persistence may be degraded. Check logs and restore the database connection.

## 7. Local run

Python 3.12 is recommended.

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
# Set environment variables, then run:
python bot.py
```

## Troubleshooting

- **Bot does not see messages:** check BotFather privacy mode and re-add the bot.
- **Delete/ban/mute fails:** grant the bot the necessary admin rights.
- **Owner commands do not work:** confirm `OWNER_ID` is your numeric Telegram ID.
- **Database errors:** verify `MONGO_DB_URI`, database credentials, and Atlas Network Access.
