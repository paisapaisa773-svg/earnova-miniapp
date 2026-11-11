from flask import Flask, request, jsonify
import os, hmac, hashlib, json, time, uuid, secrets
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlparse
from datetime import datetime, timezone, date
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_BOT_TOKEN = os.environ.get('ADMIN_BOT_TOKEN')
ADMIN_WEBHOOK_SECRET = os.environ.get('ADMIN_WEBHOOK_SECRET', '')
ADMIN_MINI_APP_URL = os.environ.get('ADMIN_MINI_APP_URL', 'https://paisapaisa773-svg.github.io/earnova-miniapp/admin.html')
DATABASE_URL = os.environ.get('DATABASE_URL')
POSTBACK_SECRET = os.environ.get('POSTBACK_SECRET')
FRONTEND_ORIGIN = 'https://paisapaisa773-svg.github.io'
ADMIN_ID = int(os.environ.get('ADMIN_ID', '7691713278'))
BOT_USERNAME = os.environ.get('BOT_USERNAME', 'Earn0vaRewardBot')
CHANNEL_ID = os.environ.get('CHANNEL_ID', '@Earnova0fficial')
GROUP_ID = os.environ.get('GROUP_ID', '')
CHANNEL_URL = 'https://t.me/Earnova0fficial'
GROUP_URL = os.environ.get('GROUP_URL', 'https://t.me/+ncm5joi6ZzMxY2M1')
MIN_WITHDRAWAL_FIRST = Decimal('20.00')
MIN_WITHDRAWAL_AFTER_PAID = Decimal('50.00')
CHECKIN_REWARDS = [Decimal(x) for x in ('5.00','10.00','10.00','15.00','15.00','20.00','25.00')]


def get_db():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL is not configured')
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def create_tables(db):
    # Existing tables are deliberately preserved; only additive tables/columns are created.
    db.execute('''CREATE TABLE IF NOT EXISTS users (
        id BIGSERIAL PRIMARY KEY, telegram_id BIGINT UNIQUE NOT NULL,
        username TEXT, balance NUMERIC(12,2) NOT NULL DEFAULT 0.00,
        created_at TIMESTAMPTZ DEFAULT NOW())''')
    db.execute('''CREATE TABLE IF NOT EXISTS wallet_transactions (
        id BIGSERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
        amount NUMERIC(12,2) NOT NULL, transaction_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', idempotency_key TEXT UNIQUE NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW())''')
    db.execute('''CREATE TABLE IF NOT EXISTS ad_history (
        id BIGSERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
        network TEXT NOT NULL, event_id TEXT, reward NUMERIC(12,2) NOT NULL DEFAULT 0.00,
        status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(network, event_id))''')
    db.execute("ALTER TABLE ad_history ADD COLUMN IF NOT EXISTS batch_id TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ad_history_batch ON ad_history(batch_id,status)")
    db.execute('''CREATE TABLE IF NOT EXISTS ad_batches (
        batch_id TEXT PRIMARY KEY, telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
        required_ads INTEGER NOT NULL DEFAULT 3, reward NUMERIC(12,2) NOT NULL DEFAULT 0.00,
        status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW(),
        completed_at TIMESTAMPTZ)''')
    db.execute('''CREATE TABLE IF NOT EXISTS network_events (
        id BIGSERIAL PRIMARY KEY, network TEXT NOT NULL, ymid TEXT NOT NULL,
        telegram_id BIGINT, event_type TEXT NOT NULL, reward_event_type TEXT,
        zone_id TEXT, sub_zone_id TEXT, request_var TEXT,
        estimated_price NUMERIC(14,7) DEFAULT 0, created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(network, ymid, event_type))''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_ad_history_user_status ON ad_history(telegram_id,status)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_wallet_transactions_user ON wallet_transactions(telegram_id,created_at DESC)')
    db.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id BIGSERIAL PRIMARY KEY, referrer_id BIGINT NOT NULL REFERENCES users(telegram_id),
        referred_id BIGINT UNIQUE NOT NULL REFERENCES users(telegram_id),
        status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW(),
        approved_at TIMESTAMPTZ)''')
    db.execute('''CREATE TABLE IF NOT EXISTS user_checks (
        telegram_id BIGINT PRIMARY KEY REFERENCES users(telegram_id),
        channel_joined BOOLEAN NOT NULL DEFAULT FALSE,
        group_joined BOOLEAN NOT NULL DEFAULT FALSE,
        bonus_code_entered BOOLEAN NOT NULL DEFAULT FALSE,
        bonus_claimed BOOLEAN NOT NULL DEFAULT FALSE,
        bonus_code TEXT, updated_at TIMESTAMPTZ DEFAULT NOW())''')
    db.execute('''CREATE TABLE IF NOT EXISTS daily_checkins (
        id BIGSERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
        checkin_date DATE NOT NULL, day_number INTEGER NOT NULL,
        reward NUMERIC(12,2) NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(telegram_id, checkin_date))''')
    db.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, description TEXT DEFAULT '',
        task_type TEXT NOT NULL DEFAULT 'custom', reward NUMERIC(12,2) NOT NULL DEFAULT 0,
        target_url TEXT, active BOOLEAN NOT NULL DEFAULT TRUE,
        verification TEXT NOT NULL DEFAULT 'manual', created_at TIMESTAMPTZ DEFAULT NOW())''')
    db.execute('''CREATE TABLE IF NOT EXISTS task_completions (
        id BIGSERIAL PRIMARY KEY, task_id BIGINT NOT NULL REFERENCES tasks(id),
        telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
        status TEXT NOT NULL DEFAULT 'pending', reward NUMERIC(12,2) NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT NOW(), reviewed_at TIMESTAMPTZ,
        UNIQUE(task_id, telegram_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS bonus_codes (
        id BIGSERIAL PRIMARY KEY, code TEXT UNIQUE NOT NULL, reward NUMERIC(12,2) NOT NULL DEFAULT 0.50,
        max_claims INTEGER NOT NULL DEFAULT 1000, claims INTEGER NOT NULL DEFAULT 0,
        expires_at TIMESTAMPTZ, active BOOLEAN NOT NULL DEFAULT TRUE,
        release_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT NOW())''')
    db.execute('''CREATE TABLE IF NOT EXISTS withdrawals (
        id BIGSERIAL PRIMARY KEY, telegram_id BIGINT NOT NULL REFERENCES users(telegram_id),
        amount NUMERIC(12,2) NOT NULL, upi_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', rejection_reason TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(), reviewed_at TIMESTAMPTZ)''')
    db.execute('''CREATE TABLE IF NOT EXISTS user_settings (
        telegram_id BIGINT PRIMARY KEY REFERENCES users(telegram_id), upi_id TEXT,
        first_paid_withdrawal BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ DEFAULT NOW())''')
    db.execute('''CREATE TABLE IF NOT EXISTS referral_commissions (
        id BIGSERIAL PRIMARY KEY, referrer_id BIGINT NOT NULL REFERENCES users(telegram_id),
        referred_id BIGINT NOT NULL REFERENCES users(telegram_id), source_transaction_id BIGINT UNIQUE,
        amount NUMERIC(12,2) NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW())''')
    db.execute('''CREATE TABLE IF NOT EXISTS monthly_leaderboard_awards (
        id BIGSERIAL PRIMARY KEY, month_key TEXT NOT NULL, telegram_id BIGINT NOT NULL,
        rank INTEGER NOT NULL, amount NUMERIC(12,2) NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(month_key, telegram_id), UNIQUE(month_key, rank))''')
    db.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT')
    db.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS last_name TEXT')
    db.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT')
    db.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS upi_id TEXT')
    db.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS payment_status TEXT NOT NULL DEFAULT 'pending'")
    db.execute("ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS payment_notification_status TEXT NOT NULL DEFAULT 'not_sent'")


def verify_telegram_data(init_data):
    if not BOT_TOKEN or not init_data:
        return None
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = data.pop('hash', None)
        if not received_hash:
            return None
        auth_date = int(data.get('auth_date', '0'))
        now = int(time.time())
        if auth_date <= 0 or now - auth_date > 86400 or auth_date > now + 60:
            return None
        check_string = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
        secret_key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            return None
        user = json.loads(data.get('user', '{}'))
        return user if isinstance(user, dict) and user.get('id') else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def body_user():
    body = request.get_json(silent=True) or {}
    user = verify_telegram_data(body.get('initData', ''))
    return user, body


def ensure_user(db, user, referrer_id=None):
    tid = int(user['id'])
    try:
        referrer_id = int(referrer_id) if str(referrer_id or '').strip().isdigit() else None
    except (TypeError, ValueError):
        referrer_id = None
    if referrer_id == tid:
        referrer_id = None

    db.execute('''INSERT INTO users(telegram_id,username,first_name,last_name,referred_by)
        VALUES(%s,%s,%s,%s,NULL) ON CONFLICT(telegram_id) DO UPDATE SET
        username=EXCLUDED.username, first_name=EXCLUDED.first_name, last_name=EXCLUDED.last_name''',
        (tid, user.get('username'), user.get('first_name'), user.get('last_name')))
    db.execute('INSERT INTO user_checks(telegram_id) VALUES(%s) ON CONFLICT DO NOTHING', (tid,))
    db.execute('INSERT INTO user_settings(telegram_id) VALUES(%s) ON CONFLICT DO NOTHING', (tid,))

    # Attribute only once, including returning users who open a referral /start link later.
    if referrer_id:
        existing = db.execute('SELECT 1 FROM referrals WHERE referred_id=%s', (tid,)).fetchone()
        referrer_exists = db.execute('SELECT 1 FROM users WHERE telegram_id=%s', (referrer_id,)).fetchone()
        if not existing and referrer_exists:
            db.execute('UPDATE users SET referred_by=%s WHERE telegram_id=%s AND referred_by IS NULL',
                       (referrer_id, tid))
            inserted = db.execute('INSERT INTO referrals(referrer_id,referred_id) VALUES(%s,%s) ON CONFLICT DO NOTHING RETURNING referred_id',
                       (referrer_id, tid)).fetchone()
            if inserted:
                return referrer_id
    return None


def reward_for_ad(ad_number):
    rewards = [Decimal('1.00'),Decimal('1.20'),Decimal('1.50'),Decimal('0.90'),Decimal('0.80'),Decimal('0.70')]
    if ad_number <= len(rewards):
        return rewards[ad_number - 1]
    return max(Decimal('0.10'), Decimal('0.70') - Decimal('0.10') * (ad_number - 6))


def credit_wallet(db, telegram_id, amount, tx_type, key):
    amount = Decimal(str(amount)).quantize(Decimal('0.01'))
    inserted = db.execute('''INSERT INTO wallet_transactions
        (telegram_id,amount,transaction_type,status,idempotency_key)
        VALUES(%s,%s,%s,'completed',%s) ON CONFLICT(idempotency_key) DO NOTHING RETURNING id''',
        (telegram_id, amount, tx_type, key)).fetchone()
    if not inserted:
        return False
    db.execute('UPDATE users SET balance=balance+%s WHERE telegram_id=%s', (amount, telegram_id))
    return True


def approve_referral_and_reward(db, referral_row):
    """Approve one referral; pay ₹5 to referrer and ₹100 for each 10 approvals."""
    referrer_id = int(referral_row['referrer_id'])
    referred_id = int(referral_row['referred_id'])
    approved = db.execute("""UPDATE referrals SET status='approved',approved_at=NOW()
        WHERE referred_id=%s AND status='pending' RETURNING referred_id""", (referred_id,)).fetchone()
    if not approved:
        return False
    credit_wallet(db, referrer_id, Decimal('5.00'), 'referral_bonus', f'referrer-bonus:{referred_id}')
    count = db.execute("SELECT COUNT(*) AS total FROM referrals WHERE referrer_id=%s AND status='approved'", (referrer_id,)).fetchone()
    successful = int(count['total'] or 0)
    if successful > 0 and successful % 10 == 0:
        milestone_number = successful // 10
        credit_wallet(db, referrer_id, Decimal('100.00'), 'referral_milestone_bonus',
                      f'referral-milestone:{referrer_id}:{milestone_number}')
    return True


def membership_status(telegram_id, chat_id):
    if not BOT_TOKEN or not chat_id:
        return None
    import urllib.request, urllib.parse
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/getChatMember'
    payload = urllib.parse.urlencode({'chat_id': chat_id, 'user_id': telegram_id}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=payload), timeout=10) as response:
            data = json.loads(response.read().decode())
        if not data.get('ok'):
            return None
        status = data['result'].get('status')
        return status in ('creator', 'administrator', 'member') or (status == 'restricted' and data['result'].get('is_member', False))
    except Exception:
        app.logger.exception('Telegram membership check failed')
        return None


def verify_required_memberships(telegram_id):
    channel = membership_status(telegram_id, CHANNEL_ID)
    group = membership_status(telegram_id, GROUP_ID) if GROUP_ID else None
    return channel, group


def membership_error(telegram_id):
    channel, group = verify_required_memberships(telegram_id)
    if channel is not True or group is not True:
        if not GROUP_ID:
            return channel, group, 'Support group verification is not configured on the server. Set GROUP_ID in Render.'
        if channel is None or group is None:
            return channel, group, 'Membership could not be verified. Please try again; the bot must be admin in both official chats.'
        return channel, group, 'Join the official channel and support group first.'
    return channel, group, None


@app.after_request
def add_cors(response):
    response.headers['Access-Control-Allow-Origin'] = FRONTEND_ORIGIN
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Telegram-Init-Data'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Vary'] = 'Origin'
    return response


