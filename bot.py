
from flask import Flask, request, jsonify
import os
import hmac
import hashlib
import json
import time
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl

import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
POSTBACK_SECRET = os.environ.get("POSTBACK_SECRET")

FRONTEND_ORIGIN = "https://paisapaisa773-svg.github.io"


def get_db():
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
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

    db.execute("""
        CREATE TABLE IF NOT EXISTS network_events (
            id BIGSERIAL PRIMARY KEY,
            network TEXT NOT NULL,
            ymid TEXT NOT NULL,
            telegram_id BIGINT,
            event_type TEXT NOT NULL,
            reward_event_type TEXT,
            zone_id TEXT,
            sub_zone_id TEXT,
            request_var TEXT,
            estimated_price NUMERIC(14,7) DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(network, ymid, event_type)
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
    except (ValueError, TypeError):
        return None

    if time.time() - auth_date > 86400 or auth_date > time.time() + 60:
        return None

    check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(data.items())
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


def reward_for_ad(ad_number):
    rewards = [
        Decimal("1.00"),
        Decimal("1.20"),
        Decimal("1.50"),
        Decimal("0.90"),
        Decimal("0.80"),
        Decimal("0.70")
    ]

    if ad_number <= len(rewards):
        return rewards[ad_number - 1]

    next_reward = Decimal("0.70") - (
        Decimal("0.10") * (ad_number - 6)
    )

    return max(Decimal("0.10"), next_reward)


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = FRONTEND_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/health")
def health():
    try:
        with get_db() as db:
            create_tables(db)
            db.execute("SELECT 1")

        return jsonify({
            "status": "EARNOVA Backend Running",
            "database": "Connected"
        })

    except Exception:
        app.logger.exception("Health check failed")
        return jsonify({
            "status": "error",
            "database": "Not connected"
        }), 500


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
        app.logger.exception("Registration failed")
        return jsonify({"ok": False, "error": "Server error"}), 500


@app.route("/api/ad/start", methods=["POST", "OPTIONS"])
def start_ad():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(silent=True) or {}
    user = verify_telegram_data(body.get("initData", ""))

    if not user or not user.get("id"):
        return jsonify({
            "ok": False,
            "error": "Invalid Telegram data"
        }), 401

    telegram_id = int(user["id"])
    ymid = uuid.uuid4().hex

    try:
        with get_db() as db:
            create_tables(db)

            db.execute("""
                INSERT INTO users (telegram_id, username)
                VALUES (%s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET username = EXCLUDED.username
            """, (
                telegram_id,
                user.get("username", "")
            ))

            db.execute("""
                INSERT INTO ad_history
                (telegram_id, network, event_id, status)
                VALUES (%s, 'monetag', %s, 'pending')
            """, (telegram_id, ymid))

        return jsonify({
            "ok": True,
            "ymid": ymid
        })

    except Exception:
        app.logger.exception("Ad session creation failed")
        return jsonify({
            "ok": False,
            "error": "Could not start ad"
        }), 500


@app.route("/postback", methods=["GET"])
def monetag_postback():
    supplied_secret = request.args.get("secret", "")

    if (
        not POSTBACK_SECRET
        or not hmac.compare_digest(
            supplied_secret,
            POSTBACK_SECRET
        )
    ):
        return jsonify({"ok": False, "error": "Unauthorized"}), 403

    ymid = request.args.get("ymid", "").strip()
    event_type = request.args.get("event_type", "").strip().lower()
    reward_event_type = request.args.get(
        "reward_event_type", ""
    ).strip().lower()

    if not ymid or not event_type:
        return jsonify({
            "ok": False,
            "error": "Missing event data"
        }), 400

    try:
        estimated_price = Decimal(
            request.args.get("estimated_price", "0") or "0"
        )

        if not estimated_price.is_finite() or estimated_price < 0:
            raise InvalidOperation()

    except (InvalidOperation, ValueError):
        return jsonify({
            "ok": False,
            "error": "Invalid estimated price"
        }), 400

    telegram_id_param = request.args.get("telegram_id", "").strip()

    try:
        reported_telegram_id = (
            int(telegram_id_param) if telegram_id_param else None
        )
    except ValueError:
        return jsonify({
            "ok": False,
            "error": "Invalid Telegram ID"
        }), 400

    try:
        with get_db() as db:
            create_tables(db)

            ad = db.execute("""
                SELECT telegram_id, status
                FROM ad_history
                WHERE network = 'monetag' AND event_id = %s
                FOR UPDATE
            """, (ymid,)).fetchone()

            if not ad:
                return jsonify({
                    "ok": False,
                    "error": "Unknown ymid"
                }), 404

            telegram_id = int(ad["telegram_id"])

            if (
                reported_telegram_id is not None
                and reported_telegram_id != telegram_id
            ):
                return jsonify({
                    "ok": False,
                    "error": "Telegram ID mismatch"
                }), 403

            inserted = db.execute("""
                INSERT INTO network_events (
                    network,
                    ymid,
                    telegram_id,
                    event_type,
                    reward_event_type,
                    zone_id,
                    sub_zone_id,
                    request_var,
                    estimated_price
                )
                VALUES (
                    'monetag', %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (network, ymid, event_type)
                DO NOTHING
                RETURNING id
            """, (
                ymid,
                telegram_id,
                event_type,
                reward_event_type,
                request.args.get("zone_id"),
                request.args.get("sub_zone_id"),
                request.args.get("request_var"),
                estimated_price
            )).fetchone()

            if event_type != "impression" or reward_event_type != "valued":
                return jsonify({
                    "ok": True,
                    "message": "Event recorded; no reward"
                }), 200

            if ad["status"] == "credited":
                return jsonify({
                    "ok": True,
                    "message": "Reward already credited"
                }), 200

            user = db.execute("""
                SELECT balance
                FROM users
                WHERE telegram_id = %s
                FOR UPDATE
            """, (telegram_id,)).fetchone()

            if not user:
                return jsonify({
                    "ok": False,
                    "error": "User not found"
                }), 404

            completed = db.execute("""
                SELECT COUNT(*) AS total
                FROM ad_history
                WHERE telegram_id = %s
                  AND status = 'credited'
            """, (telegram_id,)).fetchone()
            ad_number = int(completed["total"]) + 1
            reward = reward_for_ad(ad_number)

            db.execute("""
                UPDATE users
                SET balance = balance + %s
                WHERE telegram_id = %s
            """, (reward, telegram_id))

            db.execute("""
                UPDATE ad_history
                SET reward = %s, status = 'credited'
                WHERE network = 'monetag' AND event_id = %s
            """, (reward, ymid))

            db.execute("""
                INSERT INTO wallet_transactions (
                    telegram_id,
                    amount,
                    transaction_type,
                    status,
                    idempotency_key
                )
                VALUES (%s, %s, 'ad_reward', 'completed', %s)
                ON CONFLICT (idempotency_key) DO NOTHING
            """, (
                telegram_id,
                reward,
                "monetag:" + ymid
            ))

        return jsonify({
            "ok": True,
            "message": "Reward credited",
            "reward": str(reward)
        }), 200

    except Exception:
        app.logger.exception("Monetag postback processing failed")
        return jsonify({
            "ok": False,
            "error": "Postback processing failed"
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5001))
    )
