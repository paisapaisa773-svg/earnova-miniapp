from flask import Flask, request, jsonify
import os
import hmac
import hashlib
import json
import time
import uuid
import re
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl
from datetime import datetime, timezone, date
import urllib.request
import urllib.parse

import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# ==================== CONFIG ====================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
POSTBACK_SECRET = os.environ.get("POSTBACK_SECRET", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

ADMIN_ID = int(os.environ.get("ADMIN_ID", "7691713278"))
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Earn0vaRewardBot")

CHANNEL_ID = os.environ.get("CHANNEL_ID", "@Earnova0fficial")
GROUP_ID = os.environ.get("GROUP_ID", "")

CHANNEL_URL = "https://t.me/Earnova0fficial"
GROUP_URL = os.environ.get(
    "GROUP_URL",
    "https://t.me/+ncm5joi6ZzMxY2M1"
)

FRONTEND_URL = "https://paisapaisa773-svg.github.io/earnova-miniapp/"
FRONTEND_ORIGIN = "https://paisapaisa773-svg.github.io"

MIN_WITHDRAWAL_FIRST = Decimal("20.00")
MIN_WITHDRAWAL_AFTER_PAID = Decimal("50.00")

CHECKIN_REWARDS = [
    Decimal("0.50"),
    Decimal("0.75"),
    Decimal("1.00"),
    Decimal("1.50"),
    Decimal("2.00"),
    Decimal("3.00"),
    Decimal("5.00")
]

BONUS_REWARD_DEFAULT = Decimal("0.50")
REFERRAL_REWARD = Decimal("5.00")
REFERRAL_COMMISSION_RATE = Decimal("0.10")


# ==================== DATABASE ====================

def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
    )


def create_tables(db):
    """
    Existing data is preserved.
    No table is dropped and no balance is reset.
    """

    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            username TEXT,
            balance NUMERIC(12,2) NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    db.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS first_name TEXT
    """)

    db.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS last_name TEXT
    """)

    db.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS referred_by BIGINT
    """)

    db.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS upi_id TEXT
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
            reward NUMERIC(12,2) NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(network,event_id)
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
            UNIQUE(network,ymid,event_type)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id BIGSERIAL PRIMARY KEY,
            referrer_id BIGINT NOT NULL REFERENCES users(telegram_id),
            referred_id BIGINT UNIQUE NOT NULL REFERENCES users(telegram_id),
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            approved_at TIMESTAMPTZ
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS user_checks (
            telegram_id BIGINT PRIMARY KEY REFERENCES users(telegram_id),
            channel_joined BOOLEAN NOT NULL DEFAULT FALSE,
            group_joined BOOLEAN NOT NULL DEFAULT FALSE,
            bonus_code_entered BOOLEAN NOT NULL DEFAULT FALSE,
            bonus_claimed BOOLEAN NOT NULL DEFAULT FALSE,
            bonus_code TEXT,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS daily_checkins (
            id BIGSERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
            checkin_date DATE NOT NULL,
            day_number INTEGER NOT NULL,
            reward NUMERIC(12,2) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(telegram_id,checkin_date)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id BIGSERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            task_type TEXT NOT NULL DEFAULT 'custom',
            reward NUMERIC(12,2) NOT NULL DEFAULT 0,
            target_url TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            verification TEXT NOT NULL DEFAULT 'manual',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS task_completions (
            id BIGSERIAL PRIMARY KEY,
            task_id BIGINT NOT NULL REFERENCES tasks(id),
            telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
            status TEXT NOT NULL DEFAULT 'pending',
            reward NUMERIC(12,2) NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            reviewed_at TIMESTAMPTZ,
            UNIQUE(task_id,telegram_id)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS bonus_codes (
            id BIGSERIAL PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            reward NUMERIC(12,2) NOT NULL DEFAULT 0.50,
            max_claims INTEGER NOT NULL DEFAULT 1000,
            claims INTEGER NOT NULL DEFAULT 0,
            expires_at TIMESTAMPTZ,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            release_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id BIGSERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
            amount NUMERIC(12,2) NOT NULL,
            upi_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            rejection_reason TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            reviewed_at TIMESTAMPTZ
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            telegram_id BIGINT PRIMARY KEY REFERENCES users(telegram_id),
            upi_id TEXT,
            first_paid_withdrawal BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS referral_commissions (
            id BIGSERIAL PRIMARY KEY,
            referrer_id BIGINT NOT NULL REFERENCES users(telegram_id),
            referred_id BIGINT NOT NULL REFERENCES users(telegram_id),
            source_transaction_id BIGINT UNIQUE,
            amount NUMERIC(12,2) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS monthly_leaderboard_awards (
            id BIGSERIAL PRIMARY KEY,
            month_key TEXT NOT NULL,
            telegram_id BIGINT NOT NULL,
            rank INTEGER NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(month_key,telegram_id),
            UNIQUE(month_key,rank)
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_wallet_user
        ON wallet_transactions(telegram_id,created_at DESC)
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_ad_user
        ON ad_history(telegram_id,status)
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_referrer
        ON referrals(referrer_id,status)
    """)


# ==================== TELEGRAM AUTH ====================

def verify_telegram_data(init_data):
    if not BOT_TOKEN or not init_data:
        return None

    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = data.pop("hash", None)

        if not received_hash:
            return None

        auth_date = int(data.get("auth_date", "0"))
        now = int(time.time())

        if auth_date <= 0:
            return None

        if now - auth_date > 86400:
            return None

        if auth_date > now + 60:
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

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        user = json.loads(data.get("user", "{}"))

        if not isinstance(user, dict) or not user.get("id"):
            return None

        return user

    except Exception:
        return None


