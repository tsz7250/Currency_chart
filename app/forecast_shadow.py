"""影子驗證的不可覆寫紀錄與獨立程序啟動，不介入主站 API。"""
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)
_process = None


def fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


class ShadowStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, config TEXT NOT NULL, start TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS forecasts (run TEXT NOT NULL, origin TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run, origin))')
            db.execute('CREATE TRIGGER IF NOT EXISTS immutable_forecasts_update BEFORE UPDATE ON forecasts BEGIN SELECT RAISE(ABORT, "immutable forecast"); END')
            db.execute('CREATE TRIGGER IF NOT EXISTS immutable_forecasts_delete BEFORE DELETE ON forecasts BEGIN SELECT RAISE(ABORT, "immutable forecast"); END')
            db.execute('CREATE TRIGGER IF NOT EXISTS immutable_runs_update BEFORE UPDATE ON runs BEGIN SELECT RAISE(ABORT, "immutable run"); END')
            db.execute('CREATE TRIGGER IF NOT EXISTS immutable_runs_delete BEFORE DELETE ON runs BEGIN SELECT RAISE(ABORT, "immutable run"); END')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, config, start):
        run = fingerprint(config)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO runs VALUES (?, ?, ?)', (run, json.dumps(config), str(start)))
        return run

    def info(self, run):
        with self.connect() as db:
            row = db.execute('SELECT config,start FROM runs WHERE id=?', (run,)).fetchone()
        if not row:
            raise ValueError('影子設定不存在')
        return json.loads(row[0]), date.fromisoformat(row[1])

    def contains(self, run, origin):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM forecasts WHERE run=? AND origin=?', (run, str(origin))).fetchone() is not None

    def append(self, run, origin, payload):
        config, start = self.info(run)
        if not start <= origin < start + timedelta(days=90):
            raise ValueError('超出鎖定的 90 天起點期間')
        issued = datetime.fromisoformat(payload['issued_at'])
        from .alerts import TAIPEI
        if issued.tzinfo is None or issued.astimezone(TAIPEI).date() != origin:
            raise ValueError('不得補造過去日期的影子預測')
        if set(payload['pairs']) != set(config['pairs']):
            raise ValueError('影子幣對不完整')
        for pair in payload['pairs'].values():
            for name in ('candidate', 'baseline'):
                if len(pair[name]) != 30:
                    raise ValueError('影子預測必須包含 30 天')
                for point in pair[name]:
                    median, lo, hi = point['median'], point['low'], point['high']
                    if not math.isfinite(median) or median <= 0:
                        raise ValueError('無效影子價格')
                    if (lo is None) != (hi is None) or (lo is not None and
                        (not all(math.isfinite(x) for x in (lo, hi)) or not 0 < lo <= median <= hi)):
                        raise ValueError('無效影子區間')
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str)
        with self.connect() as db:
            cursor = db.execute('INSERT OR IGNORE INTO forecasts VALUES (?, ?, ?)', (run, str(origin), encoded))
            return cursor.rowcount == 1

    def records(self, run):
        with self.connect() as db:
            rows = db.execute('SELECT origin,payload FROM forecasts WHERE run=? ORDER BY origin', (run,)).fetchall()
        return [(date.fromisoformat(d), json.loads(p)) for d, p in rows]


def launch_shadow(instance_path):
    global _process
    try:
        folder = Path(instance_path) / 'forecast-shadow'
        if not (folder / 'active.json').exists() or (_process is not None and _process.poll() is None):
            return False
        root = Path(__file__).resolve().parents[1]
        with (folder / 'worker.log').open('a', encoding='utf-8') as stream:
            _process = subprocess.Popen([sys.executable, '-u', '-X', 'utf8', str(root / 'scripts/shadow_forecasts.py'), '--folder', str(folder)],
                cwd=root, stdout=stream, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        return True
    except Exception:
        logger.exception('影子驗證啟動失敗；主站維持原服務')
        return False
