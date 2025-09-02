import os, time, imaplib, email, re, sys, json, pathlib
import requests
from bs4 import BeautifulSoup

IMAP_SERVER      = os.getenv("IMAP_SERVER", "imap.gmail.com")
EMAIL_ACCOUNT    = os.getenv("EMAIL_ACCOUNT")                # e.g. you@gmail.com
EMAIL_PASSWORD   = os.getenv("EMAIL_PASSWORD")               # 16-char Google App Password
IMAP_FOLDER      = os.getenv("IMAP_FOLDER", "INBOX")
# Default: any Wix mail that mentions payment/order/invoice in subject
IMAP_SEARCH      = os.getenv("IMAP_SEARCH", '(FROM "@wix.com")')
SUBJECT_KEYWORDS = os.getenv("SUBJECT_KEYWORDS", "payment,invoice,order").lower().split(",")
POLL_SECONDS     = int(os.getenv("POLL_SECONDS", "60"))
MAX_EMAILS       = int(os.getenv("MAX_EMAILS_PER_RUN", "20"))

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")            # @channel or -100123...

STATE_PATH       = os.getenv("STATE_PATH", "/tmp/last_uid.json")

def log(*args):
    print(*args, flush=True)

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True})
    if resp.status_code != 200:
        log("Telegram error:", resp.status_code, resp.text[:300])
        return False
    return True

def clean_html_to_text(html):
    try:
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text("\n", strip=True)
    except Exception:
        return html

def extract_plaintext(msg):
    # Prefer text/plain, fallback to text/html
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                return part.get_payload(decode=True).decode(errors="ignore")
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/html":
                html = part.get_payload(decode=True).decode(errors="ignore")
                return clean_html_to_text(html)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            try:
                return payload.decode(errors="ignore")
            except Exception:
                return str(payload)
    return ""

# Simple regex helpers – tweak to match your Wix email template if needed
NAME_RE   = re.compile(r"(?im)^(?:Customer|Customer Name|Name)\s*:\s*(.+)$")
EMAIL_RE  = re.compile(r"(?im)^[Ee]mail\s*:\s*([^\s]+@[^\s]+)")
AMOUNT_RE = re.compile(r"(?im)^(?:Amount|Total)\s*:\s*([A-Z]{3}|USD)?\s*\$?([0-9,]+(?:\.[0-9]{2})?)")

def parse_fields(text):
    name   = (NAME_RE.search(text) or (None,))[0] if NAME_RE.search(text) else None
    email_ = (EMAIL_RE.search(text) or (None,))[0] if EMAIL_RE.search(text) else None
    amt_m  = AMOUNT_RE.search(text)
    amount = None
    if amt_m:
        ccy = amt_m.group(1) or "USD"
        val = amt_m.group(2)
        amount = f"{ccy} {val}".strip()
    return name, email_, amount

def load_last_uid():
    p = pathlib.Path(STATE_PATH)
    if p.exists():
        try:
            return json.loads(p.read_text()).get("last_uid", 0)
        except Exception:
            return 0
    return 0

def save_last_uid(uid):
    p = pathlib.Path(STATE_PATH)
    p.write_text(json.dumps({"last_uid": uid}))

def subject_matches(subject: str) -> bool:
    s = (subject or "").lower()
    return any(k.strip() and k.strip() in s for k in SUBJECT_KEYWORDS)

def run_once():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    mail.select(IMAP_FOLDER)
    # Use UID so we can keep state safely
    typ, data = mail.uid("SEARCH", None, IMAP_SEARCH)
    if typ != "OK":
        log("IMAP search failed:", data)
        return

    uids = [int(x) for x in data[0].split()] if data and data[0] else []
    if not uids:
        log("No matching emails.")
        return

    last_seen = load_last_uid()
    new_uids = [u for u in uids if u > last_seen]
    if not new_uids:
        log("No new emails since UID", last_seen)
        return

    # only process a limited batch per run
    new_uids.sort()
    batch = new_uids[-MAX_EMAILS:]

    latest = last_seen
    for uid in batch:
        typ, msg_data = mail.uid("FETCH", str(uid), "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        subject = msg.get("subject", "")
        if not subject_matches(subject):
            # skip non-payment subjects even if from wix
            continue

        body = extract_plaintext(msg)
        name, email_addr, amount = parse_fields(body)

        pretty = (
            "✅ Payment received\n"
            f"🧾 Subject: {subject}\n"
            f"👤 Customer: {name or 'N/A'}\n"
            f"📧 Email: {email_addr or 'N/A'}\n"
            f"💵 Amount: {amount or 'N/A'}\n"
            "—\n"
            f"{body[:1200]}"
        )
        ok = send_telegram(pretty)
        log(f"Sent UID {uid}: {ok}")
        latest = max(latest, uid)

    # persist last processed UID
    save_last_uid(latest)

def main_loop():
    if not all([EMAIL_ACCOUNT, EMAIL_PASSWORD, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID]):
        log("❗ Missing one or more required env vars: EMAIL_ACCOUNT, EMAIL_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID")
        sys.exit(1)

    log("Worker started. Polling every", POLL_SECONDS, "seconds")
    while True:
        try:
            run_once()
        except Exception as e:
            log("Error:", repr(e))
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main_loop()