def body_user():
    body = request.get_json(silent=True) or {}

    user = verify_telegram_data(
        body.get("initData", "")
    )

    return user, body


def admin_user(user):
    return bool(user and int(user.get("id", 0)) == ADMIN_ID)


# ==================== USER MANAGEMENT ====================

def ensure_user(db, user, referrer_id=None):
    tid = int(user["id"])

    if referrer_id:
        try:
            referrer_id = int(referrer_id)
        except (ValueError, TypeError):
            referrer_id = None

    if referrer_id == tid:
        referrer_id = None

    db.execute("""
        INSERT INTO users(
            telegram_id,username,first_name,last_name,referred_by
        )
        VALUES(%s,%s,%s,%s,%s)
        ON CONFLICT(telegram_id)
        DO UPDATE SET
            username=EXCLUDED.username,
            first_name=EXCLUDED.first_name,
            last_name=EXCLUDED.last_name
    """, (
        tid,
        user.get("username"),
        user.get("first_name"),
        user.get("last_name"),
        referrer_id
    ))

    db.execute("""
        INSERT INTO user_checks(telegram_id)
        VALUES(%s)
        ON CONFLICT DO NOTHING
    """, (tid,))

    db.execute("""
        INSERT INTO user_settings(telegram_id)
        VALUES(%s)
        ON CONFLICT DO NOTHING
    """, (tid,))

    if referrer_id:
        referrer = db.execute("""
            SELECT telegram_id
            FROM users
            WHERE telegram_id=%s
        """, (referrer_id,)).fetchone()

        existing = db.execute("""
            SELECT id FROM referrals
            WHERE referred_id=%s
        """, (tid,)).fetchone()

        if referrer and not existing:
            db.execute("""
                INSERT INTO referrals(referrer_id,referred_id)
                VALUES(%s,%s)
                ON CONFLICT DO NOTHING
            """, (referrer_id, tid))


# ==================== WALLET ====================

def credit_wallet(db, telegram_id, amount, tx_type, key):
    amount = Decimal(str(amount)).quantize(
        Decimal("0.01")
    )

    if amount <= 0:
        return False

    inserted = db.execute("""
        INSERT INTO wallet_transactions(
            telegram_id,amount,transaction_type,
            status,idempotency_key
        )
        VALUES(%s,%s,%s,'completed',%s)
        ON CONFLICT(idempotency_key) DO NOTHING
        RETURNING id
    """, (
        telegram_id,
        amount,
        tx_type,
        key
    )).fetchone()

    if not inserted:
        return False

    db.execute("""
        UPDATE users
        SET balance=balance+%s
        WHERE telegram_id=%s
    """, (amount, telegram_id))

    return True


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

    return max(
        Decimal("0.10"),
        Decimal("0.70") -
        Decimal("0.10") * (ad_number - 6)
    )


# ==================== MEMBERSHIP ====================

def membership_status(telegram_id, chat_id):
    if not BOT_TOKEN or not chat_id:
        return None

    url = (
        f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember"
    )

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "user_id": telegram_id
    }).encode()

    try:
        req = urllib.request.Request(url, data=payload)

        with urllib.request.urlopen(req, timeout=12) as response:
            result = json.loads(response.read().decode())

        if not result.get("ok"):
            return None

        member = result.get("result", {})
        status = member.get("status")

        return (
            status in ("creator", "administrator", "member")
            or (
                status == "restricted"
                and member.get("is_member", False)
            )
        )

    except Exception:
        app.logger.exception("Membership verification failed")
        return None


def verify_required_memberships(telegram_id):
    channel = membership_status(
        telegram_id,
        CHANNEL_ID
    )

    group = membership_status(
        telegram_id,
        GROUP_ID
    )

    return channel, group


def refresh_membership(db, telegram_id):
    channel, group = verify_required_memberships(
        telegram_id
    )

    db.execute("""
        UPDATE user_checks
        SET channel_joined=%s,
            group_joined=%s,
            updated_at=NOW()
        WHERE telegram_id=%s
    """, (
        channel is True,
        group is True,
        telegram_id
    ))

    return channel, group


def membership_required(db, telegram_id):
    channel, group = refresh_membership(
        db,
        telegram_id
    )

    return channel is True and group is True, channel, group


# ==================== REFERRAL RELEASE ====================

def approve_referral_if_eligible(db, referred_id):
    checks = db.execute("""
        SELECT channel_joined,group_joined,bonus_code_entered
        FROM user_checks
        WHERE telegram_id=%s
        FOR UPDATE
    """, (referred_id,)).fetchone()

    if not checks:
        return False

    if not (
        checks["channel_joined"]
        and checks["group_joined"]
        and checks["bonus_code_entered"]
    ):
        return False

    referral = db.execute("""
        SELECT referrer_id,status
        FROM referrals
        WHERE referred_id=%s
        FOR UPDATE
    """, (referred_id,)).fetchone()

    if not referral or referral["status"] != "pending":
        return False

    if int(referral["referrer_id"]) == int(referred_id):
        db.execute("""
            UPDATE referrals
            SET status='rejected'
            WHERE referred_id=%s
        """, (referred_id,))
        return False

    db.execute("""
        UPDATE referrals
        SET status='approved',approved_at=NOW()
        WHERE referred_id=%s AND status='pending'
    """, (referred_id,))

    credit_wallet(
        db,
        referred_id,
        REFERRAL_REWARD,
        "referral_bonus",
        f"referred-bonus:{referred_id}"
    )

    credit_wallet(
        db,
        int(referral["referrer_id"]),
        REFERRAL_REWARD,
        "referral_bonus",
        f"referrer-bonus:{referred_id}"
    )

    return True


# ==================== REFERRAL COMMISSION ====================

