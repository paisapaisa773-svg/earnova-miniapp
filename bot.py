
from flask import Flask, request, jsonify
import os
import hmac
import hashlib
import json
import time
import psycopg
from psycopg.rows import dict_row
from urllib.parse import parse_qsl

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")


def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10
    )


def create_tables(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            username TEXT,
            balance NUMERIC(12,2) NOT NULL DEFAULT 0.00,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS wallet_transactions (
            id BIGSERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
            amount NUMERIC(12,2) NOT NULL,
            transaction_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            idempotency_key TEXT UNIQUE NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS ad_history (
            id BIGSERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
            network TEXT NOT NULL,
            event_id TEXT,
            reward NUMERIC(12,2) NOT NULL DEFAULT 0.00,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(network, event_id)
        )
    """)


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

    if time.time() - auth_date > 86400:
        return None

    if auth_date > time.time() + 60:
        return None

    check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(data.items())
    )

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    try:
        return json.loads(data.get("user", "{}"))
    except (json.JSONDecodeError, TypeError):
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
        return jsonify({
            "ok": False,
            "error": "Invalid Telegram data"
        }), 401

    try:
        with get_db() as db:
            create_tables(db)

            db.execute("""
                INSERT INTO users (telegram_id, username)
                VALUES (%s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET username = EXCLUDED.username
            """, (
                int(user["id"]),
                user.get("username", "")
            ))

        return jsonify({
            "ok": True,
            "message": "User registered"
        })

    except Exception:
        app.logger.exception("User registration failed")
        return jsonify({
            "ok": False,
            "error": "Database operation failed"
        }), 503


@app.route("/health")
def health():
    try:
        with get_db() as db:
            db.execute("SELECT 1")
            create_tables(db)

        return jsonify({
            "status": "EARNOVA Backend Running",
            "database": "Connected"
        })

    except Exception:
        app.logger.exception("Database health check failed")
        return jsonify({
            "status": "Backend Running",
            "database": "Connection Failed"
        }), 503


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5001))
    )
