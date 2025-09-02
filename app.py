import os, time, imaplib, email, re, sys, json, pathlib
import requests
from bs4 import BeautifulSoup

# ---------- Config via env ----------
IMAP_SERVER      = os.getenv("IMAP_SERVER", "imap.gmail.com")
EMAIL_ACCOUNT    = os.getenv("EMAIL_ACCOUNT")
EMAIL_PASSWORD   = os.getenv("EMAIL_PASSWORD")               # Google App Password (no spaces)
IMAP_FOLDER      = os.getenv("IMAP_FOLDER", "INBOX")
IMAP_SEARCH      = os.getenv("IMAP_SEARCH", '(FROM "@wix.com")')
SUBJECT_KEYWORDS = os.getenv("SUBJECT_KEYWORDS", "payment,invoice,order").lower().split(",")
POLL_SECONDS     = int(os.getenv("POLL_SECONDS", "60"))
MAX_EMAILS       = int(os.getenv("MAX_EMAILS_PER_RUN", "20"))
DEBUG_PREVIEW    = os.getenv("DEBUG_PREVIEW", "0") == "1"

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATE_PATH       = os.getenv("STATE_PATH", "/tmp/last_uid.json")
ALLOWED_FROM     = [d.strip().lower() for d in os.getenv("ALLOWED_FROM_DOMAINS", "wix.com").split(",") if d.strip()]

# ---------- Utils ----------
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
        for el in soup(["script", "style", "noscript"]):
            el.extract()
        text = soup.get_text("\n", strip=True)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text
    except Exception:
        return html

def extract_plaintext(msg):
    """Prefer text/plain; otherwise clean text/html. Works for single-part and multipart."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            ctype  = part.get_content_type()
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(errors="ignore")
            if ctype == "text/plain":
                return text
            if ctype == "text/html":
                return clean_html_to_text(text)
        return ""
    else:
        ctype = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        text = payload.decode(errors="ignore") if payload else ""
        if ctype == "text/html" or "<html" in text.lower() or "<!doctype" in text.lower():
            return clean_html_to_text(text)
        return text

# ---------- Robust field extraction ----------
NAME_LABEL_RE = re.compile(r"(?im)^(?:Customer(?: Name)?|Buyer|Billing name|Recipient|Name)\s*[:\-]\s*(.+)$")
EMAIL_LABEL_RE = re.compile(r"(?im)^[\w\s]*email[\w\s]*[:\-]\s*([^\s<>\)]+@[^\s<>\)]+)")
AMOUNT_LABEL_RE = re.compile(
    r"(?im)^(?:Amount(?:\s*paid)?|Payment amount|Charged|Total(?:\s*paid)?)\s*[:\-]?\s*(?:USD|US\$|EUR|€|GBP|£|\$)?\s*\$?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)"
)
ANY_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
ANY_CURRENCY_NUMBER_RE = re.compile(r"(?:USD|US\$|EUR|€|GBP|£|\$)\s*\$?\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?)")

def _guess_currency(s: str) -> str:
    s = s.upper()
    if "€" in s or "EUR" in s: return "EUR"
    if "£" in s or "GBP" in s: return "GBP"
    return "USD"

def parse_fields(text: str):
    name = None
    email_ = None
    amount = None

    m = NAME_LABEL_RE.search(text)
    if m: name = m.group(1).strip()

    m = EMAIL_LABEL_RE.search(text)
    if m:
        email_ = m.group(1).strip()
    else:
        m2 = ANY_EMAIL_RE.search(text)
        if m2: email_ = m2.group(0)

    m = AMOUNT_LABEL_RE.search(text)
    if m:
        val = m.group(1)
        ccy = _guess_currency(m.group(0))
        amount = f"{ccy} {val}"
    else:
        candidates = [x.replace(",", "") for x in ANY_CURRENCY_NUMBER_RE.findall(text)]
        if candidates:
            try:
                val = max(float(v) for v in candidates)
                amount = f"USD {val:,.2f}"
            except Exception:
                pass
    return name, email_, amount

# ---------- State ----------
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

def from_allowed(msg) -> bool:
    frm = (msg.get("from") or "").lower()
    return any(dom and dom in frm for dom in ALLOWED_FROM) if ALLOWED_FROM else True

# ---------- Main poller ----------
def run_once():
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
    mail.select(IMAP_FOLDER)

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
        log(f"No new emails since UID {last_seen}")
        return

    new_uids.sort()
    batch = new_uids[-MAX_EMAILS:]

    latest = last_seen
    for uid in batch:
        typ, msg_data = mail.uid("FETCH", str(uid), "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            continue

        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        if not from_allowed(msg):
            continue

        subject = msg.get("subject", "")
        if not subject_matches(subject):
            continue

        body = extract_plaintext(msg)
        if DEBUG_PREVIEW:
            log("Body preview:", body[:400].replace("\n", " ")[:400])

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

    save_last_uid(latest)

def main_loop():
    missing = [k for k, v in {
        "EMAIL_ACCOUNT": EMAIL_ACCOUNT,
        "EMAIL_PASSWORD": EMAIL_PASSWORD,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_TOKEN,
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
    }.items() if not v]
    if missing:
        log("❗ Missing required env vars:", ", ".join(missing))
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