def credit_referral_commission(db, transaction_id):
    tx = db.execute("""
        SELECT id,telegram_id,amount,transaction_type,status
        FROM wallet_transactions
        WHERE id=%s
    """, (transaction_id,)).fetchone()

    if not tx:
        return

    if tx["status"] != "completed":
        return

    if tx["transaction_type"] not in (
        "task_reward",
        "bonus_code"
    ):
        return

    referral = db.execute("""
        SELECT referrer_id,status
        FROM referrals
        WHERE referred_id=%s
    """, (tx["telegram_id"],)).fetchone()

    if not referral or referral["status"] != "approved":
        return

    commission = (
        Decimal(tx["amount"]) *
        REFERRAL_COMMISSION_RATE
    ).quantize(Decimal("0.01"))

    if commission <= 0:
        return

    inserted = db.execute("""
        INSERT INTO referral_commissions(
            referrer_id,referred_id,
            source_transaction_id,amount
        )
        VALUES(%s,%s,%s,%s)
        ON CONFLICT(source_transaction_id) DO NOTHING
        RETURNING id
    """, (
        referral["referrer_id"],
        tx["telegram_id"],
        transaction_id,
        commission
    )).fetchone()

    if inserted:
        credit_wallet(
            db,
            int(referral["referrer_id"]),
            commission,
            "referral_commission",
            f"referral-commission:{transaction_id}"
        )


# ==================== CORS ====================

@app.after_request
def add_cors(response):
    origin = request.headers.get("Origin", "")

    if origin == FRONTEND_ORIGIN:
        response.headers["Access-Control-Allow-Origin"] = origin

    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type"
    )

    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, OPTIONS"
    )

    response.headers["Vary"] = "Origin"

    return response


# ==================== HEALTH ====================

@app.route("/health", methods=["GET"])
def health():
    try:
        with get_db() as db:
            create_tables(db)
            db.execute("SELECT 1")

        return jsonify(
            status="EARNOVA Backend Running",
            database="Connected"
        ), 200

    except Exception:
        app.logger.exception("Health check failed")

        return jsonify(
            status="error",
            database="Not connected"
        ), 500


# ==================== REGISTER ====================