@app.route('/health')
def health():
    try:
        with get_db() as db:
            create_tables(db)
            db.execute('SELECT 1')
        return jsonify(status='EARNOVA Backend Running', database='Connected'), 200
    except Exception:
        app.logger.exception('Health check failed')
        return jsonify(status='error', database='Not connected'), 500


@app.route('/api/register', methods=['POST','OPTIONS'])
def register():
    if request.method == 'OPTIONS': return '', 204
    user, body = body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'), 401
    try:
        referrer_id = body.get('referrer_id') or body.get('start_param')
        with get_db() as db:
            create_tables(db); ensure_user(db,user,referrer_id)
            row = db.execute('SELECT balance,created_at FROM users WHERE telegram_id=%s',(int(user['id']),)).fetchone()
        return jsonify(ok=True,balance=str(row['balance']),created_at=row['created_at'].isoformat()), 200
    except Exception:
        app.logger.exception('Registration failed'); return jsonify(ok=False,error='Server error'),500


@app.route('/api/balance', methods=['POST','OPTIONS'])
def get_balance():
    if request.method == 'OPTIONS': return '',204
    user,_ = body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'),401
    try:
        with get_db() as db:
            create_tables(db); row=db.execute('SELECT balance FROM users WHERE telegram_id=%s',(int(user['id']),)).fetchone()
        if not row: return jsonify(ok=False,error='User not registered'),404
        return jsonify(ok=True,balance=str(row['balance'])),200
    except Exception:
        app.logger.exception('Balance lookup failed'); return jsonify(ok=False,error='Server error'),500


@app.route('/api/membership', methods=['POST','OPTIONS'])
def check_membership():
    if request.method=='OPTIONS': return '',204
    user,_=body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'),401
    channel,group=verify_required_memberships(int(user['id']))
    if channel is True or group is True:
        with get_db() as db:
            create_tables(db); ensure_user(db,user)
            if channel is True: db.execute('UPDATE user_checks SET channel_joined=TRUE WHERE telegram_id=%s',(int(user['id']),))
            if group is True: db.execute('UPDATE user_checks SET group_joined=TRUE WHERE telegram_id=%s',(int(user['id']),))
    return jsonify(ok=True,channel_joined=channel,group_joined=group,channel_url=CHANNEL_URL,group_url=GROUP_URL,
        configured=bool(GROUP_ID)),200


@app.route('/api/ad/start', methods=['POST','OPTIONS'])
def start_ad():
    if request.method=='OPTIONS': return '',204
    user,_=body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'),401
    try:
        tid=int(user['id']); batch_id=uuid.uuid4().hex
        ymids=[f"{batch_id}_{i}" for i in range(1,4)]
        with get_db() as db:
            create_tables(db); ensure_user(db,user)
            db.execute("INSERT INTO ad_batches(batch_id,telegram_id,required_ads,status) VALUES(%s,%s,3,'pending')",(batch_id,tid))
            for index,ymid in enumerate(ymids,1):
                db.execute("INSERT INTO ad_history(telegram_id,network,event_id,status,batch_id) VALUES(%s,'monetag',%s,'pending',%s)",(tid,ymid,batch_id))
        return jsonify(ok=True,batch_id=batch_id,ymids=ymids,required_ads=3,network='monetag'),200
    except Exception:
        app.logger.exception('Ad batch creation failed'); return jsonify(ok=False,error='Could not start ad session'),500


@app.route('/api/ad/status', methods=['POST','OPTIONS'])
def ad_status():
    if request.method=='OPTIONS': return '',204
    user,body=body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'),401
    batch_id=str(body.get('batch_id','')).strip()
    ymid=str(body.get('ymid','')).strip()
    try:
        with get_db() as db:
            create_tables(db)
            if batch_id:
                batch=db.execute("SELECT required_ads,reward,status FROM ad_batches WHERE batch_id=%s AND telegram_id=%s",(batch_id,int(user['id']))).fetchone()
                if not batch: return jsonify(ok=False,error='Ad session not found'),404
                progress=db.execute("SELECT COUNT(*) AS verified FROM ad_history WHERE batch_id=%s AND status='credited'",(batch_id,)).fetchone()
                return jsonify(ok=True,status=batch['status'],reward=str(batch['reward']),
                    ads_verified=int(progress['verified']),required_ads=int(batch['required_ads']),
                    credited=batch['status']=='credited'),200
            if not ymid or len(ymid)>100: return jsonify(ok=False,error='Invalid ad session'),400
            row=db.execute("SELECT status,reward FROM ad_history WHERE network='monetag' AND event_id=%s AND telegram_id=%s",(ymid,int(user['id']))).fetchone()
        if not row: return jsonify(ok=False,error='Ad session not found'),404
        return jsonify(ok=True,status=row['status'],reward=str(row['reward']),credited=row['status']=='credited'),200
    except Exception:
        app.logger.exception('Ad status lookup failed'); return jsonify(ok=False,error='Server error'),500


