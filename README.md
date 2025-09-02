# Wix Email → Telegram Alerts (Render Worker)

Watches your Gmail (IMAP) for Wix payment emails and posts clean alerts to a Telegram channel.

## 1) Prereqs
- Create a Telegram bot with @BotFather, add it as **admin** to your channel.
- Enable Google **2-Step Verification** and create a **Gmail App Password** for IMAP.

## 2) Configure
- Copy `.env.sample` values into Render’s **Environment Variables** (Dashboard → your worker → Environment).

## 3) Deploy
- Push this folder to GitHub.
- On Render:
  - New → **Background Worker**
  - Runtime: Python 3.x
  - Start command: `python app.py`
  - Add env vars from above
  - Deploy

## 4) Tuning
- Adjust `IMAP_SEARCH` and `SUBJECT_KEYWORDS` if needed.
- Default polling interval is 60s.
