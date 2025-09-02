# web.py
import os
from threading import Thread
from flask import Flask, jsonify
from app import main_loop

app = Flask(__name__)

def _run_worker():
    # runs your IMAP → Telegram loop forever
    main_loop()

@app.route("/")
def health():
    return jsonify({"ok": True}), 200

# When gunicorn imports this module, we start the worker thread.
# Gunicorn will serve the Flask app; the thread runs the email poller.
worker_thread = Thread(target=_run_worker, daemon=True)
worker_thread.start()