@app.route('/api/checkin', methods=['POST','OPTIONS'])
def daily_checkin():
    if request.method == 'OPTIONS': return '', 204
    user, _ = body_user()
    if not user: return jsonify(ok=False, error='Invalid Telegram data'), 401
    tid = int(user['id'])
    today = datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Kolkata')).date()

    # Preserve the existing official-chat membership requirement.
    channel, group, gate_error = membership_error(tid)
    if gate_error:
        return jsonify(ok=False, error=gate_error, channel_joined=channel, group_joined=group), 403

    try:
        with get_db() as db:
            create_tables(db)
            ensure_user(db, user)
            # Serialize claims for this user so two simultaneous requests cannot double-credit.
            db.execute('SELECT telegram_id FROM users WHERE telegram_id=%s FOR UPDATE', (tid,)).fetchone()
            last = db.execute(
                'SELECT checkin_date, day_number FROM daily_checkins WHERE telegram_id=%s ORDER BY checkin_date DESC LIMIT 1',
                (tid,)
            ).fetchone()
            if last and last['checkin_date'] == today:
                return jsonify(ok=False, error="Already checked in today"), 409

            valid_task = db.execute("""SELECT id FROM task_completions
                WHERE telegram_id=%s AND status='completed' AND reviewed_at IS NOT NULL
                AND DATE(reviewed_at AT TIME ZONE 'Asia/Kolkata')=%s
                ORDER BY reviewed_at DESC LIMIT 1""", (tid, today)).fetchone()
            if not valid_task:
                return jsonify(ok=False, error='Complete at least one valid task and wait for its approval today to unlock Daily Check-in.'), 403

            day_no = 1 if not last or (today - last['checkin_date']).days > 1 else (int(last['day_number']) % 7) + 1
            reward = CHECKIN_REWARDS[day_no - 1]
            inserted = db.execute("""INSERT INTO daily_checkins(telegram_id,checkin_date,day_number,reward)
                VALUES(%s,%s,%s,%s) ON CONFLICT(telegram_id,checkin_date) DO NOTHING RETURNING id""",
                (tid, today, day_no, reward)).fetchone()
            if not inserted:
                return jsonify(ok=False, error="Already checked in today"), 409
            if not credit_wallet(db, tid, reward, 'daily_checkin', f'checkin:{tid}:{today.isoformat()}'):
                raise RuntimeError('Daily check-in wallet credit was already processed')
        return jsonify(ok=True, day=day_no, reward=str(reward)), 200
    except Exception:
        app.logger.exception('Daily check-in failed')
        return jsonify(ok=False, error='Server error'), 500


@app.route('/api/checkin/status', methods=['POST','OPTIONS'])
def checkin_status():
    if request.method == 'OPTIONS': return '', 204
    user, _ = body_user()
    if not user: return jsonify(ok=False, error='Invalid Telegram data'), 401
    today = datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Kolkata')).date()
    tid = int(user['id'])
    try:
        with get_db() as db:
            create_tables(db)
            row = db.execute(
                'SELECT checkin_date,day_number FROM daily_checkins WHERE telegram_id=%s ORDER BY checkin_date DESC LIMIT 1',
                (tid,)
            ).fetchone()
            valid_task = db.execute("""SELECT id FROM task_completions
                WHERE telegram_id=%s AND status='completed' AND reviewed_at IS NOT NULL
                AND DATE(reviewed_at AT TIME ZONE 'Asia/Kolkata')=%s
                ORDER BY reviewed_at DESC LIMIT 1""", (tid, today)).fetchone()
        checked = bool(row and row['checkin_date'] == today)
        next_day = 1 if not row or (today - row['checkin_date']).days > 1 else (int(row['day_number']) % 7) + 1
        last_day = int(row['day_number']) if row else 0
        can_claim = bool(valid_task) and not checked
        return jsonify(
            ok=True, checked_today=checked, next_day=next_day, last_day=last_day,
            valid_task_today=bool(valid_task), can_claim=can_claim,
            task_requirement='At least one task must be approved today.',
            rewards=[str(x) for x in CHECKIN_REWARDS]
        ), 200
    except Exception:
        app.logger.exception('Check-in status failed')
        return jsonify(ok=False, error='Server error'), 500


@app.route('/api/tasks', methods=['POST','OPTIONS'])
def list_tasks():
    if request.method=='OPTIONS': return '',204
    user,_=body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'),401
    try:
        with get_db() as db:
            create_tables(db); ensure_user(db,user)
            rows=db.execute('''SELECT t.id,t.title,t.description,t.task_type,t.reward,t.target_url,t.verification,
                COALESCE(c.status,'not_started') AS user_status FROM tasks t
                LEFT JOIN task_completions c ON c.task_id=t.id AND c.telegram_id=%s
                WHERE t.active=TRUE ORDER BY t.id DESC''',(int(user['id']),)).fetchall()
        return jsonify(ok=True,tasks=[{**r,'reward':str(r['reward'])} for r in rows]),200
    except Exception:
        app.logger.exception('Task list failed'); return jsonify(ok=False,error='Server error'),500


@app.route('/api/tasks/complete', methods=['POST','OPTIONS'])
def complete_task():
    if request.method=='OPTIONS': return '',204
    user,body=body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'),401
    try: task_id=int(body.get('task_id'))
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid task'),400
    tid=int(user['id'])
    channel, group, gate_error = membership_error(tid)
    if gate_error: return jsonify(ok=False,error=gate_error,channel_joined=channel,group_joined=group),403
    try:
        with get_db() as db:
            create_tables(db); ensure_user(db,user)
            task=db.execute('SELECT * FROM tasks WHERE id=%s AND active=TRUE',(task_id,)).fetchone()
            if not task: return jsonify(ok=False,error='Task not found'),404
            prior=db.execute('SELECT status FROM task_completions WHERE task_id=%s AND telegram_id=%s',(task_id,tid)).fetchone()
            if prior: return jsonify(ok=False,error='Task already submitted',status=prior['status']),409
            # Link-visit tasks are recorded as pending/manual review unless explicitly configured otherwise.
            status='pending'
            db.execute('INSERT INTO task_completions(task_id,telegram_id,status,reward) VALUES(%s,%s,%s,%s)',(task_id,tid,status,task['reward']))
        return jsonify(ok=True,status=status,message='Task submitted for review' if status=='pending' else 'Task completed'),200
    except Exception:
        app.logger.exception('Task completion failed'); return jsonify(ok=False,error='Server error'),500
@app.route('/api/referral', methods=['POST','OPTIONS'])
def referral_summary():
    if request.method=='OPTIONS': return '',204
    user,_=body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'),401
    tid=int(user['id'])
    try:
        with get_db() as db:
            create_tables(db)
            rows=db.execute('''SELECT r.status,u.first_name,u.username FROM referrals r JOIN users u ON u.telegram_id=r.referred_id
                WHERE r.referrer_id=%s ORDER BY r.created_at DESC LIMIT 20''',(tid,)).fetchall()
            counts=db.execute("SELECT COUNT(*) total,COUNT(*) FILTER(WHERE status='approved') successful,COUNT(*) FILTER(WHERE status='pending') pending FROM referrals WHERE referrer_id=%s",(tid,)).fetchone()
            earnings=db.execute("SELECT COALESCE(SUM(amount),0) amount FROM wallet_transactions WHERE telegram_id=%s AND transaction_type IN ('referral_bonus','referral_commission','referral_milestone_bonus') AND status='completed'",(tid,)).fetchone()
        return jsonify(ok=True,referral_link=f'https://t.me/{BOT_USERNAME}?start={tid}',total=counts['total'],successful=counts['successful'],pending=counts['pending'],earnings=str(earnings['amount']),referrals=rows),200
    except Exception:
        app.logger.exception('Referral summary failed'); return jsonify(ok=False,error='Server error'),500


@app.route('/api/referral/progress', methods=['POST','OPTIONS'])
def referral_progress():
    if request.method=='OPTIONS': return '',204
    user,_=body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'),401
    tid=int(user['id'])
    try:
        channel,group=verify_required_memberships(tid)
        with get_db() as db:
            create_tables(db); ensure_user(db,user)
            ref=db.execute("SELECT status FROM referrals WHERE referred_id=%s",(tid,)).fetchone()
            checks=db.execute("SELECT bonus_code_entered FROM user_checks WHERE telegram_id=%s",(tid,)).fetchone()
        steps=[
            {'id':'channel','title':'Join Official Channel','done':channel is True},
            {'id':'group','title':'Join Support Group','done':group is True},
            {'id':'code','title':'Enter Daily Bonus Code','done':bool(checks and checks['bonus_code_entered'])}
        ]
        return jsonify(ok=True,is_referred=bool(ref),referral_status=ref['status'] if ref else None,
            steps=steps,completed=sum(1 for x in steps if x['done']),total=3,
            bonus_unlocked=bool(ref and ref['status']=='approved')),200
    except Exception:
        app.logger.exception('Referral progress failed'); return jsonify(ok=False,error='Server error'),500


@app.route('/api/bonus-code', methods=['POST','OPTIONS'])
def redeem_bonus_code():
    if request.method=='OPTIONS': return '',204
    user,body=body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'),401
    code=str(body.get('code','')).strip().upper()
    if not code or len(code)>64: return jsonify(ok=False,error='Enter a valid code'),400
    tid=int(user['id'])
    try:
        with get_db() as db:
            create_tables(db); ensure_user(db,user)
            checks=db.execute('SELECT * FROM user_checks WHERE telegram_id=%s FOR UPDATE',(tid,)).fetchone()
            if checks['bonus_code_entered']: return jsonify(ok=False,error='Code already used'),409
            channel,group=verify_required_memberships(tid)
            if channel is not True or group is not True:
                return jsonify(ok=False,error='Join the official channel and group first',channel_joined=channel,group_joined=group),403
            row=db.execute('SELECT * FROM bonus_codes WHERE code=%s AND active=TRUE FOR UPDATE',(code,)).fetchone()
            if not row: return jsonify(ok=False,error='Invalid or inactive code'),404
            if row['release_at'] and row['release_at']>datetime.now(timezone.utc): return jsonify(ok=False,error='Code is not released yet'),403
            if row['expires_at'] and row['expires_at']<datetime.now(timezone.utc): return jsonify(ok=False,error='Code expired'),410
            if row['claims']>=row['max_claims']: return jsonify(ok=False,error='Code claim limit reached'),409
            db.execute('UPDATE bonus_codes SET claims=claims+1 WHERE id=%s',(row['id'],))
            db.execute('UPDATE user_checks SET channel_joined=TRUE,group_joined=TRUE,bonus_code_entered=TRUE,bonus_code=%s,updated_at=NOW() WHERE telegram_id=%s',(code,tid))
            credit_wallet(db,tid,row['reward'],'bonus_code',f'bonus-code:{row["id"]}:{tid}')
            # Existing channel, group and bonus-code checks verify the referral.
            ref=db.execute('SELECT referrer_id,referred_id FROM referrals WHERE referred_id=%s AND status=%s FOR UPDATE',(tid,'pending')).fetchone()
            referral_approved=False
            if ref:
                referral_approved=approve_referral_and_reward(db,ref)
                if referral_approved:
                    db.execute('UPDATE user_checks SET bonus_claimed=TRUE WHERE telegram_id=%s',(tid,))
        return jsonify(ok=True,reward=str(row['reward']),referral_bonus_unlocked=bool(referral_approved)),200
    except Exception:
        app.logger.exception('Bonus code redemption failed'); return jsonify(ok=False,error='Server error'),500


