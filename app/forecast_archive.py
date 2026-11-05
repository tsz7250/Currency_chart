"""發布端報價版本與真正發出的預測；歷史補抓不補造預測。"""
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from datetime import date,datetime,timezone
from contextlib import contextmanager


def encoded(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,default=str).encode()


class ForecastArchive:
    def __init__(self,path):
        self.path=str(path);Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS quotes(pair TEXT, day TEXT, fetched TEXT, digest TEXT, payload TEXT, PRIMARY KEY(pair,day,digest))')
            db.execute('CREATE TABLE IF NOT EXISTS issued(version TEXT PRIMARY KEY, issued_at TEXT, payload TEXT)')
            for table in ('quotes','issued'):
                for action in ('UPDATE','DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable'); END")

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30)
        try:
            with db:yield db
        finally:db.close()

    def add(self,pair,day,rate,fx_date=None,fetched=None,kind='live'):
        date.fromisoformat(str(day));rate=float(rate)
        if not math.isfinite(rate) or rate<=0:raise ValueError('無效報價')
        payload=dict(rate=rate,fx_date=fx_date,source='Mastercard',kind=kind)
        digest=hashlib.sha256(encoded(payload)).hexdigest()
        fetched=fetched or datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO quotes VALUES(?,?,?,?,?)',(pair,str(day),fetched,digest,encoded(payload).decode()))

    def history(self,pair,known_at=None):
        with self.connect() as db:
            rows=db.execute('SELECT day,fetched,payload FROM quotes WHERE pair=? ORDER BY fetched,rowid',(pair,)).fetchall()
        result={}
        for day,fetched,payload in rows:
            if known_at and datetime.fromisoformat(fetched)>datetime.fromisoformat(known_at):continue
            result[day]=dict(json.loads(payload),fetched_at=fetched)
        return result

    def issue(self,version,payload):
        issued=datetime.fromisoformat(payload['published_at'])
        if issued.tzinfo is None:raise ValueError('發布時間需要時區')
        with self.connect() as db:
            existing=db.execute('SELECT payload FROM issued WHERE version=?',(version,)).fetchone()
            value=encoded(payload).decode()
            if existing and existing[0]!=value:raise ValueError('不可覆寫發布紀錄')
            db.execute('INSERT OR IGNORE INTO issued VALUES(?,?,?)',(version,payload['published_at'],value))

    def records(self):
        with self.connect() as db:return [json.loads(r[0]) for r in db.execute('SELECT payload FROM issued ORDER BY issued_at')]
