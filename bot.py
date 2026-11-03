from flask import Flask, request, jsonify
import sqlite3
import os
import hmac
import hashlib
import json
import time
from urllib.parse import parse_qsl

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DB_NAME = "earnova.db"

def get_db():
    db = sqlite3.connect(DB_NAME)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT UNIQUE,
            username TEXT,
            balance REAL DEFAULT 0
        )
    """)
    db.commit()
    return db

def verify_telegram_data(init_data):
    if not BOT_TOKEN or not init_data:
        return None

    data = dict(parse_qsl(init_data))
    received_hash = data.pop("hash", None)

    if not received_hash:
        return None

    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError:
        return None

    if time.time() - auth_date > 86400 or auth_date > time.time() + 60:
        return None

    check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(data.items())
    )

    secret_key = hmac.new(
        b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
    ).digest()

    calculated_hash = hmac.new(
        secret_key, check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    try:
        return json.loads(data.get("user", "{}"))
    except json.JSONDecodeError:
        return None

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = (
        "https://paisapaisa773-svg.github.io"
    )
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response

@app.route("/api/register", methods=["POST", "OPTIONS"])
def register():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    user = verify_telegram_data(body.get("initData", ""))

    if not user or not user.get("id"):
        return jsonify({"ok": False, "error": "Invalid Telegram data"}), 401

    db = get_db()
    db.execute(
        """INSERT INTO users (telegram_id, username)
        VALUES (?, ?)
        ON CONFLICT(telegram_id)
        DO UPDATE SET username=excluded.username""",
        (str(user["id"]), user.get("username", ""))
    )
    db.commit()
    db.close()

    return jsonify({"ok": True, "message": "User registered"})

@app.route("/health")
def health():
    return jsonify({"status": "EARNOVA Backend Running"})

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5001))
    )