@app.route('/api/withdraw', methods=['POST','OPTIONS'])
def request_withdrawal():
    if request.method=='OPTIONS': return '',204
    user,body=body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'),401
    tid=int(user['id']); upi=str(body.get('upi_id','')).strip()
    try: amount=Decimal(str(body.get('amount',''))).quantize(Decimal('0.01'))
    except (InvalidOperation,ValueError): return jsonify(ok=False,error='Invalid amount'),400
    if not upi or len(upi)>100 or '@' not in upi or amount<=0: return jsonify(ok=False,error='Enter valid UPI ID and amount'),400
    try:
        with get_db() as db:
            create_tables(db); ensure_user(db,user)
            settings=db.execute('SELECT first_paid_withdrawal FROM user_settings WHERE telegram_id=%s FOR UPDATE',(tid,)).fetchone()
            minimum=MIN_WITHDRAWAL_AFTER_PAID if settings['first_paid_withdrawal'] else MIN_WITHDRAWAL_FIRST
            if amount<minimum: return jsonify(ok=False,error=f'Minimum withdrawal is ₹{minimum}'),400
            pending=db.execute("SELECT id FROM withdrawals WHERE telegram_id=%s AND status='pending'",(tid,)).fetchone()
            if pending: return jsonify(ok=False,error='A withdrawal request is already pending'),409
            userrow=db.execute('SELECT balance FROM users WHERE telegram_id=%s FOR UPDATE',(tid,)).fetchone()
            if Decimal(userrow['balance'])<amount: return jsonify(ok=False,error='Insufficient balance'),400
            db.execute('UPDATE users SET balance=balance-%s,upi_id=%s WHERE telegram_id=%s',(amount,upi,tid))
            db.execute('UPDATE user_settings SET upi_id=%s WHERE telegram_id=%s',(upi,tid))
            db.execute("INSERT INTO wallet_transactions(telegram_id,amount,transaction_type,status,idempotency_key) VALUES(%s,%s,'withdrawal','pending',%s)",(tid,-amount,f'withdrawal-request:{uuid.uuid4().hex}'))
            row=db.execute('INSERT INTO withdrawals(telegram_id,amount,upi_id) VALUES(%s,%s,%s) RETURNING id',(tid,amount,upi)).fetchone()
        return jsonify(ok=True,withdrawal_id=row['id'],status='pending'),200
    except Exception:
        app.logger.exception('Withdrawal request failed'); return jsonify(ok=False,error='Server error'),500


@app.route('/api/wallet', methods=['POST','OPTIONS'])
def wallet_details():
    if request.method=='OPTIONS': return '',204
    user,_=body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'),401
    tid=int(user['id'])
    try:
        with get_db() as db:
            create_tables(db)
            u=db.execute('SELECT balance,upi_id FROM users WHERE telegram_id=%s',(tid,)).fetchone()
            tx=db.execute('SELECT amount,transaction_type,status,created_at FROM wallet_transactions WHERE telegram_id=%s ORDER BY created_at DESC LIMIT 20',(tid,)).fetchall()
            withdrawals=db.execute("""SELECT id,amount,status,rejection_reason,created_at,reviewed_at
                FROM withdrawals WHERE telegram_id=%s ORDER BY created_at DESC LIMIT 20""",(tid,)).fetchall()
            settings=db.execute('SELECT first_paid_withdrawal FROM user_settings WHERE telegram_id=%s',(tid,)).fetchone()
        return jsonify(ok=True,balance=str(u['balance']),upi_id=u['upi_id'],minimum_withdrawal=str(MIN_WITHDRAWAL_AFTER_PAID if settings and settings['first_paid_withdrawal'] else MIN_WITHDRAWAL_FIRST),transactions=[{**r,'amount':str(r['amount']),'created_at':r['created_at'].isoformat()} for r in tx],withdrawals=[{**r,'amount':str(r['amount']),'created_at':r['created_at'].isoformat() if r['created_at'] else None,'reviewed_at':r['reviewed_at'].isoformat() if r['reviewed_at'] else None} for r in withdrawals]),200
    except Exception:
        app.logger.exception('Wallet details failed'); return jsonify(ok=False,error='Server error'),500


@app.route('/api/leaderboard', methods=['POST','OPTIONS'])
def leaderboard():
    if request.method=='OPTIONS': return '',204
    user,body=body_user()
    if not user: return jsonify(ok=False,error='Invalid Telegram data'),401
    period=str(body.get('period','monthly')).lower()
    if period not in ('monthly','all_time'): return jsonify(ok=False,error='Invalid period'),400
    try:
        with get_db() as db:
            create_tables(db)
            if period=='monthly':
                rows=db.execute("SELECT u.first_name,u.username,COUNT(r.id) successful_referrals FROM referrals r JOIN users u ON u.telegram_id=r.referrer_id WHERE r.status='approved' AND r.approved_at >= (date_trunc('month', NOW() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata') GROUP BY u.telegram_id,u.first_name,u.username ORDER BY successful_referrals DESC LIMIT 50").fetchall()
            else:
                rows=db.execute("SELECT u.first_name,u.username,COUNT(r.id) successful_referrals FROM referrals r JOIN users u ON u.telegram_id=r.referrer_id WHERE r.status='approved' GROUP BY u.telegram_id,u.first_name,u.username ORDER BY successful_referrals DESC LIMIT 50").fetchall()
        return jsonify(ok=True,period=period,leaders=rows,prizes=[{'rank':1,'amount':'500','label':'🥇 1st Place'},{'rank':2,'amount':'300','label':'🥈 2nd Place'},{'rank':3,'amount':'200','label':'🥉 3rd Place'}],reward_note='Monthly prizes are reviewed and confirmed by admin after month end.'),200
    except Exception:
        app.logger.exception('Leaderboard failed'); return jsonify(ok=False,error='Server error'),500


@app.route('/postback', methods=['GET'])
def monetag_postback():
    supplied=request.args.get('secret','')
    if not POSTBACK_SECRET or not hmac.compare_digest(supplied,POSTBACK_SECRET): return jsonify(ok=False,error='Unauthorized'),403
    ymid=request.args.get('ymid','').strip(); event_type=request.args.get('event_type','').strip().lower(); reward_type=request.args.get('reward_event_type','').strip().lower()
    if not ymid or not event_type: return jsonify(ok=False,error='Missing event data'),400
    try:
        price=Decimal(request.args.get('estimated_price','0') or '0')
        if not price.is_finite() or price<0: raise InvalidOperation()
    except (InvalidOperation,ValueError): return jsonify(ok=False,error='Invalid estimated price'),400
    try:
        with get_db() as db:
            create_tables(db)
            ad=db.execute("SELECT telegram_id,status,batch_id FROM ad_history WHERE network='monetag' AND event_id=%s FOR UPDATE",(ymid,)).fetchone()
            if not ad: return jsonify(ok=False,error='Unknown ymid'),404
            tid=int(ad['telegram_id'])
            reported=request.args.get('telegram_id','').strip()
            if reported and int(reported)!=tid: return jsonify(ok=False,error='Telegram ID mismatch'),403
            inserted=db.execute('''INSERT INTO network_events(network,ymid,telegram_id,event_type,reward_event_type,zone_id,sub_zone_id,request_var,estimated_price)
                VALUES('monetag',%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(network,ymid,event_type) DO NOTHING RETURNING id''',
                (ymid,tid,event_type,reward_type,request.args.get('zone_id'),request.args.get('sub_zone_id'),request.args.get('request_var'),price)).fetchone()
            if not inserted: return jsonify(ok=True,message='Duplicate event ignored'),200
            if event_type!='impression' or reward_type!='valued': return jsonify(ok=True,message='Event recorded; no reward'),200
            if ad['status']=='credited': return jsonify(ok=True,message='Ad impression already counted'),200
            count=db.execute('''SELECT COUNT(*) AS total
                FROM ad_history ah LEFT JOIN ad_batches b ON b.batch_id=ah.batch_id
                WHERE ah.telegram_id=%s AND ah.status='credited'
                AND (ah.batch_id IS NULL OR b.status='credited' OR ah.batch_id=%s)''',
                (tid,ad['batch_id'] or '')).fetchone()
            reward=reward_for_ad(int(count['total'])+1)
            db.execute("UPDATE ad_history SET reward=%s,status='credited' WHERE network='monetag' AND event_id=%s",(reward,ymid))
            if ad['batch_id']:
                batch=db.execute("SELECT required_ads,status FROM ad_batches WHERE batch_id=%s FOR UPDATE",(ad['batch_id'],)).fetchone()
                if not batch: return jsonify(ok=False,error='Ad batch not found'),404
                progress=db.execute("SELECT COUNT(*) AS verified,COALESCE(SUM(reward),0) AS total_reward FROM ad_history WHERE batch_id=%s AND status='credited'",(ad['batch_id'],)).fetchone()
                if int(progress['verified'])>=int(batch['required_ads']) and batch['status']!='credited':
                    combined=Decimal(str(progress['total_reward'])).quantize(Decimal('0.01'))
                    if credit_wallet(db,tid,combined,'ad_reward','monetag-batch:'+ad['batch_id']):
                        db.execute("UPDATE ad_batches SET reward=%s,status='credited',completed_at=NOW() WHERE batch_id=%s",(combined,ad['batch_id']))
                    else:
                        db.execute("UPDATE ad_batches SET reward=%s,status='credited',completed_at=NOW() WHERE batch_id=%s",(combined,ad['batch_id']))
                    return jsonify(ok=True,message='All three ads verified; combined reward credited',reward=str(combined)),200
                return jsonify(ok=True,message='Verified ad recorded; waiting for remaining ads',
                    ads_verified=int(progress['verified']),required_ads=int(batch['required_ads'])),200
            # Preserve the existing one-ad reward behavior for older sessions.
            if credit_wallet(db,tid,reward,'ad_reward','monetag:'+ymid):
                return jsonify(ok=True,message='Reward credited',reward=str(reward)),200
            return jsonify(ok=True,message='Reward already credited'),200
    except Exception:
        app.logger.exception('Monetag postback processing failed'); return jsonify(ok=False,error='Postback processing failed'),500

