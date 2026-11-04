"""經信箱確認的每日匯率提醒；資料與寄送狀態保存在 SQLite。"""
import hashlib
import json
import logging
import re
import secrets
import smtplib
import sqlite3
import ssl
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, render_template, request

bp = Blueprint('alerts', __name__)
logger = logging.getLogger(__name__)
TAIPEI = timezone(timedelta(hours=8))


def now_taipei():
    return datetime.now(TAIPEI)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def positive_decimal(value):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError('目標匯率必須是有限正數。')
    text = str(value)
    if len(text) > 40:
        raise ValueError('匯率數值過長。')
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError('目標匯率必須是有限正數。') from None
    if not number.is_finite() or number <= 0 or not -18 <= number.adjusted() <= 18:
        raise ValueError('目標匯率必須是有限正數，且介於 1e-18 與 1e19 之間。')
    return number


class AlertError(Exception):
    def __init__(self, message, status=429):
        super().__init__(message)
        self.status = status


class AlertService:
    def __init__(self, database, *, sender='', base_url='', smtp_host='smtp.gmail.com',
                 smtp_port=465, smtp_username='', smtp_password=''):
        self.database = str(database)
        self.smtp_host = smtp_host
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.sender = sender or smtp_username
        try:
            self.smtp_port = int(smtp_port)
            valid_smtp = (isinstance(smtp_host, str) and bool(smtp_host) and
                          not re.search(r'\s', smtp_host) and
                          not isinstance(smtp_port, bool) and 1 <= self.smtp_port <= 65535)
        except (ValueError, TypeError):
            self.smtp_port = None
            valid_smtp = False
        self.base_url = base_url.rstrip('/')
        try:
            parsed = urlsplit(self.base_url)
            valid_url = (parsed.scheme in ('http', 'https') and parsed.hostname and
                         (parsed.port is None or 1 <= parsed.port <= 65535) and
                         not parsed.username and not parsed.password and
                         not parsed.query and not parsed.fragment and
                         parsed.path in ('', '/') and not re.search(r'\s', self.base_url))
        except ValueError:
            valid_url = False
        self.enabled = bool(valid_smtp and self.valid_email(smtp_username) and smtp_password and
                            self.valid_email(self.sender) and valid_url)
        with open(Path(__file__).resolve().parent.parent / 'static/currency_list.json',
                  encoding='utf-8') as source:
            self.currencies = {item['code'] for item in json.load(source)}
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL, buy TEXT NOT NULL, sell TEXT NOT NULL,
                    direction TEXT NOT NULL, target TEXT NOT NULL,
                    state TEXT NOT NULL, verify_hash TEXT NOT NULL,
                    cancel_hash TEXT NOT NULL, expires REAL NOT NULL,
                    UNIQUE(email, buy, sell, direction, target)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    key TEXT PRIMARY KEY, alert_id INTEGER NOT NULL,
                    state TEXT NOT NULL, message_id TEXT
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    day TEXT NOT NULL, created REAL NOT NULL,
                    email_hash TEXT NOT NULL, kind TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS attempts_day ON attempts(day);
                CREATE INDEX IF NOT EXISTS attempts_email ON attempts(email_hash, created);
                CREATE TABLE IF NOT EXISTS applications (ip_hash TEXT, created REAL);
                CREATE INDEX IF NOT EXISTS applications_ip ON applications(ip_hash, created);
                CREATE TABLE IF NOT EXISTS cancel_tokens (hash TEXT PRIMARY KEY, alert_id INTEGER NOT NULL);
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def valid_email(value):
        return (isinstance(value, str) and len(value) <= 254 and
                re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}", value))

    def reserve(self, db, key, alert_id, email, kind, now):
        existing = db.execute('SELECT state FROM deliveries WHERE key=?', (key,)).fetchone()
        # shortcut: 回應不明不自動重寄，維護者須向寄信服務查證後處理。
        if existing and existing['state'] != 'failed':
            return False
        stamp = now.timestamp()
        if kind == 'verify' and db.execute(
                "SELECT 1 FROM attempts WHERE email_hash=? AND kind='verify' AND created>?",
                (digest(email), stamp - 600)).fetchone():
            raise AlertError('同一信箱每 10 分鐘最多寄送一次驗證信。')
        if db.execute('SELECT COUNT(*) FROM attempts WHERE day=?',
                      (now.date().isoformat(),)).fetchone()[0] >= 250:
            raise AlertError('今日寄信額度已用完，請明日再試。')
        db.execute('INSERT OR REPLACE INTO deliveries(key, alert_id, state) VALUES(?,?,?)',
                   (key, alert_id, 'sending'))
        db.execute('INSERT INTO attempts VALUES(?,?,?,?)',
                   (now.date().isoformat(), stamp, digest(email), kind))
        return True

    def deliver(self, key, email, subject, content):
        state, message_id = 'failed', None
        smtp = None
        sending = False
        try:
            message = EmailMessage()
            message['From'] = formataddr(('匯率到價提醒', self.sender))
            message['To'] = email
            message['Subject'] = subject
            message['Date'] = format_datetime(now_taipei())
            message['Message-ID'] = make_msgid(domain=self.sender.rsplit('@', 1)[-1])
            message.set_content(content)
            smtp = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=20,
                                   context=ssl.create_default_context())
            smtp.login(self.smtp_username, self.smtp_password)
            sending = True
            refused = smtp.send_message(message, from_addr=self.sender, to_addrs=[email])
            if not refused:
                state, message_id = 'sent', str(message['Message-ID'])
        except smtplib.SMTPResponseException as error:
            logger.warning('SMTP 明確拒絕 key=%s code=%s', key, error.smtp_code)
        except smtplib.SMTPRecipientsRefused:
            logger.warning('SMTP 拒絕收件人 key=%s', key)
        except (smtplib.SMTPException, OSError, ValueError):
            state = 'unknown' if sending else 'failed'
            logger.warning('SMTP 連線失敗 key=%s state=%s', key, state)
        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except (smtplib.SMTPException, OSError):
                    try:
                        smtp.close()
                    except OSError:
                        pass
            with self.connection() as db:
                db.execute('UPDATE deliveries SET state=?, message_id=? WHERE key=?',
                           (state, message_id, key))
            logger.info('提醒寄信結果 key=%s state=%s', key, state)
        return state

    def subscribe(self, payload, ip):
        if not self.enabled:
            raise AlertError('到價提醒尚未設定寄信服務。', 503)
        if not isinstance(payload, dict):
            raise ValueError('請提供有效的訂閱資料。')
        email = payload.get('email')
        if not self.valid_email(email):
            raise ValueError('請輸入有效的 email。')
        email = email.strip().lower()
        buy, sell, direction = (payload.get(k) for k in ('buy_currency', 'sell_currency', 'direction'))
        if (not isinstance(buy, str) or not isinstance(sell, str) or
                buy not in self.currencies or sell not in self.currencies or buy == sell):
            raise ValueError('請選擇兩個不同且支援的幣別。')
        if direction not in ('gte', 'lte'):
            raise ValueError('請選擇大於等於或小於等於。')
        target = format(positive_decimal(payload.get('target_rate')), 'f')
        if '.' in target:
            target = target.rstrip('0').rstrip('.')
        now = now_taipei()
        stamp = now.timestamp()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM applications WHERE created<?', (stamp - 3600,))
            if db.execute('SELECT COUNT(*) FROM applications WHERE ip_hash=?',
                          (digest(ip),)).fetchone()[0] >= 10:
                raise AlertError('申請次數過多，請一小時後再試。')
            db.execute('INSERT INTO applications VALUES(?,?)', (digest(ip), stamp))
        verify, cancel = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        key = str(uuid.uuid4())
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''DELETE FROM cancel_tokens WHERE alert_id IN
                (SELECT id FROM alerts WHERE state='pending' AND expires<?)''', (stamp,))
            db.execute("DELETE FROM alerts WHERE state='pending' AND expires<?", (stamp,))
            db.execute('DELETE FROM attempts WHERE created<?', (stamp - 172800,))
            old = db.execute('''SELECT * FROM alerts WHERE email=? AND buy=? AND sell=?
                                AND direction=? AND target=?''',
                             (email, buy, sell, direction, target)).fetchone()
            if old and old['state'] == 'active':
                return
            if not old or old['state'] == 'cancelled':
                count = db.execute("SELECT COUNT(*) FROM alerts WHERE email=? AND state!='cancelled'",
                                   (email,)).fetchone()[0]
                if count >= 5:
                    raise AlertError('每個信箱最多可設定 5 個提醒。')
            if old:
                alert_id = old['id']
                db.execute('INSERT OR IGNORE INTO cancel_tokens VALUES(?,?)',
                           (old['cancel_hash'], alert_id))
                db.execute('''UPDATE alerts SET state='pending', verify_hash=?, cancel_hash=?,
                              expires=? WHERE id=?''',
                           (digest(verify), digest(cancel), stamp + 86400, alert_id))
            else:
                alert_id = db.execute('''INSERT INTO alerts
                    (email,buy,sell,direction,target,state,verify_hash,cancel_hash,expires)
                    VALUES(?,?,?,?,?,'pending',?,?,?)''',
                    (email, buy, sell, direction, target, digest(verify), digest(cancel),
                     stamp + 86400)).lastrowid
            self.reserve(db, key, alert_id, email, 'verify', now)
        sign = '大於等於' if direction == 'gte' else '小於等於'
        content = (f'你申請了匯率到價提醒：1 {buy} 可兌換的 {sell} {sign} {target}。\n'
                   f'請在 24 小時內開啟並確認：\n{self.base_url}/alerts/verify/{verify}\n\n'
                   f'不是你申請的，請忽略此信。\n取消申請：{self.base_url}/alerts/cancel/{cancel}')
        if self.deliver(key, email, '請確認匯率到價提醒', content) != 'sent':
            raise AlertError('驗證信尚未確認寄出，請稍後再試或聯絡維護者。', 503)

    def token_alert(self, action, token, confirm=False):
        if action not in ('verify', 'cancel') or not 20 <= len(token) <= 100:
            return False
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if action == 'cancel':
                row = db.execute('''SELECT * FROM alerts WHERE cancel_hash=? OR id IN
                    (SELECT alert_id FROM cancel_tokens WHERE hash=?)''',
                    (digest(token), digest(token))).fetchone()
            else:
                row = db.execute('SELECT * FROM alerts WHERE verify_hash=?', (digest(token),)).fetchone()
            if not row or row['state'] == 'cancelled':
                return False
            if action == 'verify' and (row['state'] != 'pending' or row['expires'] < now_taipei().timestamp()):
                return False
            if confirm:
                if action == 'cancel':
                    db.execute("UPDATE alerts SET state='cancelled', email=?, verify_hash='', cancel_hash='' WHERE id=?",
                               (f"cancelled:{row['id']}", row['id']))
                    db.execute('DELETE FROM cancel_tokens WHERE alert_id=?', (row['id'],))
                else:
                    db.execute("UPDATE alerts SET state='active', verify_hash='' WHERE id=?", (row['id'],))
            return True

    def check(self, manager):
        if not self.enabled:
            return
        now = now_taipei()
        expected = now.date()
        while expected.weekday() >= 5:
            expected -= timedelta(days=1)
        with self.connection() as db:
            alerts = db.execute("SELECT * FROM alerts WHERE state='active'").fetchall()
        rates = {}
        for alert in alerts:
            pair = (alert['buy'], alert['sell'])
            try:
                if pair not in rates:
                    rates[pair] = manager.get_current_rate(*pair, force_refresh=True)
                quote = rates[pair]
                if not quote or quote.get('date') != expected.isoformat():
                    continue
                rate, target = positive_decimal(quote['rate']), Decimal(alert['target'])
                if not (rate >= target if alert['direction'] == 'gte' else rate <= target):
                    continue
                key = f"reminder:{alert['id']}:{now.date().isoformat()}"
                with self.connection() as db:
                    db.execute('BEGIN IMMEDIATE')
                    current = db.execute('SELECT state FROM alerts WHERE id=?', (alert['id'],)).fetchone()
                    if not current or current['state'] != 'active':
                        continue
                    if not self.reserve(db, key, alert['id'], alert['email'], 'reminder', now):
                        continue
                # 保留既有取消連結，讓舊信件仍可取消提醒。
                cancel = secrets.token_urlsafe(32)
                with self.connection() as db:
                    db.execute('INSERT INTO cancel_tokens VALUES(?,?)',
                               (digest(cancel), alert['id']))
                sign = '大於等於' if alert['direction'] == 'gte' else '小於等於'
                content = (f"1 {pair[0]} 可兌換 {rate} {pair[1]}，已達{sign} {target} 的條件。\n"
                           f"資料日期：{quote['date']}；來源：Mastercard，銀行手續費為 0。\n"
                           f"條件持續成立時，每日最多通知一次。\n"
                           f"取消提醒：{self.base_url}/alerts/cancel/{cancel}")
                self.deliver(key, alert['email'], '匯率已達設定門檻', content)
            except AlertError:
                logger.warning('提醒寄送已達每日額度，停止本次檢查')
                break
            except Exception:
                logger.exception('提醒檢查失敗 alert_id=%s', alert['id'])


@bp.route('/api/alerts', methods=['POST'])
def subscribe():
    if request.content_length is None or request.content_length > 4096:
        return jsonify(error='訂閱資料過大或缺少長度。'), 413
    try:
        current_app.alerts.subscribe(request.get_json(silent=True), request.remote_addr or 'unknown')
        return jsonify(message='申請已受理。新訂閱請至信箱確認；既有提醒不重複建立。'), 202
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except AlertError as error:
        return jsonify(error=str(error)), error.status


@bp.route('/alerts/<action>/<token>', methods=['GET', 'POST'])
def confirmation(action, token):
    valid = current_app.alerts.token_alert(action, token, confirm=request.method == 'POST')
    response = current_app.make_response((render_template(
        'alert_confirmation.html', action=action, valid=valid,
        done=request.method == 'POST'), 200 if valid else 400))
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response
