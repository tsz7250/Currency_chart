import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.forecast_market import MarketHistory, next_source_day
from app.forecast_research import metrics, new_states, replay, replacement_checks
from app.forecast_shadow import ShadowStore, launch_shadow
from scripts.shadow_forecasts import assess, daily


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.start = date(2025, 1, 1)
        self.entries = [(self.start + timedelta(days=i), 100 + i * .1) for i in range(365)]
        self.origins = [d for d, _ in self.entries[89:]]
        self.forecasts = [[[v * .99, v, v * 1.01] for _ in range(30)] for _, v in self.entries[89:]]

    def test_availability_holidays_inverse_and_stale(self):
        friday = date(2026, 4, 3)
        tuesday = friday + timedelta(days=4)
        self.assertEqual(next_source_day(friday, [friday, tuesday]), tuesday + timedelta(days=1))
        market = MarketHistory({'sources': {name: {'rows': [{'quote_date': str(friday),
            'available_date': str(tuesday + timedelta(days=1)), 'values': values}]} for name, values in
            [('ecb', {'EUR': 1, 'USD': 1.2, 'HKD': 9.6}), ('cbc', {'TWD': 30})]}})
        self.assertIsNone(market.rate('TWD-HKD', tuesday))
        self.assertAlmostEqual(market.rate('TWD-HKD', tuesday + timedelta(days=1)), 9.6 / 36)
        self.assertAlmostEqual(market.rate('HKD-TWD', tuesday + timedelta(days=1)), 36 / 9.6)
        self.assertIsNone(market.rate('USD-TWD', friday + timedelta(days=11)))

    def test_future_changes_do_not_change_predictions(self):
        cutoff = self.entries[270][0]
        changed = [(d, r if d <= cutoff else r * 1.02) for d, r in self.entries]
        for center in ('raw', 'bias', 'blend', 'covariates'):
            for method in ('cumulative', 'asymmetric', 'adaptive'):
                before = replay(self.entries, self.origins, self.forecasts, center, method)
                after = replay(changed, self.origins, self.forecasts, center, method)
                self.assertEqual([r for r in before if r['origin'] <= cutoff], [r for r in after if r['origin'] <= cutoff])

    def test_bias_and_intervals_require_mature_samples(self):
        rows = replay(self.entries, self.origins, self.forecasts, 'bias', 'asymmetric')
        first = self.origins[0]
        early = next(r for r in rows if r['origin'] == first + timedelta(days=58) and r['horizon'] == 30)
        self.assertEqual(early['median'], early['raw_median'])
        corrected = next(r for r in rows if r['origin'] == first + timedelta(days=59) and r['horizon'] == 30)
        self.assertGreater(corrected['median'], corrected['raw_median'])
        self.assertIsNone(corrected['low'])
        first_interval = next(r for r in rows if r['horizon'] == 30 and r['low'] is not None)
        self.assertEqual(first_interval['origin'], first + timedelta(days=69))
        self.assertTrue(all(r['low'] is None or 0 < r['low'] <= r['median'] <= r['high'] for r in rows))

    def test_restart_preserves_adaptive_state(self):
        expected = replay(self.entries, self.origins, self.forecasts, 'bias', 'adaptive')
        state = new_states()
        first = replay(self.entries, self.origins[:150], self.forecasts[:150], 'bias', 'adaptive', state)
        restored = copy.deepcopy(state)
        second = replay(self.entries, self.origins[150:], self.forecasts[150:], 'bias', 'adaptive', restored)
        self.assertEqual(first + second, expected)

    def test_missing_intervals_do_not_count_as_zero_width_success(self):
        rows = replay(self.entries[:100], self.origins[:11], self.forecasts[:11], 'raw', 'asymmetric')
        result = metrics(rows, dict(self.entries[:100]))
        self.assertEqual(result['interval_samples'], 0)
        self.assertIsNone(result['coverage80'])
        self.assertEqual(replacement_checks({}, {}, {}), {'complete': False})