# Telegram /start webhook: configure Telegram webhook URL to /telegram/webhook and set WEBHOOK_SECRET.
@app.route('/telegram/webhook', methods=['POST'])
def telegram_webhook():
    secret=os.environ.get('WEBHOOK_SECRET') or os.environ.get('ADMIN_WEBHOOK_SECRET','')
    if secret and request.headers.get('X-Telegram-Bot-Api-Secret-Token','')!=secret:
        return jsonify(ok=False),403
    update=request.get_json(silent=True) or {}; msg=update.get('message') or {}; chat=msg.get('chat') or {}; tguser=msg.get('from') or {}
    text_in=(msg.get('text') or '').strip()
    if text_in.startswith('/start') and tguser.get('id'):
        try:
            # Supports /start <id> and /start@BotName <id> deep links.
            # ensure_user validates the referrer, prevents self-referrals and attributes once.
            import re
            match = re.match(r'^/start(?:@[A-Za-z0-9_]+)?(?:\s+(\d+))?$', text_in)
            referrer_id = match.group(1) if match else None
            already_referred_by_other = False
            with get_db() as db:
                create_tables(db); newly_referred_by=ensure_user(db,tguser,referrer_id)
                if referrer_id:
                    existing_referral = db.execute(
                        '''SELECT COALESCE(r.referrer_id,u.referred_by) AS referrer_id
                           FROM users u LEFT JOIN referrals r ON r.referred_id=u.telegram_id
                           WHERE u.telegram_id=%s''',
                        (int(tguser['id']),)
                    ).fetchone()
                    original_referrer = existing_referral.get('referrer_id') if existing_referral else None
                    already_referred_by_other = bool(
                        original_referrer and int(original_referrer) != int(referrer_id)
                    )
            if already_referred_by_other:
                send_telegram_message(int(referrer_id),
                    "⚠️ Referral Not Successful!\\n\\n"
                    "This user has already been referred by someone else.\\n"
                    "No referral credit was added.")
            if newly_referred_by:
                joined_name=(tguser.get('first_name') or tguser.get('username') or 'New member').strip()
                joined_username=(' @'+tguser['username']) if tguser.get('username') else ''
                send_telegram_message(newly_referred_by,
                    f"🎉 New referral joined EARNOVA!\\n\\n👤 {joined_name}{joined_username} joined using your referral link.\\n⏳ Referral bonus: ₹5 after all 3 steps are completed.")
            first_name=(tguser.get('first_name') or 'there').strip()
            welcome=(
                f"⚡ EARNOVA REWARD\n"
                f"Welcome, {first_name}! 🎉\n\n"
                "Your rewards journey starts here.\n"
                "🎯 Explore available tasks\n"
                "💰 Track your points and wallet\n"
                "👥 Invite friends with your referral link\n"
                "📋 Check your task and withdrawal status\n\n"
                "Tap the button below to open EARNOVA.\n"
                "Good luck and enjoy! 🚀"
            )
            if already_referred_by_other:
                welcome += (
                    "\n\n⚠️ ALREADY REFERRED!\n"
                    "You have already joined through another user's referral link.\n"
                    "🔒 Your original referral is protected. This new link cannot change it."
                )
            send_telegram_message(chat.get('id'), welcome,
                {'inline_keyboard':[[{'text':'🚀 Open EARNOVA','web_app':{'url':'https://paisapaisa773-svg.github.io/earnova-miniapp/'}}],
                                    [{'text':'📢 Official Channel','url':CHANNEL_URL},{'text':'💬 Support Group','url':GROUP_URL}]]})
        except Exception: app.logger.exception('Telegram start handler failed')
    return jsonify(ok=True),200


