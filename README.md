# 🛡 GUARDIX BOT

Premium all-in-one Telegram group management aur security bot.
Stack: Python 3.12, aiogram 3, MongoDB Atlas, Railway (Docker).

## Files
| File | Kaam |
|---|---|
| `bot.py` | Poora bot |
| `requirements.txt` | Libraries |
| `Dockerfile` | Railway build |
| `.gitignore` | Secrets GitHub par jaane se rokta hai |

## Railway Variables
| Variable | Value |
|---|---|
| `BOT_TOKEN` | @BotFather ka token (zaroori) |
| `BOT_USERNAME` | Bot ka username, bina @ ke |
| `OWNER_ID` | Owner ki numeric Telegram ID (zaroori) |
| `SUDO_IDS` | Extra sudo IDs, comma se alag (optional) |
| `LOG_GROUP_ID` | Log channel ID, `-100...` wali |
| `SUPPORT_URL` | Support channel link |
| `MONGO_DB_URI` | MongoDB Atlas connection string (zaroori) |
| `DATABASE_NAME` | `guardix` |
| `TIMEZONE` | `Asia/Kolkata` |

Token, password ya URI ko kabhi GitHub par mat daalo.

## Deploy
1. Charon files private GitHub repo ke root mein upload karo.
2. Railway → New Project → Deploy from GitHub repo.
3. Variables tab mein upar wale variables daalo.
4. Deploy ke baad Logs mein `Starting @...` dikhna chahiye, aur log channel mein `ONLINE` card aana chahiye.

## BotFather settings
- `/mybots` → bot → Bot Settings → **Group Privacy → Turn off**

## Group setup
- Bot ko admin banao: Delete messages, Ban users, Invite users, Pin messages.
- Invite link mein **Request Admin Approval** on rakho, tabhi captcha DM bina `/start` ke jata hai.
- Log channel mein bhi bot ko admin banao (post messages).
- Test: `/protect on`, `/help`, `/settings`.

## Commands
**Basic:** `/start` `/help` `/id` `/rules`
**Moderation:** `/ban` `/unban` `/kick` `/mute` `/unmute` `/tmute` `/tban` `/warn` `/warns` `/resetwarns` `/del` `/purge` `/pin` `/unpin` `/promote` `/demote` `/admins`
**Protection:** `/protect on|off` `/addbadword` `/delbadword`
**Tagall:** `/all` `/tagall` `/tagadmins` `/cancel`
**Setup:** `/setwelcome` `/setrules` `/settings`
**VIP:** `/addvip` `/delvip` `/viplist`
**Owner/Sudo (DM):** `/broadcast` `/logs` `/sudolist` `/addsudo` `/delsudo`

## Auto features
- Gaali filter (Hindi, Hinglish, English): message delete, warning, limit par mute ya ban
- Spam, flood, mass mention, link delete (non-admin)
- Raid protection, anti-bot add, edited message check
- Warnings 7 din baad expire
- Service messages (joined/left) cleanup
- Naya group ya bot removal alert log channel mein

## Captcha
- **Join request wale group:** bot DM mein easy sawal bhejta hai. Sahi jawab par approve, galat jawab par request pending.
- **Seedha join wale group:** captcha nahi, DM mein welcome + group ka naam. User ne bot start nahi kiya ho to group mein "Start Bot" button aata hai.

## Roles
- **Owner:** sab kuch, plus `/addsudo` `/delsudo`.
- **Sudo:** admin powers har group mein, plus `/broadcast` `/logs`.
- **Group admin:** apne group mein moderation aur setup (Telegram admin rights ke hisaab se).
- **VIP:** filter aur warning nahi lagti.
- Owner/Sudo par ban, mute ya warn nahi lag sakta.

## Limits (Telegram ke rules)
- Bot group ki poori member list nahi dekh sakta. Tagall sirf unhi ko tag karta hai jinhe bot ne dekha hai.
- Bot kisi ko DM tabhi bhej sakta hai jab user ne bot start kiya ho ya join request pending ho.
- Koi bhi filter 100% perfect nahi hota.

## Troubleshooting
| Problem | Kya karo |
|---|---|
| `Required variable missing` | Railway Variables check karo |
| `MongoDB connect nahi hua` | Password, URI aur Atlas Network Access (`0.0.0.0/0`) check karo |
| Bot group mein kuch nahi karta | Bot admin hai? Group Privacy off hai? |
| Captcha DM nahi aata | Invite link mein Request Admin Approval on karo, bot ko Invite users right do |
| Log channel mein kuch nahi aata | `LOG_GROUP_ID` sahi hai? Bot channel mein admin hai? |
