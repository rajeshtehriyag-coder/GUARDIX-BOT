# 🛡️ GUARDIX v4.0 — Telegram Group Protection Bot

Premium group security: always-on protection, one-tap verification, premium cards with the member's DP,
20 fonts · 10 themes, and a full button control panel for the Bot Owner in DM (`/owner`).

## Files (7)
`main.py` · `core.py` · `words.py` (226 abuse words + 40 phrases) · `lines.py` (586 welcome shayari/jokes) · `requirements.txt` · `Dockerfile` · `README.md`

## Railway variables
Required: `BOT_TOKEN`, `OWNER_ID`, `MONGO_DB_URI`.  Optional: `SUDO_IDS`, `LOG_GROUP_ID`, `SUPPORT_URL`, `DATABASE_NAME`, `TIMEZONE`,
`API_ID` + `API_HASH` (my.telegram.org → enables `/sync`, auto-sync for `/all` and `/zombies`), `PURGE_MAX` (500), `DIGEST_HOURS` (`10,20`).

## Commands (62)
**Members (13):** `/start /help /profile(/me) /id /ping /info /rules /admins /report /kickme /active /groupinfo /appeal`
**Admins (35):** `/ban /tban /unban /kick /mute /unmute /warn /warns /del /purge /pin /unpin /lock /unlock /promote /demote /link /all /stopall /verify /check /zombies /sync /addbadword /delbadword /allowlink /filter /filters /stop /settings /setwelcome /setrules /addvip /delvip /logs`
**Owner + Sudo (6):** `/owner /broadcast /gcast /gift /godmode /banall yes`
**Owner only, DM (8):** `/cmds /lines /verifyall /look /sudo /gban /ungban /backup`
No confirmation panels: ban, mute, kick, `/kickme`, `/zombies`, broadcasts and warn-reset run immediately. `/banall` needs the word `yes`.

## Protection (always ON, no switch)
Flood, same-message spam, mass mentions, raids, abusive words (also in **edited** messages), links (delete + ✅ Approve / 🚫 Reject).
Admins: spam/flood → delete + callout tagging the group owner; their abusive words only if the Owner switches **Admin gaali** ON in `/owner` (global or per group).

## Verification
Join → DP card + shayari + **Verify Now** → bot DM opens, START → ✅ Verified + Return-to-Group button. `/verify on` tags unverified writers. `/verifyall on|off` (Owner DM) = all groups; `off` only reverts groups it switched on.

## Filters
`/filter hello` (reply to any message) or `/filter hello text` → anyone writing the word gets that reply. `/filters` lists names. `/stop hello` stops it. Max 50 per group.

## Appeals
A banned user (who started the bot) gets a DM with `/appeal`; the admins see ✅ Accept / ❌ Reject buttons in the group.

## Owner panel (`/owner`)
🎛 Commands (ON/OFF + who may use) · 🎨 Look (10 themes, 20 fonts) · ✍️ Funny lines · 🔐 Verify all · 🛡 Admin gaali · 🛠 Maintenance ·
🏘 Groups (per-group verify / admin gaali / commands / look / leave) · 📣 Broadcast / Group cast · 🎁 Giveaway · 👑 Sudo · 🚫 Global bans · 💾 Backup · 📊 Stats.

## Limits (Telegram)
Bots cannot add users to a group and cannot DM a user who never started the bot (except ~5 min after a join request).
`/sync` may return fewer members in very large groups. `/purge` counts are "sent for deletion", Telegram skips old messages silently.
