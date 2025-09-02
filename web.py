from threading import Thread
from flask import Flask, jsonify
from app import main_loop

app = Flask(__name__)
_worker = None

def _run_worker():
    main_loop()

@app.get("/")
def health():
    global _worker
    if _worker is None:
        _worker = Thread(target=_run_worker, daemon=True)
        _worker.start()
    return jsonify({"ok": True}), 200
