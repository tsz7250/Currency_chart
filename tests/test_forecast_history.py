import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest

from app.forecast_history import ForecastHistory


class ForecastHistoryTests(unittest.TestCase):
    def test_only_backfills_days_180_through_364(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            history = ForecastHistory(Path(tmp) / 'quotes.sqlite3')
            today = date(2026, 11, 5)
            recent = [today - timedelta(days=i) for i in range(180)]
            history.seed('TWD-HKD', [str(day) for day in recent], [1] * len(recent))
            pending = history.pending_backfill('TWD-HKD', today)
            self.assertEqual(len(pending), 185)
            self.assertEqual((pending[0], pending[-1]),
                             (today - timedelta(days=364), today - timedelta(days=180)))
            self.assertTrue(all(day < today - timedelta(days=179) for day in pending))

    def test_terminal_attempts_are_not_retried(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            history = ForecastHistory(Path(tmp) / 'quotes.sqlite3')
            today = date(2026, 11, 5)
            first = today - timedelta(days=364)
            history.record('EUR-USD', first, status='not_found')
            history.record('EUR-USD', first + timedelta(days=1), status='other')
            pending = history.pending_backfill('EUR-USD', today)
            self.assertNotIn(first, pending)
            self.assertIn(first + timedelta(days=1), pending)

    def test_imports_legacy_quotes_once(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            legacy = root / 'legacy.sqlite3'
            db = sqlite3.connect(legacy)
            try:
                db.execute('CREATE TABLE quotes(pair TEXT,day TEXT,fetched TEXT,digest TEXT,payload TEXT)')
                payload = json.dumps({'rate': 1.25, 'fx_date': '2026-01-01', 'source': 'Mastercard'})
                db.execute('INSERT INTO quotes VALUES(?,?,?,?,?)',
                           ('EUR-USD', '2026-01-01', '2026-01-02T00:00:00+00:00', 'x', payload))
                db.commit()
            finally:
                db.close()
            history = ForecastHistory(root / 'new.sqlite3', legacy)
            history.migrate_legacy()
            history.migrate_legacy()
            result = history.history('EUR-USD', date(2026, 1, 2))
            self.assertEqual(result, {'dates': ['2026-01-01'], 'rates': [1.25]})


if __name__ == '__main__':
    unittest.main()
