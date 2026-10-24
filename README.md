# 🛡️ GUARDIX v3.0 — Telegram Group Protection Bot

Premium group security bot: **always-on protection**, one-tap verification, premium cards with the member's profile photo,
20 fonts and 10 themes (controlled only by the Bot Owner from DM).

## Files (7)
`main.py` · `core.py` · `words.py` (100+ abuse words) · `lines.py` (586 welcome shayari / jokes) · `requirements.txt` · `Dockerfile` · `README.md`

## Railway variables
Required: `BOT_TOKEN`, `OWNER_ID`, `MONGO_DB_URI`.  Optional: `SUDO_IDS`, `LOG_GROUP_ID`, `SUPPORT_URL`, `DATABASE_NAME`, `TIMEZONE`.
New (optional): `API_ID` + `API_HASH` (my.telegram.org → API development tools) → enables `/sync` (full member list over MTProto with the same bot token).
`PURGE_MAX` (default 500) · `DIGEST_HOURS` (default `10,20` = report digest times).

## Protection (permanent — no switch, no /protect)
- Spam, same-message spam (all copies deleted + warn), flood (mute 5 min), mass mentions, raids, abusive words (100+ built-in, leetspeak aware).
- **Links** are deleted → message with **✅ Approve** (posts it back) / **🚫 Reject + Warn** for staff. `/allowlink` for trusted domains.
- **Admins** who spam: message deleted + callout tagging the group owner (no warn).
- `/addbadword` · `/delbadword` (also hides a built-in word for that group) · `/allowlink`.

## Verification (one tap, per group)
- Join → welcome card with the member's **DP + random shayari/joke** + **Verify Now** button. Reachable users also get a Verify button in DM.
- Verify → bot starts in DM, welcome with DP, **✅ Verified**, **Return to Group** button; the group card turns into a ✅ card.
- `/verify on|off` — when ON, an unverified member who writes is tagged to verify (never muted or deleted).
- Join-request groups: the requester gets a DM Verify button (the bot never approves).
- `/start gift` → giveaway entry · 🌅 Daily Shayari opt-in button in the start panel.

## Reports
`/report` or `@admin`: duplicates merged, staff/bots can't be reported, **🚫 Fake report** button (3 strikes = blocked). Twice a day the bot posts a digest of open reports.

## Commands
Public: `/start /help /profile(/me) /id /ping /info /rules /admins /viplist /report /kickme /top /active /groupinfo /stats`
Admin: `/ban /tban /unban /kick /mute /unmute /warn /unwarn /warns /resetwarns /del /purge /pin /unpin /unpinall /lock /unlock /promote /demote /addbadword /delbadword /allowlink /settings /setwelcome /welcome /setrules /announce /addvip /delvip /verify /link /zombies /sync /logs`
Owner+Sudo: `/broadcast /gcast /dmpromo /callpm /giftcast /giftlist /giftwinner /sudolist /uptime /owner /events /addevent /event /delevent /stop /godmode /banall`
Owner: `/look /addsudo /delsudo /gban /ungban /leavechat /clearevents /giftreset`

`/promote` shows permission buttons; you can only give rights you hold (and the bot holds). `/godmode` in a group silently gives Owner/Sudo admin.
Owner/Sudo always have admin power; if another bot/admin removes them the bot unbans them and DMs a rejoin link.

## Limits (Telegram)
Bots cannot add users to a group and cannot DM a user who never started the bot (except for ~5 min after a join request).
`/sync` is best effort: very large groups may return fewer members. Message counts for `/purge` can't be exact (Telegram skips old messages silently).