def send_telegram_message(chat_id,text,reply_markup=None):
    if not BOT_TOKEN or not chat_id: return
    import urllib.request
    payload={'chat_id':chat_id,'text':text}
    if reply_markup: payload['reply_markup']=json.dumps(reply_markup)
    data=__import__('urllib.parse',fromlist=['urlencode']).urlencode(payload).encode()
    req=urllib.request.Request(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',data=data)
    with urllib.request.urlopen(req,timeout=10) as response: response.read()




# ---------------- Secure Admin API ----------------
def verify_admin_init_data(init_data):
    """Validate Telegram Mini App initData using the separate Admin Bot token."""
    if not ADMIN_BOT_TOKEN or not init_data:
        return None
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = data.pop('hash', None)
        if not received_hash:
            return None
        auth_date = int(data.get('auth_date', '0'))
        now = int(time.time())
        if auth_date <= 0 or now - auth_date > 86400 or auth_date > now + 60:
            return None
        check_string = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
        secret_key = hmac.new(b'WebAppData', ADMIN_BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            return None
        user = json.loads(data.get('user', '{}'))
        if not isinstance(user, dict) or int(user.get('id', 0)) != ADMIN_ID:
            return None
        return user
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def admin_body():
    body = request.get_json(silent=True) or {}
    init_data = request.headers.get('X-Telegram-Init-Data', '') or body.get('initData', '')
    user = verify_admin_init_data(init_data)
    return (user, body) if user else (None, body)


def admin_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if request.method == 'OPTIONS':
            return '', 204
        user, body = admin_body()
        if not user:
            return jsonify(ok=False, error='Unauthorized admin session'), 401
        request.admin_user = user
        request.admin_body = body
        return fn(*args, **kwargs)
    return wrapped


def admin_error(label):
    app.logger.exception('Admin API failed: %s', label)
    return jsonify(ok=False, error='Admin operation failed; check server logs'), 500


@app.route('/api/admin/dashboard', methods=['POST','OPTIONS'])
@admin_required
def admin_dashboard():
    if request.method == 'OPTIONS': return '', 204
    try:
        with get_db() as db:
            create_tables(db)
            d = db.execute("""SELECT
                (SELECT COUNT(*) FROM users) total_users,
                (SELECT COALESCE(SUM(balance),0) FROM users) total_balance,
                (SELECT COUNT(*) FROM withdrawals WHERE status='pending') pending_withdrawals,
                (SELECT COUNT(*) FROM withdrawals) total_withdrawals,
                (SELECT COUNT(*) FROM task_completions WHERE status='pending') pending_tasks,
                (SELECT COUNT(*) FROM task_completions WHERE status='completed') completed_tasks,
                (SELECT COUNT(*) FROM referrals) total_referrals,
                (SELECT COALESCE(SUM(amount),0) FROM wallet_transactions WHERE transaction_type='ad_reward' AND status='completed') ad_rewards""").fetchone()
        return jsonify(ok=True, **{k: str(v) if isinstance(v, Decimal) else v for k,v in d.items()})
    except Exception: return admin_error('dashboard')


@app.route('/api/admin/withdrawals', methods=['POST','OPTIONS'])
@admin_required
def admin_withdrawals():
    if request.method == 'OPTIONS': return '', 204
    try:
        status = str(request.admin_body.get('status','all'))
        with get_db() as db:
            create_tables(db)
            if status == 'all':
                rows = db.execute("""SELECT id,telegram_id,amount,upi_id,status,rejection_reason,created_at,payment_status,payment_notification_status
                    FROM withdrawals ORDER BY created_at DESC LIMIT 300""").fetchall()
            else:
                rows = db.execute("""SELECT id,telegram_id,amount,upi_id,status,rejection_reason,created_at
                    FROM withdrawals WHERE status=%s ORDER BY created_at DESC LIMIT 300""",(status,)).fetchall()
        return jsonify(ok=True, withdrawals=[{**r,'amount':str(r['amount']),'created_at':r['created_at'].isoformat() if r['created_at'] else None} for r in rows])
    except Exception: return admin_error('withdrawals')


def send_user_telegram_message(chat_id, text):
    if not BOT_TOKEN or not chat_id:
        raise RuntimeError('BOT_TOKEN is not configured')
    import urllib.request
    payload = __import__('urllib.parse', fromlist=['urlencode']).urlencode({
        'chat_id': chat_id, 'text': text
    }).encode()
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage', data=payload
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        result = json.loads(response.read().decode())
    if not result.get('ok'):
        raise RuntimeError('Telegram sendMessage failed')


@app.route('/api/admin/withdrawals/review', methods=['POST','OPTIONS'])
@admin_required
def admin_review_withdrawal():
    if request.method == 'OPTIONS': return '', 204
    body=request.admin_body
    try: wid=int(body.get('withdrawal_id'))
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid withdrawal ID'),400
    action=str(body.get('action','')).lower()
    if action not in ('approve','reject'): return jsonify(ok=False,error='Invalid action'),400
    reason=str(body.get('reason','')).strip()[:500]
    try:
        with get_db() as db:
            create_tables(db)
            w=db.execute("SELECT * FROM withdrawals WHERE id=%s FOR UPDATE",(wid,)).fetchone()
            if not w: return jsonify(ok=False,error='Withdrawal not found'),404
            if w['status'] != 'pending': return jsonify(ok=False,error='Withdrawal already reviewed'),409
            ledger = db.execute("""SELECT id FROM wallet_transactions
                WHERE telegram_id=%s AND transaction_type='withdrawal' AND status='pending'
                AND amount=%s ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
                (w['telegram_id'], -w['amount'])).fetchone()
            if not ledger: return jsonify(ok=False,error='Matching withdrawal ledger entry not found'),409
            if action=='approve':
                db.execute("UPDATE withdrawals SET status='approved',reviewed_at=NOW() WHERE id=%s",(wid,))
                db.execute("UPDATE wallet_transactions SET status='completed' WHERE id=%s",(ledger['id'],))
                db.execute('UPDATE user_settings SET first_paid_withdrawal=TRUE WHERE telegram_id=%s',(w['telegram_id'],))
            else:
                if not reason: return jsonify(ok=False,error='Rejection reason required'),400
                db.execute("UPDATE withdrawals SET status='rejected',rejection_reason=%s,reviewed_at=NOW() WHERE id=%s",(reason,wid))
                db.execute("UPDATE wallet_transactions SET status='rejected' WHERE id=%s",(ledger['id'],))
                credit_wallet(db,int(w['telegram_id']),w['amount'],'withdrawal_refund',f'withdrawal-refund:{wid}')
        notification_sent = False
        if action == 'approve':
            try:
                amount_text = f"{Decimal(str(w['amount'])):.2f}"
                send_user_telegram_message(
                    int(w['telegram_id']),
                    f"🎉 Withdrawal Approved!\n\nAapka ₹{amount_text} withdrawal approve ho gaya hai.\nPayment aapke account mein transfer hone ki process mein hai.\n\nDhanyavaad! ❤️"
                )
                notification_sent = True
            except Exception:
                app.logger.exception('Withdrawal approved but user notification failed')
        return jsonify(ok=True,status='approved' if action=='approve' else 'rejected',
                       notification_sent=notification_sent if action=='approve' else None)
    except Exception: return admin_error('withdrawal review')


@app.route('/api/admin/withdrawals/mark-paid', methods=['POST','OPTIONS'])
@admin_required
def admin_mark_withdrawal_paid():
    if request.method == 'OPTIONS': return '', 204
    body=request.admin_body
    try: wid=int(body.get('withdrawal_id'))
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid withdrawal ID'),400
    try:
        with get_db() as db:
            create_tables(db)
            w=db.execute("SELECT * FROM withdrawals WHERE id=%s FOR UPDATE",(wid,)).fetchone()
            if not w: return jsonify(ok=False,error='Withdrawal not found'),404
            if w['status'] != 'approved': return jsonify(ok=False,error='Approve the withdrawal request first'),409
            if w.get('payment_status') == 'paid': return jsonify(ok=False,error='Payment already marked successful'),409
            db.execute("UPDATE withdrawals SET payment_status='paid' WHERE id=%s",(wid,))
        amount_text = f"{Decimal(str(w['amount'])):.2f}"
        try:
            send_user_telegram_message(
                int(w['telegram_id']),
                f"🎉 Payment Successful!\n\nAapka ₹{amount_text} withdrawal amount aapke account mein successfully transfer ho gaya hai.\n\nDhanyavaad! ❤️"
            )
            notification_status='sent'
        except Exception:
            app.logger.exception('Payment marked paid but success notification failed')
            notification_status='failed'
        with get_db() as db:
            create_tables(db)
            db.execute("UPDATE withdrawals SET payment_notification_status=%s WHERE id=%s",(notification_status,wid))
        return jsonify(ok=True,payment_status='paid',notification_status=notification_status)
    except Exception: return admin_error('mark withdrawal paid')


@app.route('/api/admin/users', methods=['POST','OPTIONS'])
@admin_required
def admin_users():
    if request.method == 'OPTIONS': return '', 204
    q=str(request.admin_body.get('q','')).strip()[:100]
    try:
        with get_db() as db:
            create_tables(db)
            if q:
                rows=db.execute("""SELECT telegram_id,username,first_name,last_name,balance,created_at FROM users
                    WHERE telegram_id::text ILIKE %s OR COALESCE(username,'') ILIKE %s OR COALESCE(first_name,'') ILIKE %s OR COALESCE(last_name,'') ILIKE %s
                    ORDER BY created_at DESC LIMIT 100""",(f'%{q}%',f'%{q}%',f'%{q}%',f'%{q}%')).fetchall()
            else:
                rows=db.execute("SELECT telegram_id,username,first_name,last_name,balance,created_at FROM users ORDER BY created_at DESC LIMIT 50").fetchall()
        return jsonify(ok=True,users=[{**r,'balance':str(r['balance']),'created_at':r['created_at'].isoformat() if r['created_at'] else None} for r in rows])
    except Exception: return admin_error('users')


@app.route('/api/admin/user-transactions', methods=['POST','OPTIONS'])
@admin_required
def admin_user_transactions():
    if request.method == 'OPTIONS': return '', 204
    try: tid=int(request.admin_body.get('telegram_id'))
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid Telegram ID'),400
    try:
        with get_db() as db:
            create_tables(db)
            rows=db.execute("""SELECT amount,transaction_type,status,created_at FROM wallet_transactions
                WHERE telegram_id=%s ORDER BY created_at DESC LIMIT 100""",(tid,)).fetchall()
        return jsonify(ok=True,transactions=[{**r,'amount':str(r['amount']),'created_at':r['created_at'].isoformat() if r['created_at'] else None} for r in rows])
    except Exception: return admin_error('user transactions')


@app.route('/api/admin/user-detail', methods=['POST','OPTIONS'])
@admin_required
def admin_user_detail():
    if request.method == 'OPTIONS': return '', 204
    try:
        tid = int(request.admin_body.get('telegram_id'))
    except (TypeError, ValueError):
        return jsonify(ok=False, error='Invalid Telegram ID'), 400
    try:
        with get_db() as db:
            create_tables(db)
            user = db.execute("""SELECT telegram_id,username,first_name,last_name,balance,created_at
                FROM users WHERE telegram_id=%s""", (tid,)).fetchone()
            if not user:
                return jsonify(ok=False, error='User not found'), 404
            totals = db.execute("""SELECT
                COALESCE(SUM(amount) FILTER (
                    WHERE status='completed' AND amount>0
                    AND transaction_type <> 'withdrawal_refund'
                ),0) total_earned
                FROM wallet_transactions WHERE telegram_id=%s""", (tid,)).fetchone()
            task_stats = db.execute("""SELECT
                COUNT(*) FILTER (WHERE status='completed') completed_tasks,
                COUNT(*) FILTER (WHERE status='rejected') rejected_tasks,
                COUNT(*) FILTER (WHERE status='pending') pending_tasks
                FROM task_completions WHERE telegram_id=%s""", (tid,)).fetchone()
            referral_count = db.execute("SELECT COUNT(*) total FROM referrals WHERE referrer_id=%s AND status='approved'", (tid,)).fetchone()
            withdrawn = db.execute("SELECT COALESCE(SUM(amount),0) total FROM withdrawals WHERE telegram_id=%s AND status='approved'", (tid,)).fetchone()
            transactions = db.execute("""SELECT id,amount,transaction_type,status,created_at
                FROM wallet_transactions WHERE telegram_id=%s ORDER BY created_at DESC LIMIT 100""", (tid,)).fetchall()
            tasks = db.execute("""SELECT c.id,c.task_id,c.status,c.reward,c.created_at,t.title
                FROM task_completions c LEFT JOIN tasks t ON t.id=c.task_id
                WHERE c.telegram_id=%s ORDER BY c.created_at DESC LIMIT 100""", (tid,)).fetchall()
            referrals = db.execute("""SELECT id,referrer_id,referred_id,status,created_at FROM referrals
                WHERE referrer_id=%s OR referred_id=%s ORDER BY created_at DESC LIMIT 100""", (tid,tid)).fetchall()
            withdrawals = db.execute("""SELECT id,amount,upi_id,status,created_at,reviewed_at
                FROM withdrawals WHERE telegram_id=%s ORDER BY created_at DESC LIMIT 100""", (tid,)).fetchall()
        def iso(row, key='created_at'):
            value = row.get(key)
            return value.isoformat() if value else None
        activity = [{
            'id': r['id'], 'label': str(r['transaction_type']).replace('_',' ').title(),
            'amount': str(r['amount']), 'status': r['status'], 'created_at': iso(r)
        } for r in transactions if r['status'] == 'completed']
        referral_rows = [{
            'id': r['id'], 'role': 'referrer' if int(r['referrer_id']) == tid else 'referred',
            'other_user_id': int(r['referred_id'] if int(r['referrer_id']) == tid else r['referrer_id']),
            'status': r['status'], 'created_at': iso(r)
        } for r in referrals]
        return jsonify(ok=True,
            user={**user,'balance':str(user['balance']),'created_at':iso(user)},
            summary={
                'total_earned':str(totals['total_earned']),
                'completed_tasks':task_stats['completed_tasks'],
                'rejected_tasks':task_stats['rejected_tasks'],
                'pending_tasks':task_stats['pending_tasks'],
                'referrals':referral_count['total'],
                'withdrawn':str(withdrawn['total'])
            },
            activity=activity,
            transactions=[{**r,'amount':str(r['amount']),'created_at':iso(r)} for r in transactions],
            tasks=[{**r,'reward':str(r['reward']),'created_at':iso(r)} for r in tasks],
            referrals=referral_rows,
            withdrawals=[{**r,'amount':str(r['amount']),'created_at':iso(r),'reviewed_at':iso(r,'reviewed_at')} for r in withdrawals]
        ), 200
    except Exception:
        return admin_error('user detail')


@app.route('/api/admin/ads', methods=['POST','OPTIONS'])
@admin_required
def admin_ads_monitor():
    if request.method == 'OPTIONS': return '', 204
    raw_tid = request.admin_body.get('telegram_id')
    try:
        tid = int(raw_tid) if raw_tid not in (None, '') else None
        if tid is not None and tid <= 0: raise ValueError()
    except (TypeError, ValueError):
        return jsonify(ok=False, error='Invalid Telegram ID'), 400
    try:
        with get_db() as db:
            create_tables(db)
            user = None
            if tid is not None:
                user = db.execute("""SELECT telegram_id,username,first_name,last_name
                    FROM users WHERE telegram_id=%s""", (tid,)).fetchone()
                session_filter = " WHERE telegram_id=%s"
                event_filter = " WHERE telegram_id=%s"
                params = (tid,)
            else:
                session_filter = event_filter = ""
                params = ()
            summary = db.execute("""
                SELECT COUNT(*) AS sessions_total,
                    COUNT(*) FILTER (WHERE status='credited') AS credited_sessions,
                    COUNT(*) FILTER (WHERE status='pending') AS pending_sessions,
                    COALESCE(SUM(reward),0) AS session_reward_sum
                FROM ad_history""" + session_filter, params).fetchone()
            event_summary = db.execute("""
                SELECT COUNT(*) FILTER (WHERE event_type='impression' AND reward_event_type='valued') AS valued_impressions,
                    COUNT(*) FILTER (WHERE event_type='click' AND reward_event_type='valued') AS valued_clicks,
                    COUNT(*) FILTER (WHERE reward_event_type='non_valued') AS nonvalued_events
                FROM network_events""" + event_filter, params).fetchone()
            wallet_sql = """SELECT COUNT(*) AS wallet_credit_count, COALESCE(SUM(amount),0) AS wallet_credit_amount
                FROM wallet_transactions WHERE transaction_type='ad_reward' AND status='completed'"""
            wallet_params = (tid,) if tid is not None else ()
            if tid is not None: wallet_sql += " AND telegram_id=%s"
            wallet_summary = db.execute(wallet_sql, wallet_params).fetchone()
            sessions = db.execute("""SELECT id,network,event_id,reward,status,created_at
                FROM ad_history""" + session_filter + " ORDER BY created_at DESC LIMIT 100", params).fetchall()
            events = db.execute("""SELECT id,network,ymid,event_type,reward_event_type,estimated_price,created_at
                FROM network_events""" + event_filter + " ORDER BY created_at DESC LIMIT 100", params).fetchall()
        def as_iso(value):
            return value.isoformat() if value else None
        activity = []
        for row in sessions:
            activity.append({'source':'ad_history','id':row['id'],'network':row['network'],
                'event_id':row['event_id'],'reward':str(row['reward']),'status':row['status'],
                'created_at':as_iso(row['created_at'])})
        for row in events:
            activity.append({'source':'network_events','id':row['id'],'network':row['network'],
                'event_id':row['ymid'],'event_type':row['event_type'],
                'reward_event_type':row['reward_event_type'],'reward':str(row['estimated_price']),
                'status':row['reward_event_type'],'created_at':as_iso(row['created_at'])})
        activity.sort(key=lambda item:item.get('created_at') or '', reverse=True)
        return jsonify(ok=True,user=({**user} if user else None),summary={
            'sessions_total':summary['sessions_total'],'credited_sessions':summary['credited_sessions'],
            'pending_sessions':summary['pending_sessions'],'session_reward_sum':str(summary['session_reward_sum']),
            'valued_impressions':event_summary['valued_impressions'],'valued_clicks':event_summary['valued_clicks'],
            'nonvalued_events':event_summary['nonvalued_events'],
            'wallet_credit_count':wallet_summary['wallet_credit_count'],
            'wallet_credit_amount':str(wallet_summary['wallet_credit_amount'])
        },activity=activity[:150]), 200
    except Exception:
        return admin_error('ads monitor')


@app.route('/api/admin/tasks', methods=['POST','OPTIONS'])
@admin_required
def admin_tasks():
    if request.method == 'OPTIONS': return '', 204
    try:
        with get_db() as db:
            create_tables(db)
            rows=db.execute("SELECT id,title,description,task_type,reward,target_url,active,verification FROM tasks ORDER BY id DESC").fetchall()
        return jsonify(ok=True,tasks=[{**r,'reward':str(r['reward'])} for r in rows])
    except Exception: return admin_error('tasks')


@app.route('/api/admin/tasks/create', methods=['POST','OPTIONS'])
@admin_required
def admin_create_task():
    if request.method == 'OPTIONS': return '', 204
    b=request.admin_body
    title=str(b.get('title','')).strip()[:160]
    try: reward=Decimal(str(b.get('reward',''))).quantize(Decimal('0.01'))
    except (InvalidOperation,ValueError): return jsonify(ok=False,error='Invalid reward'),400
    verification=str(b.get('verification','manual'))
    target_url=str(b.get('target_url','')).strip()[:1000]
    parsed_url=urlparse(target_url)
    if not title or not reward.is_finite() or reward<=0 or verification != 'manual':
        return jsonify(ok=False,error='Enter a title and positive reward. Only manual review tasks are supported.'),400
    if parsed_url.scheme != 'https' or not parsed_url.netloc:
        return jsonify(ok=False,error='Enter a valid HTTPS sponsor URL.'),400
    try:
        with get_db() as db:
            create_tables(db)
            row=db.execute("""INSERT INTO tasks(title,description,task_type,reward,target_url,verification)
                VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",(title,str(b.get('description',''))[:1000],str(b.get('task_type','custom'))[:40],reward,target_url,verification)).fetchone()
        return jsonify(ok=True,task_id=row['id'])
    except Exception: return admin_error('create task')


@app.route('/api/admin/tasks/update', methods=['POST','OPTIONS'])
@admin_required
def admin_update_task():
    if request.method == 'OPTIONS': return '', 204
    try: task_id=int(request.admin_body.get('task_id'))
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid task ID'),400
    active=request.admin_body.get('active')
    if not isinstance(active,bool): return jsonify(ok=False,error='Active must be true or false'),400
    try:
        with get_db() as db:
            create_tables(db)
            row=db.execute("UPDATE tasks SET active=%s WHERE id=%s RETURNING id",(active,task_id)).fetchone()
        if not row: return jsonify(ok=False,error='Task not found'),404
        return jsonify(ok=True)
    except Exception: return admin_error('update task')


@app.route('/api/admin/task-submissions', methods=['POST','OPTIONS'])
@admin_required
def admin_task_submissions():
    if request.method == 'OPTIONS': return '', 204
    status=str(request.admin_body.get('status','pending'))
    try:
        with get_db() as db:
            create_tables(db)
            rows=db.execute("""SELECT c.id,c.task_id,c.telegram_id,c.status,c.reward,c.created_at,t.title
                FROM task_completions c JOIN tasks t ON t.id=c.task_id
                WHERE c.status=%s ORDER BY c.created_at ASC LIMIT 300""",(status,)).fetchall()
        return jsonify(ok=True,submissions=[{**r,'reward':str(r['reward']),'created_at':r['created_at'].isoformat() if r['created_at'] else None} for r in rows])
    except Exception: return admin_error('task submissions')


@app.route('/api/admin/task-submissions/review', methods=['POST','OPTIONS'])
@admin_required
def admin_review_task_submission():
    if request.method == 'OPTIONS': return '', 204
    b=request.admin_body
    try: sid=int(b.get('submission_id'))
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid submission ID'),400
    action=str(b.get('action','')).lower()
    if action not in ('approve','reject'): return jsonify(ok=False,error='Invalid action'),400
    try:
        with get_db() as db:
            create_tables(db)
            row=db.execute("SELECT * FROM task_completions WHERE id=%s FOR UPDATE",(sid,)).fetchone()
            if not row: return jsonify(ok=False,error='Submission not found'),404
            if row['status']!='pending': return jsonify(ok=False,error='Submission already reviewed'),409
            if action=='approve':
                db.execute("UPDATE task_completions SET status='completed',reviewed_at=NOW() WHERE id=%s",(sid,))
                credit_wallet(db,int(row['telegram_id']),row['reward'],'task_reward',f'task:{row["task_id"]}:{row["telegram_id"]}')
            else:
                db.execute("UPDATE task_completions SET status='rejected',reviewed_at=NOW() WHERE id=%s",(sid,))
        return jsonify(ok=True,status='approved' if action=='approve' else 'rejected')
    except Exception: return admin_error('review task submission')


@app.route('/api/admin/bonus-codes', methods=['POST','OPTIONS'])
@admin_required
def admin_bonus_codes():
    if request.method == 'OPTIONS': return '', 204
    try:
        with get_db() as db:
            create_tables(db)
            rows=db.execute("SELECT id,code,reward,max_claims,claims,expires_at,active,release_at FROM bonus_codes ORDER BY id DESC LIMIT 300").fetchall()
        return jsonify(ok=True,codes=[{**r,'reward':str(r['reward']),'expires_at':r['expires_at'].isoformat() if r['expires_at'] else None,'release_at':r['release_at'].isoformat() if r['release_at'] else None} for r in rows])
    except Exception: return admin_error('bonus codes')


@app.route('/api/admin/bonus-codes/create', methods=['POST','OPTIONS'])
@admin_required
def admin_create_bonus_code():
    if request.method == 'OPTIONS': return '', 204
    b=request.admin_body
    code=str(b.get('code','')).strip().upper()[:64]
    try:
        reward=Decimal(str(b.get('reward',''))).quantize(Decimal('0.01'))
        max_claims=int(b.get('max_claims',100))
    except (InvalidOperation,ValueError,TypeError): return jsonify(ok=False,error='Invalid reward or claim limit'),400
    if not code or not reward.is_finite() or reward<=0 or max_claims<1 or max_claims>1000000:
        return jsonify(ok=False,error='Valid code, positive reward and claim limit required'),400
    try:
        with get_db() as db:
            create_tables(db)
            row=db.execute("""INSERT INTO bonus_codes(code,reward,max_claims,release_at,expires_at)
                VALUES(%s,%s,%s,%s,%s) RETURNING id""",(code,reward,max_claims,b.get('release_at') or None,b.get('expires_at') or None)).fetchone()
        return jsonify(ok=True,bonus_id=row['id'])
    except Exception:
        app.logger.exception('Admin create bonus failed')
        return jsonify(ok=False,error='Could not create bonus code (code may already exist)'),400


@app.route('/api/admin/bonus-codes/update', methods=['POST','OPTIONS'])
@admin_required
def admin_update_bonus_code():
    if request.method == 'OPTIONS': return '', 204
    try: bonus_id=int(request.admin_body.get('bonus_id'))
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid bonus ID'),400
    active=request.admin_body.get('active')
    if not isinstance(active,bool): return jsonify(ok=False,error='Active must be true or false'),400
    try:
        with get_db() as db:
            create_tables(db)
            row=db.execute("UPDATE bonus_codes SET active=%s WHERE id=%s RETURNING id",(active,bonus_id)).fetchone()
        if not row: return jsonify(ok=False,error='Bonus code not found'),404
        return jsonify(ok=True)
    except Exception: return admin_error('update bonus code')


@app.route('/api/admin/referrals', methods=['POST','OPTIONS'])
@admin_required
def admin_referrals():
    if request.method == 'OPTIONS': return '', 204
    status=str(request.admin_body.get('status','all'))
    try:
        with get_db() as db:
            create_tables(db)
            if status=='all':
                rows=db.execute("""SELECT r.id,r.referrer_id,r.referred_id,r.status,r.created_at,
                    ru.username AS referrer_username,ru.first_name AS referrer_first_name,
                    du.username AS referred_username,du.first_name AS referred_first_name
                    FROM referrals r
                    LEFT JOIN users ru ON ru.telegram_id=r.referrer_id
                    LEFT JOIN users du ON du.telegram_id=r.referred_id
                    ORDER BY r.created_at DESC LIMIT 500""").fetchall()
            else:
                rows=db.execute("""SELECT r.id,r.referrer_id,r.referred_id,r.status,r.created_at,
                    ru.username AS referrer_username,ru.first_name AS referrer_first_name,
                    du.username AS referred_username,du.first_name AS referred_first_name
                    FROM referrals r
                    LEFT JOIN users ru ON ru.telegram_id=r.referrer_id
                    LEFT JOIN users du ON du.telegram_id=r.referred_id
                    WHERE r.status=%s ORDER BY r.created_at DESC LIMIT 500""",(status,)).fetchall()
            summary=db.execute("""SELECT r.referrer_id,
                COALESCE(u.first_name,'') AS first_name,COALESCE(u.username,'') AS username,
                COUNT(*) AS total,
                COUNT(*) FILTER(WHERE r.status='approved') AS approved,
                COUNT(*) FILTER(WHERE r.status='pending') AS pending,
                COUNT(*) FILTER(WHERE r.status='rejected') AS rejected
                FROM referrals r LEFT JOIN users u ON u.telegram_id=r.referrer_id
                GROUP BY r.referrer_id,u.first_name,u.username
                ORDER BY COUNT(*) DESC,r.referrer_id ASC LIMIT 500""").fetchall()
        return jsonify(ok=True,
            referrals=[{**r,'created_at':r['created_at'].isoformat() if r['created_at'] else None} for r in rows],
            summary=summary)
    except Exception: return admin_error('referrals')


@app.route('/api/admin/referrals/review', methods=['POST','OPTIONS'])
@admin_required
def admin_review_referral():
    if request.method == 'OPTIONS': return '', 204
    b=request.admin_body
    try: rid=int(b.get('referral_id'))
    except (TypeError,ValueError): return jsonify(ok=False,error='Invalid referral ID'),400
    action=str(b.get('action','')).lower()
    if action not in ('approve','reject'): return jsonify(ok=False,error='Invalid action'),400
    try:
        with get_db() as db:
            create_tables(db)
            row=db.execute("SELECT * FROM referrals WHERE id=%s FOR UPDATE",(rid,)).fetchone()
            if not row: return jsonify(ok=False,error='Referral not found'),404
            if row['status']!='pending': return jsonify(ok=False,error='Referral already reviewed'),409
            if action=='approve':
                approve_referral_and_reward(db,row)
            else:
                db.execute("UPDATE referrals SET status='rejected' WHERE id=%s",(rid,))
        return jsonify(ok=True,status='approved' if action=='approve' else 'rejected')
    except Exception: return admin_error('review referral')


# Separate admin bot webhook. It does not change the existing user-bot webhook.
def send_admin_telegram_message(chat_id, text, reply_markup=None):
    if not ADMIN_BOT_TOKEN or not chat_id:
        raise RuntimeError('ADMIN_BOT_TOKEN is not configured')
    import urllib.request
    payload = {'chat_id': chat_id, 'text': text}
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    data = __import__('urllib.parse', fromlist=['urlencode']).urlencode(payload).encode()
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/sendMessage', data=data
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        result = json.loads(response.read().decode())
    if not result.get('ok'):
        raise RuntimeError('Telegram sendMessage failed')


@app.route('/admin/telegram/webhook', methods=['POST'])
def admin_telegram_webhook():
    if not ADMIN_BOT_TOKEN or not ADMIN_WEBHOOK_SECRET:
        return jsonify(ok=False, error='Admin bot is not configured'), 503
    supplied = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not hmac.compare_digest(supplied, ADMIN_WEBHOOK_SECRET):
        return jsonify(ok=False), 403

    update = request.get_json(silent=True) or {}
    msg = update.get('message') or {}
    chat = msg.get('chat') or {}
    tguser = msg.get('from') or {}
    text_in = (msg.get('text') or '').strip()
    if not chat.get('id') or not tguser.get('id'):
        return jsonify(ok=True), 200

    if int(tguser['id']) != ADMIN_ID:
        if text_in.startswith('/start'):
            send_admin_telegram_message(chat['id'], '⛔ यह Admin Bot केवल अधिकृत Admin के लिए है।')
        return jsonify(ok=True), 200

    if text_in.startswith('/start'):
        keyboard = {'inline_keyboard': [[{
            'text': '⚙️ Open EARNOVA Admin Panel',
            'web_app': {'url': ADMIN_MINI_APP_URL}
        }]]}
        send_admin_telegram_message(
            chat['id'],
            '✅ EARNOVA Admin Bot चालू है। नीचे बटन दबाकर Admin Panel खोलें।',
            keyboard
        )
    elif text_in.startswith('/help'):
        send_admin_telegram_message(chat['id'], 'Admin Panel खोलने के लिए /start भेजें।')
    return jsonify(ok=True), 200


@app.route('/setup-user-webhook', methods=['GET'])
def setup_user_webhook():
    # One-time setup helper, protected by the existing admin webhook secret.
    supplied = request.args.get('key', '')
    setup_secret = os.environ.get('ADMIN_WEBHOOK_SECRET', '')
    if not setup_secret or not hmac.compare_digest(supplied, setup_secret):
        return jsonify(ok=False, error='Unauthorized'), 403
    if not BOT_TOKEN:
        return jsonify(ok=False, error='BOT_TOKEN is not configured'), 503
    try:
        import urllib.request
        webhook_url = request.url_root.rstrip('/') + '/telegram/webhook'
        payload = __import__('urllib.parse', fromlist=['urlencode']).urlencode({
            'url': webhook_url,
            'secret_token': setup_secret,
            'allowed_updates': json.dumps(['message'])
        }).encode()
        req = urllib.request.Request(
            f'https://api.telegram.org/bot{BOT_TOKEN}/setWebhook', data=payload
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode())
        if not result.get('ok'):
            app.logger.error('User webhook setup rejected by Telegram')
            return jsonify(ok=False, error='Telegram rejected webhook setup'), 502
        return jsonify(ok=True, message='User bot webhook configured'), 200
    except Exception:
        app.logger.exception('User webhook setup failed')
        return jsonify(ok=False, error='Could not configure user webhook'), 502


@app.route('/admin/setup-webhook', methods=['GET'])
def setup_admin_webhook():
    # One-time setup helper. The key is the same secret Telegram will send in webhook headers.
    supplied = request.args.get('key', '')
    if not ADMIN_WEBHOOK_SECRET or not hmac.compare_digest(supplied, ADMIN_WEBHOOK_SECRET):
        return jsonify(ok=False, error='Unauthorized'), 403
    if not ADMIN_BOT_TOKEN:
        return jsonify(ok=False, error='ADMIN_BOT_TOKEN is not configured'), 503
    try:
        import urllib.request
        webhook_url = request.url_root.rstrip('/') + '/admin/telegram/webhook'
        payload = __import__('urllib.parse', fromlist=['urlencode']).urlencode({
            'url': webhook_url,
            'secret_token': ADMIN_WEBHOOK_SECRET,
            'allowed_updates': json.dumps(['message'])
        }).encode()
        req = urllib.request.Request(
            f'https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/setWebhook', data=payload
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode())
        if not result.get('ok'):
            app.logger.error('Admin webhook setup rejected by Telegram')
            return jsonify(ok=False, error='Telegram rejected webhook setup'), 502
        return jsonify(ok=True, message='Admin bot webhook configured', webhook_url=webhook_url), 200
    except Exception:
        app.logger.exception('Admin webhook setup failed')
        return jsonify(ok=False, error='Could not configure admin webhook'), 502


if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5001)))
