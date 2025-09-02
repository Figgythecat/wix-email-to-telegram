import os, time, imaplib, email, re
import requests
from html import escape
from bs4 import BeautifulSoup

# ==== Config (from Environment Variables) ====
IMAP_SERVER   = os.getenv("IMAP_SERVER", "imap.gmail.com")
EMAIL_ACCOUNT = os.getenv("EMAIL_ACCOUNT")          # your Gmail
EMAIL_PASSWORD= os.getenv("EMAIL_PASSWORD")         # 16-char Google App Password
IMAP_FOLDER   = os.getenv("IMAP_FOLDER", "INBOX")
IMAP_SEARCH   = os.getenv("IMAP_SEARCH", '(FROM "@wix.com")')  # appended to UNSEEN
SUBJECT_KEYWORDS = [s.strip().lower() for s in os.getenv(
    "SUBJECT_KEYWORDS", "payment,invoice,order"
).split(",") if s.strip()]

POLL_SECONDS        = int(os.getenv("POLL_SECONDS", "60"))
MAX_EMAILS_PER_RUN  = int(os.getenv("MAX_EMAILS_PER_RUN", "20"))
PROCESSED_LABEL     = os.getenv("PROCESSED_LABEL", "WixProcessed")  # Gmail label to tag processed mail

TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")


# ==== Helpers ====
def log(*a): print(*a, flush=True)

def send_telegram_html(text_html: str) -> bool:
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        log("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })
    if r.status_code != 200:
        log("Telegram error:", r.status_code, r.text[:300])
        return False
    return True

def html_to_text(html: str) -> str:
    try:
        return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    except Exception:
        return html

def extract_plaintext(msg) -> str:
    # Prefer text/plain; fallback to text/html converted to text
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="ignore")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html = part.get_payload(decode=True).decode(errors="ignore")
                return html_to_text(html)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            try:
                return payload.decode(errors="ignore")
            except Exception:
                return str(payload)
    return ""

# Regex variants that match common Wix email phrasings
NAME_RE   = re.compile(r"(?im)^(?:Customer|Customer Name|Buyer|Name)\s*:\s*(.+)$")
EMAIL_RE  = re.compile(r"(?im)^[Ee]mail\s*:\s*([^\s]+@[^\s]+)")
AMOUNT_RE = re.compile(r"(?im)^(?:Amount|Total|Paid|Amount Paid)\s*:\s*([A-Z]{3}|USD)?\s*\$?([0-9,]+(?:\.[0-9]{2})?)")

def parse_fields(text: str):
    name   = (NAME_RE.search(text) or [None, None])[1]
    mail   = (EMAIL_RE.search(text) or [None, None])[1]
    amt    = None
    m = AMOUNT_RE.search(text)
    if m:
        ccy = m.group(1) or "USD"
        val = m.group(2)
        amt = f"{ccy} {val}"
    return name, mail, amt

def subject_matches(subject: str) -> bool:
    s = (subject or "").lower()
    return not SUBJECT_KEYWORDS or any(k in s for k in SUBJECT_KEYWORDS)


# ==== IMAP poller (one pass) ====
def run_once():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    mail.select(IMAP_FOLDER)

    # Only unread messages, plus your custom search
    search_query = f'UNSEEN {IMAP_SEARCH}'.strip()
    typ, data = mail.uid("SEARCH", None, search_query)
    if typ != "OK":
        log("IMAP search failed:", data)
        return

    uids = [u for u in (data[0].split() if data and data[0] else [])]
    if not uids:
        log("No new matching emails.")
        return

    # limit batch
    batch = [int(x) for x in uids][-MAX_EMAILS_PER_RUN:]

    for uid in batch:
        try:
            typ, msg_data = mail.uid("FETCH", str(uid), "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = msg.get("subject", "")
            if not subject_matches(subject):
                # mark non-matching mail as seen so it doesn't loop forever
                mail.uid("STORE", str(uid), "+FLAGS", r"(\Seen)")
                continue

            body = extract_plaintext(msg)
            name, email_addr, amount = parse_fields(body)

            message = (
                "✅ <b>Payment received</b>\n"
                f"🧾 <b>Subject:</b> {escape(subject)}\n"
                f"👤 <b>Customer:</b> {escape(name or 'N/A')}\n"
                f"📧 <b>Email:</b> {escape(email_addr or 'N/A')}\n"
                f"💵 <b>Amount:</b> {escape(amount or 'N/A')}\n"
                "—\n"
                f"{escape(body[:1200])}"
            )

            ok = send_telegram_html(message)

            # Mark processed mail as Seen and tag with a Gmail label so we never reprocess it
            if ok:
                mail.uid("STORE", str(uid), "+FLAGS", r"(\Seen)")
                try:
                    mail.uid("STORE", str(uid), "+X-GM-LABELS", f"({PROCESSED_LABEL})")
                except Exception:
                    # Non-Gmail servers won't support X-GM-LABELS; safe to ignore
                    pass

            log(f"Processed UID {uid}: sent={ok}")

        except Exception as e:
            # Soft-report to Telegram; never crash the loop
            try:
                send_telegram_html(f"⚠️ <b>Worker error:</b> {escape(repr(e))}")
            except Exception:
                pass

    try:
        mail.logout()
    except Exception:
        pass


# ==== Forever loop ====
def main_loop():
    if not all([EMAIL_ACCOUNT, EMAIL_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
        log("❗ Missing required env vars: EMAIL_ACCOUNT, EMAIL_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID")
        raise SystemExit(1)
    log("Worker started. Polling every", POLL_SECONDS, "seconds")
    while True:
        try:
            run_once()
        except Exception as e:
            try:
                send_telegram_html(f"⚠️ <b>Loop error:</b> {escape(repr(e))}")
            except Exception:
                pass
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main_loop()
