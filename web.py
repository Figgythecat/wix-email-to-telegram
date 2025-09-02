# web.py
import os
from threading import Thread, Event
from flask import Flask, jsonify
from app import main_loop, send_telegram  # uses your app.py functions

app = Flask(__name__)

_started = Event()
_worker_thread = None

def _ensure_worker_started():
    """Start the background IMAP→Telegram loop exactly once per process."""
    global _worker_thread
    if not _started.is_set():
        _worker_thread = Thread(target=main_loop, name="imap-worker", daemon=True)
        _worker_thread.start()
        _started.set()

# Autostart the worker unless explicitly disabled
if os.getenv("WORKER_AUTOSTART", "1") == "1":
    _ensure_worker_started()

@app.get("/")
def health():
    """Health check for Render/UptimeRobot."""
    return jsonify({"ok": True, "worker_running": _started.is_set()}), 200

@app.get("/kick")
def kick():
    """Manually start the worker if autostart was disabled."""
    _ensure_worker_started()
    return jsonify({"started": _started.is_set()}), 200

@app.get("/tg-ping")
def tg_ping():
    """Server-side Telegram test to verify BOT_TOKEN + CHAT_ID on Render."""
    ok = send_telegram("🔔 tg-ping from Render (server-side)")
    return jsonify({"sent": ok}), (200 if ok else 500)