@app.route("/api/register", methods=["POST", "OPTIONS"])
def register():
    if request.method == "OPTIONS":
        return "", 204

    user, body = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    try:
        referrer_id = body.get("referrer_id")

        with get_db() as db:
            create_tables(db)

            ensure_user(
                db,
                user,
                referrer_id
            )

            row = db.execute("""
                SELECT balance,created_at
                FROM users
                WHERE telegram_id=%s
            """, (int(user["id"]),)).fetchone()

        return jsonify(
            ok=True,
            balance=str(row["balance"]),
            created_at=row["created_at"].isoformat()
        ), 200

    except Exception:
        app.logger.exception("Registration failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== BALANCE ====================

@app.route("/api/balance", methods=["POST", "OPTIONS"])
def get_balance():
    if request.method == "OPTIONS":
        return "", 204

    user, _ = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    try:
        with get_db() as db:
            create_tables(db)

            row = db.execute("""
                SELECT balance
                FROM users
                WHERE telegram_id=%s
            """, (int(user["id"]),)).fetchone()

        if not row:
            return jsonify(ok=False, error="User not registered"), 404

        return jsonify(
            ok=True,
            balance=str(row["balance"])
        ), 200

    except Exception:
        app.logger.exception("Balance lookup failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== MEMBERSHIP API ====================

@app.route("/api/membership", methods=["POST", "OPTIONS"])
def check_membership():
    if request.method == "OPTIONS":
        return "", 204

    user, _ = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    try:
        tid = int(user["id"])

        with get_db() as db:
            create_tables(db)
            ensure_user(db, user)

            channel, group = refresh_membership(
                db,
                tid
            )

        return jsonify(
            ok=True,
            channel_joined=channel,
            group_joined=group,
            channel_url=CHANNEL_URL,
            group_url=GROUP_URL,
            configured=bool(GROUP_ID)
        ), 200

    except Exception:
        app.logger.exception("Membership API failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== AD START ====================

@app.route("/api/ad/start", methods=["POST", "OPTIONS"])
def start_ad():
    if request.method == "OPTIONS":
        return "", 204

    user, _ = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    try:
        tid = int(user["id"])
        ymid = uuid.uuid4().hex

        with get_db() as db:
            create_tables(db)
            ensure_user(db, user)

            allowed, channel, group = membership_required(
                db,
                tid
            )

            if not allowed:
                return jsonify(
                    ok=False,
                    error="Join official channel and group first",
                    channel_joined=channel,
                    group_joined=group
                ), 403

            db.execute("""
                INSERT INTO ad_history(
                    telegram_id,network,event_id,status
                )
                VALUES(%s,'monetag',%s,'pending')
            """, (tid, ymid))

        return jsonify(
            ok=True,
            ymid=ymid,
            network="monetag"
        ), 200

    except Exception:
        app.logger.exception("Ad start failed")
        return jsonify(ok=False, error="Could not start ad"), 500


# ==================== AD STATUS ====================

@app.route("/api/ad/status", methods=["POST", "OPTIONS"])
def ad_status():
    if request.method == "OPTIONS":
        return "", 204

    user, body = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    ymid = str(body.get("ymid", "")).strip()

    if not ymid or len(ymid) > 100:
        return jsonify(ok=False, error="Invalid ad session"), 400

    try:
        with get_db() as db:
            create_tables(db)

            row = db.execute("""
                SELECT status,reward
                FROM ad_history
                WHERE network='monetag'
                AND event_id=%s
                AND telegram_id=%s
            """, (
                ymid,
                int(user["id"])
            )).fetchone()

        if not row:
            return jsonify(ok=False, error="Ad session not found"), 404

        return jsonify(
            ok=True,
            status=row["status"],
            reward=str(row["reward"]),
            credited=row["status"] == "credited"
        ), 200

    except Exception:
        app.logger.exception("Ad status failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== DAILY CHECK-IN ====================

@app.route("/api/checkin", methods=["POST", "OPTIONS"])
def daily_checkin():
    if request.method == "OPTIONS":
        return "", 204

    user, _ = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    tid = int(user["id"])
    today = datetime.now(timezone.utc).date()

    try:
        with get_db() as db:
            create_tables(db)
            ensure_user(db, user)

            allowed, channel, group = membership_required(
                db,
                tid
            )

            if not allowed:
                return jsonify(
                    ok=False,
                    error="Join official channel and group first",
                    channel_joined=channel,
                    group_joined=group
                ), 403

            last = db.execute("""
                SELECT checkin_date,day_number
                FROM daily_checkins
                WHERE telegram_id=%s
                ORDER BY checkin_date DESC
                LIMIT 1
            """, (tid,)).fetchone()

            if last and last["checkin_date"] == today:
                return jsonify(
                    ok=False,
                    error="Already checked in today"
                ), 409

            if not last:
                day_no = 1
            else:
                gap = (today - last["checkin_date"]).days

                if gap > 1:
                    day_no = 1
                else:
                    day_no = (
                        int(last["day_number"]) % 7
                    ) + 1

            reward = CHECKIN_REWARDS[day_no - 1]

            db.execute("""
                INSERT INTO daily_checkins(
                    telegram_id,checkin_date,day_number,reward
                )
                VALUES(%s,%s,%s,%s)
            """, (
                tid,
                today,
                day_no,
                reward
            ))

            credit_wallet(
                db,
                tid,
                reward,
                "daily_checkin",
                f"checkin:{tid}:{today.isoformat()}"
            )

        return jsonify(
            ok=True,
            day=day_no,
            reward=str(reward)
        ), 200

    except Exception:
        app.logger.exception("Check-in failed")
        return jsonify(ok=False, error="Server error"), 500


@app.route("/api/checkin/status", methods=["POST", "OPTIONS"])
def checkin_status():
    if request.method == "OPTIONS":
        return "", 204

    user, _ = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    today = datetime.now(timezone.utc).date()

    try:
        with get_db() as db:
            create_tables(db)

            row = db.execute("""
                SELECT checkin_date,day_number
                FROM daily_checkins
                WHERE telegram_id=%s
                ORDER BY checkin_date DESC
                LIMIT 1
            """, (int(user["id"]),)).fetchone()

        checked = bool(
            row and row["checkin_date"] == today
        )

        if not row or (today - row["checkin_date"]).days > 1:
            next_day = 1
        else:
            next_day = (
                int(row["day_number"]) % 7
            ) + 1

        return jsonify(
            ok=True,
            checked_today=checked,
            next_day=next_day,
            rewards=[str(x) for x in CHECKIN_REWARDS]
        ), 200

    except Exception:
        app.logger.exception("Check-in status failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== TASK LIST ====================

@app.route("/api/tasks", methods=["POST", "OPTIONS"])
def list_tasks():
    if request.method == "OPTIONS":
        return "", 204

    user, _ = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    try:
        with get_db() as db:
            create_tables(db)

            rows = db.execute("""
                SELECT
                    t.id,t.title,t.description,t.task_type,
                    t.reward,t.target_url,t.verification,
                    COALESCE(c.status,'not_started') AS user_status
                FROM tasks t
                LEFT JOIN task_completions c
                    ON c.task_id=t.id
                    AND c.telegram_id=%s
                WHERE t.active=TRUE
                ORDER BY t.id DESC
            """, (int(user["id"]),)).fetchall()

        return jsonify(
            ok=True,
            tasks=[
                {
                    **row,
                    "reward": str(row["reward"])
                }
                for row in rows
            ]
        ), 200

    except Exception:
        app.logger.exception("Task list failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== TASK SUBMISSION ====================

@app.route("/api/tasks/complete", methods=["POST", "OPTIONS"])
def complete_task():
    if request.method == "OPTIONS":
        return "", 204

    user, body = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    try:
        task_id = int(body.get("task_id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Invalid task"), 400

    tid = int(user["id"])

    try:
        with get_db() as db:
            create_tables(db)
            ensure_user(db, user)

            allowed, channel, group = membership_required(
                db,
                tid
            )

            if not allowed:
                return jsonify(
                    ok=False,
                    error="Join official channel and group first",
                    channel_joined=channel,
                    group_joined=group
                ), 403

            task = db.execute("""
                SELECT *
                FROM tasks
                WHERE id=%s AND active=TRUE
                FOR UPDATE
            """, (task_id,)).fetchone()

            if not task:
                return jsonify(ok=False, error="Task not found"), 404

            prior = db.execute("""
                SELECT status
                FROM task_completions
                WHERE task_id=%s AND telegram_id=%s
            """, (task_id, tid)).fetchone()

            if prior:
                return jsonify(
                    ok=False,
                    error="Task already submitted",
                    status=prior["status"]
                ), 409

            # Never auto-credit a task based only on a click.
            # Admin must review and approve the submission.
            db.execute("""
                INSERT INTO task_completions(
                    task_id,telegram_id,status,reward
                )
                VALUES(%s,%s,'pending',%s)
            """, (
                task_id,
                tid,
                task["reward"]
            ))

        return jsonify(
            ok=True,
            status="pending",
            message="Task submitted for review"
        ), 200

    except Exception:
        app.logger.exception("Task submission failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== REFERRAL SUMMARY ====================

@app.route("/api/referral", methods=["POST", "OPTIONS"])
def referral_summary():
    if request.method == "OPTIONS":
        return "", 204

    user, _ = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    tid = int(user["id"])

    try:
        with get_db() as db:
            create_tables(db)

            rows = db.execute("""
                SELECT
                    r.status,
                    u.first_name,
                    u.username
                FROM referrals r
                JOIN users u
                    ON u.telegram_id=r.referred_id
                WHERE r.referrer_id=%s
                ORDER BY r.created_at DESC
                LIMIT 20
            """, (tid,)).fetchall()

            counts = db.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER(
                        WHERE status='approved'
                    ) AS successful,
                    COUNT(*) FILTER(
                        WHERE status='pending'
                    ) AS pending
                FROM referrals
                WHERE referrer_id=%s
            """, (tid,)).fetchone()

            earnings = db.execute("""
                SELECT COALESCE(SUM(amount),0) AS amount
                FROM wallet_transactions
                WHERE telegram_id=%s
                AND transaction_type IN (
                    'referral_bonus',
                    'referral_commission'
                )
                AND status='completed'
            """, (tid,)).fetchone()

        return jsonify(
            ok=True,
            referral_link=(
                f"https://t.me/{BOT_USERNAME}?start={tid}"
            ),
            total=counts["total"],
            successful=counts["successful"],
            pending=counts["pending"],
            earnings=str(earnings["amount"]),
            referrals=rows
        ), 200

    except Exception:
        app.logger.exception("Referral summary failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== BONUS CODE ====================

@app.route("/api/bonus-code", methods=["POST", "OPTIONS"])
def redeem_bonus_code():
    if request.method == "OPTIONS":
        return "", 204

    user, body = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    code = str(body.get("code", "")).strip().upper()

    if not code or len(code) > 64:
        return jsonify(ok=False, error="Enter a valid code"), 400

    tid = int(user["id"])

    try:
        with get_db() as db:
            create_tables(db)
            ensure_user(db, user)

            allowed, channel, group = membership_required(
                db,
                tid
            )

            if not allowed:
                return jsonify(
                    ok=False,
                    error="Join official channel and group first",
                    channel_joined=channel,
                    group_joined=group
                ), 403

            checks = db.execute("""
                SELECT *
                FROM user_checks
                WHERE telegram_id=%s
                FOR UPDATE
            """, (tid,)).fetchone()

            if checks["bonus_code_entered"]:
                return jsonify(
                    ok=False,
                    error="Code already used"
                ), 409

            row = db.execute("""
                SELECT *
                FROM bonus_codes
                WHERE code=%s AND active=TRUE
                FOR UPDATE
            """, (code,)).fetchone()

            if not row:
                return jsonify(
                    ok=False,
                    error="Invalid or inactive code"
                ), 404

            now = datetime.now(timezone.utc)

            if row["release_at"] and row["release_at"] > now:
                return jsonify(
                    ok=False,
                    error="Code is not released yet"
                ), 403

            if row["expires_at"] and row["expires_at"] < now:
                return jsonify(
                    ok=False,
                    error="Code expired"
                ), 410

            if row["claims"] >= row["max_claims"]:
                return jsonify(
                    ok=False,
                    error="Code claim limit reached"
                ), 409

            db.execute("""
                UPDATE bonus_codes
                SET claims=claims+1
                WHERE id=%s
            """, (row["id"],))

            db.execute("""
                UPDATE user_checks
                SET channel_joined=TRUE,
                    group_joined=TRUE,
                    bonus_code_entered=TRUE,
                    bonus_code=%s,
                    updated_at=NOW()
                WHERE telegram_id=%s
            """, (code, tid))

            credit_wallet(
                db,
                tid,
                row["reward"],
                "bonus_code",
                f"bonus-code:{row['id']}:{tid}"
            )

            approve_referral_if_eligible(
                db,
                tid
            )

        return jsonify(
            ok=True,
            reward=str(row["reward"])
        ), 200

    except Exception:
        app.logger.exception("Bonus code redemption failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== WITHDRAWAL REQUEST ====================

@app.route("/api/withdraw", methods=["POST", "OPTIONS"])
def request_withdrawal():
    if request.method == "OPTIONS":
        return "", 204

    user, body = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    tid = int(user["id"])
    upi = str(body.get("upi_id", "")).strip()

    try:
        amount = Decimal(
            str(body.get("amount", ""))
        ).quantize(Decimal("0.01"))

    except (InvalidOperation, ValueError):
        return jsonify(ok=False, error="Invalid amount"), 400

    if (
        not upi
        or len(upi) > 100
        or not re.fullmatch(
            r"[A-Za-z0-9._-]{2,256}@[A-Za-z0-9]{2,64}",
            upi
        )
        or amount <= 0
    ):
        return jsonify(
            ok=False,
            error="Enter valid UPI ID and amount"
        ), 400

    try:
        with get_db() as db:
            create_tables(db)
            ensure_user(db, user)

            allowed, channel, group = membership_required(
                db,
                tid
            )

            if not allowed:
                return jsonify(
                    ok=False,
                    error="Join official channel and group first",
                    channel_joined=channel,
                    group_joined=group
                ), 403

            settings = db.execute("""
                SELECT first_paid_withdrawal
                FROM user_settings
                WHERE telegram_id=%s
                FOR UPDATE
            """, (tid,)).fetchone()

            minimum = (
                MIN_WITHDRAWAL_AFTER_PAID
                if settings["first_paid_withdrawal"]
                else MIN_WITHDRAWAL_FIRST
            )

            if amount < minimum:
                return jsonify(
                    ok=False,
                    error=f"Minimum withdrawal is ₹{minimum}"
                ), 400

            pending = db.execute("""
                SELECT id
                FROM withdrawals
                WHERE telegram_id=%s
                AND status='pending'
                FOR UPDATE
            """, (tid,)).fetchone()

            if pending:
                return jsonify(
                    ok=False,
                    error="A withdrawal request is already pending"
                ), 409

            userrow = db.execute("""
                SELECT balance
                FROM users
                WHERE telegram_id=%s
                FOR UPDATE
            """, (tid,)).fetchone()

            if Decimal(userrow["balance"]) < amount:
                return jsonify(
                    ok=False,
                    error="Insufficient balance"
                ), 400

            db.execute("""
                UPDATE users
                SET balance=balance-%s,
                    upi_id=%s
                WHERE telegram_id=%s
            """, (amount, upi, tid))

            db.execute("""
                UPDATE user_settings
                SET upi_id=%s
                WHERE telegram_id=%s
            """, (upi, tid))

            withdrawal = db.execute("""
                INSERT INTO withdrawals(
                    telegram_id,amount,upi_id,status
                )
                VALUES(%s,%s,%s,'pending')
                RETURNING id
            """, (tid, amount, upi)).fetchone()

            db.execute("""
                INSERT INTO wallet_transactions(
                    telegram_id,amount,transaction_type,
                    status,idempotency_key
                )
                VALUES(%s,%s,'withdrawal','pending',%s)
            """, (
                tid,
                -amount,
                f"withdrawal:{withdrawal['id']}"
            ))

        return jsonify(
            ok=True,
            withdrawal_id=withdrawal["id"],
            status="pending"
        ), 200

    except Exception:
        app.logger.exception("Withdrawal request failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== WALLET DETAILS ====================

@app.route("/api/wallet", methods=["POST", "OPTIONS"])
def wallet_details():
    if request.method == "OPTIONS":
        return "", 204

    user, _ = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    tid = int(user["id"])

    try:
        with get_db() as db:
            create_tables(db)

            u = db.execute("""
                SELECT balance,upi_id
                FROM users
                WHERE telegram_id=%s
            """, (tid,)).fetchone()

            if not u:
                return jsonify(
                    ok=False,
                    error="User not registered"
                ), 404

            tx = db.execute("""
                SELECT amount,transaction_type,status,created_at
                FROM wallet_transactions
                WHERE telegram_id=%s
                ORDER BY created_at DESC
                LIMIT 30
            """, (tid,)).fetchall()

            settings = db.execute("""
                SELECT first_paid_withdrawal
                FROM user_settings
                WHERE telegram_id=%s
            """, (tid,)).fetchone()

            withdrawal_total = db.execute("""
                SELECT COALESCE(SUM(amount),0) AS total
                FROM withdrawals
                WHERE telegram_id=%s
                AND status='paid'
            """, (tid,)).fetchone()

        minimum = (
            MIN_WITHDRAWAL_AFTER_PAID
            if settings and settings["first_paid_withdrawal"]
            else MIN_WITHDRAWAL_FIRST
        )

        return jsonify(
            ok=True,
            balance=str(u["balance"]),
            upi_id=u["upi_id"],
            minimum_withdrawal=str(minimum),
            total_withdrawn=str(withdrawal_total["total"]),
            transactions=[
                {
                    **row,
                    "amount": str(row["amount"]),
                    "created_at": row["created_at"].isoformat()
                }
                for row in tx
            ]
        ), 200

    except Exception:
        app.logger.exception("Wallet details failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== LEADERBOARD ====================

@app.route("/api/leaderboard", methods=["POST", "OPTIONS"])
def leaderboard():
    if request.method == "OPTIONS":
        return "", 204

    user, body = body_user()

    if not user:
        return jsonify(ok=False, error="Invalid Telegram data"), 401

    period = str(
        body.get("period", "monthly")
    ).lower()

    if period not in ("monthly", "all_time"):
        return jsonify(ok=False, error="Invalid period"), 400

    try:
        with get_db() as db:
            create_tables(db)

            if period == "monthly":
                rows = db.execute("""
                    SELECT
                        u.first_name,
                        u.username,
                        COUNT(r.id) AS successful_referrals
                    FROM referrals r
                    JOIN users u
                        ON u.telegram_id=r.referrer_id
                    WHERE r.status='approved'
                    AND r.approved_at >= date_trunc('month',NOW())
                    GROUP BY u.telegram_id
                    ORDER BY successful_referrals DESC
                    LIMIT 50
                """).fetchall()

            else:
                rows = db.execute("""
                    SELECT
                        u.first_name,
                        u.username,
                        COUNT(r.id) AS successful_referrals
                    FROM referrals r
                    JOIN users u
                        ON u.telegram_id=r.referrer_id
                    WHERE r.status='approved'
                    GROUP BY u.telegram_id
                    ORDER BY successful_referrals DESC
                    LIMIT 50
                """).fetchall()

        return jsonify(
            ok=True,
            period=period,
            leaders=rows
        ), 200

    except Exception:
        app.logger.exception("Leaderboard failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== ADMIN: TASK REVIEW ====================

@app.route("/api/admin/task/review", methods=["POST", "OPTIONS"])
def admin_task_review():
    if request.method == "OPTIONS":
        return "", 204

    user, body = body_user()

    if not admin_user(user):
        return jsonify(ok=False, error="Admin access denied"), 403

    try:
        completion_id = int(body.get("completion_id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Invalid completion ID"), 400

    action = str(body.get("action", "")).lower()

    if action not in ("approve", "reject"):
        return jsonify(ok=False, error="Invalid action"), 400

    try:
        with get_db() as db:
            create_tables(db)

            completion = db.execute("""
                SELECT *
                FROM task_completions
                WHERE id=%s
                FOR UPDATE
            """, (completion_id,)).fetchone()

            if not completion:
                return jsonify(ok=False, error="Submission not found"), 404

            if completion["status"] != "pending":
                return jsonify(ok=False, error="Already reviewed"), 409

            if action == "approve":
                db.execute("""
                    UPDATE task_completions
                    SET status='approved',reviewed_at=NOW()
                    WHERE id=%s
                """, (completion_id,))

                tx_key = (
                    f"task:{completion['task_id']}:"
                    f"{completion['telegram_id']}"
                )

                credit_wallet(
                    db,
                    completion["telegram_id"],
                    completion["reward"],
                    "task_reward",
                    tx_key
                )

                tx = db.execute("""
                    SELECT id
                    FROM wallet_transactions
                    WHERE idempotency_key=%s
                """, (tx_key,)).fetchone()

                if tx:
                    credit_referral_commission(
                        db,
                        tx["id"]
                    )

            else:
                db.execute("""
                    UPDATE task_completions
                    SET status='rejected',reviewed_at=NOW()
                    WHERE id=%s
                """, (completion_id,))

        return jsonify(ok=True, status=action), 200

    except Exception:
        app.logger.exception("Task review failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== ADMIN: WITHDRAWAL REVIEW ====================

@app.route("/api/admin/withdrawal/review", methods=["POST", "OPTIONS"])
def admin_withdrawal_review():
    if request.method == "OPTIONS":
        return "", 204

    user, body = body_user()

    if not admin_user(user):
        return jsonify(ok=False, error="Admin access denied"), 403

    try:
        withdrawal_id = int(body.get("withdrawal_id"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="Invalid withdrawal ID"), 400

    action = str(body.get("action", "")).lower()
    reason = str(body.get("reason", "")).strip()

    if action not in ("paid", "reject"):
        return jsonify(ok=False, error="Invalid action"), 400

    if action == "reject" and not reason:
        return jsonify(
            ok=False,
            error="Rejection reason is required"
        ), 400

    try:
        with get_db() as db:
            create_tables(db)

            row = db.execute("""
                SELECT *
                FROM withdrawals
                WHERE id=%s
                FOR UPDATE
            """, (withdrawal_id,)).fetchone()

            if not row:
                return jsonify(ok=False, error="Withdrawal not found"), 404

            if row["status"] != "pending":
                return jsonify(ok=False, error="Already reviewed"), 409

            if action == "paid":
                db.execute("""
                    UPDATE withdrawals
                    SET status='paid',reviewed_at=NOW()
                    WHERE id=%s
                """, (withdrawal_id,))

                db.execute("""
                    UPDATE user_settings
                    SET first_paid_withdrawal=TRUE
                    WHERE telegram_id=%s
                """, (row["telegram_id"],))

                db.execute("""
                    UPDATE wallet_transactions
                    SET status='completed'
                    WHERE idempotency_key=%s
                """, (f"withdrawal:{withdrawal_id}",))

            else:
                db.execute("""
                    UPDATE withdrawals
                    SET status='rejected',
                        rejection_reason=%s,
                        reviewed_at=NOW()
                    WHERE id=%s
                """, (reason, withdrawal_id))

                db.execute("""
                    UPDATE users
                    SET balance=balance+%s
                    WHERE telegram_id=%s
                """, (row["amount"], row["telegram_id"]))

                db.execute("""
                    UPDATE wallet_transactions
                    SET status='rejected'
                    WHERE idempotency_key=%s
                """, (f"withdrawal:{withdrawal_id}",))

                credit_wallet(
                    db,
                    row["telegram_id"],
                    row["amount"],
                    "withdrawal_refund",
                    f"withdrawal-refund:{withdrawal_id}"
                )

        return jsonify(
            ok=True,
            status="paid" if action == "paid" else "rejected"
        ), 200

    except Exception:
        app.logger.exception("Withdrawal review failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== ADMIN: CREATE BONUS CODE ====================

@app.route("/api/admin/bonus-code/create", methods=["POST", "OPTIONS"])
def admin_create_bonus_code():
    if request.method == "OPTIONS":
        return "", 204

    user, body = body_user()

    if not admin_user(user):
        return jsonify(ok=False, error="Admin access denied"), 403

    code = str(body.get("code", "")).strip().upper()

    try:
        reward = Decimal(
            str(body.get("reward", BONUS_REWARD_DEFAULT))
        ).quantize(Decimal("0.01"))

        max_claims = int(body.get("max_claims", 1000))

    except (InvalidOperation, ValueError, TypeError):
        return jsonify(ok=False, error="Invalid bonus settings"), 400

    if (
        not re.fullmatch(r"[A-Z0-9_-]{3,64}", code)
        or reward <= 0
        or reward > Decimal("1000")
        or max_claims < 1
        or max_claims > 100000
    ):
        return jsonify(ok=False, error="Invalid bonus code details"), 400

    try:
        with get_db() as db:
            create_tables(db)

            release_at = body.get("release_at")
            expires_at = body.get("expires_at")

            db.execute("""
                INSERT INTO bonus_codes(
                    code,reward,max_claims,release_at,expires_at,active
                )
                VALUES(%s,%s,%s,%s,%s,TRUE)
            """, (
                code,
                reward,
                max_claims,
                release_at or None,
                expires_at or None
            ))

        return jsonify(
            ok=True,
            code=code,
            reward=str(reward),
            max_claims=max_claims
        ), 200

    except psycopg.errors.UniqueViolation:
        return jsonify(ok=False, error="Bonus code already exists"), 409

    except Exception:
        app.logger.exception("Bonus code creation failed")
        return jsonify(ok=False, error="Server error"), 500


# ==================== MONETAG POSTBACK ====================

@app.route("/postback", methods=["GET"])
def monetag_postback():
    supplied = request.args.get("secret", "")

    if (
        not POSTBACK_SECRET
        or not hmac.compare_digest(
            supplied,
            POSTBACK_SECRET
        )
    ):
        return jsonify(ok=False, error="Unauthorized"), 403

    ymid = request.args.get("ymid", "").strip()
    event_type = request.args.get(
        "event_type", ""
    ).strip().lower()

    reward_type = request.args.get(
        "reward_event_type", ""
    ).strip().lower()

    if not ymid or not event_type:
        return jsonify(ok=False, error="Missing event data"), 400

    try:
        price = Decimal(
            request.args.get("estimated_price", "0") or "0"
        )

        if not price.is_finite() or price < 0:
            raise InvalidOperation()

    except (InvalidOperation, ValueError):
        return jsonify(ok=False, error="Invalid estimated price"), 400

    try:
        with get_db() as db:
            create_tables(db)

            ad = db.execute("""
                SELECT telegram_id,status
                FROM ad_history
                WHERE network='monetag'
                AND event_id=%s
                FOR UPDATE
            """, (ymid,)).fetchone()

            if not ad:
                return jsonify(ok=False, error="Unknown ad session"), 404

            tid = int(ad["telegram_id"])

            reported_id = request.args.get(
                "telegram_id", ""
            ).strip()

            if reported_id:
                try:
                    if int(reported_id) != tid:
                        return jsonify(
                            ok=False,
                            error="Telegram ID mismatch"
                        ), 403
                except ValueError:
                    return jsonify(
                        ok=False,
                        error="Invalid Telegram ID"
                    ), 400

            inserted = db.execute("""
                INSERT INTO network_events(
                    network,ymid,telegram_id,event_type,
                    reward_event_type,zone_id,sub_zone_id,
                    request_var,estimated_price
                )
                VALUES(
                    'monetag',%s,%s,%s,%s,%s,%s,%s,%s
                )
                ON CONFLICT(network,ymid,event_type)
                DO NOTHING
                RETURNING id
            """, (
                ymid,
                tid,
                event_type,
                reward_type,
                request.args.get("zone_id"),
                request.args.get("sub_zone_id"),
                request.args.get("request_var"),
                price
            )).fetchone()

            if not inserted:
                return jsonify(
                    ok=True,
                    message="Duplicate event ignored"
                ), 200

            # Reward only for the configured valued-impression event.
            # Provider postback parameters must match your Monetag dashboard.
            if (
                event_type != "impression"
                or reward_type != "valued"
            ):
                return jsonify(
                    ok=True,
                    message="Event recorded; no reward"
                ), 200

            if ad["status"] == "credited":
                return jsonify(
                    ok=True,
                    message="Already credited"
                ), 200

            db.execute("""
                SELECT telegram_id
                FROM users
                WHERE telegram_id=%s
                FOR UPDATE
            """, (tid,)).fetchone()

            count = db.execute("""
                SELECT COUNT(*) AS total
                FROM ad_history
                WHERE telegram_id=%s
                AND status='credited'
            """, (tid,)).fetchone()

            reward = reward_for_ad(
                int(count["total"]) + 1
            )

            if credit_wallet(
                db,
                tid,
                reward,
                "ad_reward",
                f"monetag:{ymid}"
            ):
                db.execute("""
                    UPDATE ad_history
                    SET reward=%s,status='credited'
                    WHERE network='monetag'
                    AND event_id=%s
                """, (reward, ymid))

        return jsonify(
            ok=True,
            message="Reward credited",
            reward=str(reward)
        ), 200

    except Exception:
        app.logger.exception("Monetag postback failed")
        return jsonify(ok=False, error="Postback processing failed"), 500


# ==================== TELEGRAM BOT WEBHOOK ====================

def send_telegram_message(chat_id, message, reply_markup=None):
    if not BOT_TOKEN or not chat_id:
        return

    payload = {
        "chat_id": chat_id,
        "text": message
    }

    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    data = urllib.parse.urlencode(payload).encode()

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=data
    )

    with urllib.request.urlopen(req, timeout=15) as response:
        response.read()


@app.route("/telegram/webhook", methods=["POST"])
def telegram_webhook():
    if not WEBHOOK_SECRET:
        return jsonify(ok=False, error="Webhook secret not configured"), 503

    supplied = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token", ""
    )

    if not hmac.compare_digest(
        supplied,
        WEBHOOK_SECRET
    ):
        return jsonify(ok=False), 403

    update = request.get_json(silent=True) or {}
    message = update.get("message") or {}
    tguser = message.get("from") or {}
    chat = message.get("chat") or {}

    text = str(message.get("text", "")).strip()

    if text.startswith("/start") and tguser.get("id"):
        try:
            parts = text.split(maxsplit=1)
            referrer_id = None

            if len(parts) == 2:
                try:
                    referrer_id = int(parts[1])
                except ValueError:
                    referrer_id = None

            with get_db() as db:
                create_tables(db)

                ensure_user(
                    db,
                    tguser,
                    referrer_id
                )

            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Open EARNOVA",
                            "web_app": {
                                "url": FRONTEND_URL
                            }
                        }
                    ],
                    [
                        {
                            "text": "Official Channel",
                            "url": CHANNEL_URL
                        },
                        {
                            "text": "Support Group",
                            "url": GROUP_URL
                        }
                    ]
                ]
            }

            send_telegram_message(
                chat.get("id"),
                (
                    "Welcome to EARNOVA!\n\n"
                    "Join our official channel and support group. "
                    "Open the Mini App to continue."
                ),
                keyboard
            )

        except Exception:
            app.logger.exception("Telegram /start failed")

    return jsonify(ok=True), 200


# ==================== RUN ====================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5001))
    )
