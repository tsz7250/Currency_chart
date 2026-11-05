"""逐幣對保存本機預測需要的 Mastercard 歷史。"""
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


class ForecastHistory:
    def __init__(self, path, legacy=None):
        self.path = Path(path)
        self.legacy = Path(legacy) if legacy else None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS quotes('
                       'pair TEXT, day TEXT, rate REAL, fx_date TEXT, fetched TEXT, source TEXT, '
                       'PRIMARY KEY(pair,day))')
            db.execute('CREATE TABLE IF NOT EXISTS attempts('
                       'pair TEXT, day TEXT, status TEXT, fetched TEXT, PRIMARY KEY(pair,day))')
            db.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT)')
            db.commit()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            yield db
        finally:
            db.close()

    def migrate_legacy(self):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM metadata WHERE key='legacy-imported'").fetchone():
                return
        rows = []
        if self.legacy and self.legacy.exists():
            source = sqlite3.connect(f'file:{self.legacy.resolve().as_posix()}?mode=ro', uri=True)
            try:
                rows = source.execute(
                    'SELECT pair,day,fetched,payload FROM quotes ORDER BY fetched,rowid').fetchall()
            finally:
                source.close()
        with self.connect() as db:
            for pair, day, fetched, payload in rows:
                value = json.loads(payload)
                self._save(db, pair, day, value['rate'], value.get('fx_date'),
                           fetched, value.get('source', 'Mastercard'))
            db.execute("INSERT OR REPLACE INTO metadata VALUES('legacy-imported',?)",
                       (datetime.now(timezone.utc).isoformat(),))
            db.commit()

    @staticmethod
    def _save(db, pair, day, rate, fx_date, fetched, source):
        date.fromisoformat(str(day))
        rate = float(rate)
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError('無效報價')
        db.execute('INSERT OR REPLACE INTO quotes VALUES(?,?,?,?,?,?)',
                   (pair, str(day), rate, fx_date, fetched, source))
        db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?)',
                   (pair, str(day), 'success', fetched))

    def seed(self, pair, dates, rates, source='existing-180'):
        if len(dates) != len(rates):
            raise ValueError('日期與報價數量不符')
        fetched = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            for day, rate in zip(dates, rates):
                self._save(db, pair, day, rate, None, fetched, source)
            db.commit()

    def record(self, pair, day, rate=None, fx_date=None, status='success'):
        fetched = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            if rate is not None:
                self._save(db, pair, day, rate, fx_date, fetched, 'Mastercard')
            else:
                db.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?,?)',
                           (pair, str(day), status, fetched))
            db.commit()

    def pending_backfill(self, pair, today):
        start = today - timedelta(days=364)
        stop = today - timedelta(days=180)
        with self.connect() as db:
            terminal = {row[0] for row in db.execute(
                "SELECT day FROM attempts WHERE pair=? AND status IN ('success','not_found')", (pair,))}
        return [start + timedelta(days=i) for i in range((stop - start).days + 1)
                if str(start + timedelta(days=i)) not in terminal]

    def history(self, pair, today):
        start = str(today - timedelta(days=364))
        with self.connect() as db:
            rows = db.execute(
                'SELECT day,rate FROM quotes WHERE pair=? AND day>=? AND day<=? ORDER BY day',
                (pair, start, str(today))).fetchall()
        return {'dates': [day for day, _ in rows], 'rates': [rate for _, rate in rows]}