class ShadowTests(unittest.TestCase):
    def test_daily_worker_snapshot_restart_and_duplicate(self):
        from app.alerts import TAIPEI
        root = Path(__file__).resolve().parents[1]
        day = date(2026, 11, 5)
        data = {'TWD-HKD': {str(day - timedelta(days=i)): {'rate': 100 - i * .01} for i in range(190)}}
        def predict(inputs):
            return [[[values[-1] * scale for scale in (.99, 1, 1.01)] for _ in range(30)] for values in inputs]
        def predictions_stub(entries, *args):
            origins = [d for d, _ in entries[89:]]
            quantiles = [[[r * scale for scale in (.99, 1, 1.01)] for _ in range(30)] for _, r in entries[89:]]
            return origins, quantiles
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            folder = Path(directory)
            config = {'pairs': ['TWD-HKD'], 'center': 'blend', 'interval': 'adaptive', 'runtime_files': {}, 'packages': {}}
            for name, key in [('forecast_research.py','research_code_sha256'), ('forecast_market.py','market_code_sha256'), ('forecast.py','baseline_code_sha256')]:
                config[key] = hashlib.sha256((root/'app'/name).read_bytes()).hexdigest()
            store = ShadowStore(folder/'shadow.sqlite3')
            run = store.register(config, day)
            (folder/'active.json').write_text(json.dumps({'run':run,'research':str(folder)}))
            with patch('scripts.shadow_forecasts.refresh_history', return_value=data), \
                 patch('scripts.shadow_forecasts.download_market', return_value={'sources':{}}), \
                 patch('scripts.shadow_forecasts.Predictor', return_value=predict), \
                 patch('scripts.shadow_forecasts.predictions', side_effect=predictions_stub), \
                 patch('scripts.shadow_forecasts.now_taipei', return_value=datetime(2026,11,5,9,45,tzinfo=TAIPEI)):
                daily(folder)
                daily(folder)
            self.assertEqual(len(store.records(run)), 1)
            data['TWD-HKD']['2026-11-06'] = {'rate':100.1}
            with patch('scripts.shadow_forecasts.refresh_history', return_value=data), \
                 patch('scripts.shadow_forecasts.download_market', return_value={'sources':{}}), \
                 patch('scripts.shadow_forecasts.Predictor', return_value=predict), \
                 patch('scripts.shadow_forecasts.predictions', side_effect=predictions_stub), \
                 patch('scripts.shadow_forecasts.now_taipei', return_value=datetime(2026,11,6,9,45,tzinfo=TAIPEI)):
                daily(folder)
            records = store.records(run)
            self.assertEqual(len(records), 2)
            self.assertNotIn('2026-11-06', records[0][1]['mastercard_snapshot']['TWD-HKD'])
            self.assertEqual(len(records[1][1]['pairs']['TWD-HKD']['candidate']), 30)

    def test_immutable_restart_and_no_backdated_forecasts(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            path = Path(folder) / 'shadow.sqlite3'
            store = ShadowStore(path)
            day = date(2026, 11, 5)
            run = store.register({'pairs': ['TWD-HKD']}, day)
            points = [{'median': 1, 'low': .9, 'high': 1.1}] * 30
            payload = {'issued_at': '2026-11-05T09:45:00+08:00', 'pairs': {'TWD-HKD': {'candidate': points, 'baseline': points}}}
            self.assertTrue(store.append(run, day, payload))
            self.assertFalse(ShadowStore(path).append(run, day, dict(payload, test='changed')))
            self.assertNotIn('test', store.records(run)[0][1])
            with store.connect() as db, self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE forecasts SET payload=?', ('{}',))
            with self.assertRaises(ValueError):
                store.append(run, day + timedelta(days=1), payload)
            self.assertFalse(assess(store, run, {}, day)['eligible_for_production'])

    def test_launch_failure_does_not_escape_to_main_app(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            target = Path(folder) / 'forecast-shadow'
            target.mkdir()
            (target / 'active.json').write_text('{}')
            with patch('app.forecast_shadow.subprocess.Popen', side_effect=OSError('test failure')):
                self.assertFalse(launch_shadow(folder))


if __name__ == '__main__':
    unittest.main()
