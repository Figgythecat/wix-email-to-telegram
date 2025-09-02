from threading import Thread
from flask import Flask, jsonify
from app import main_loop

app = Flask(__name__)

def _run_worker():
    main_loop()  # background IMAP→Telegram loop

# Start the worker thread when the web app starts
worker_thread = Thread(target=_run_worker, daemon=True)
worker_thread.start()

@app.get("/")
def health():
    return jsonify({"ok": True}), 200
