import math
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

from flask import Flask

from app.forecast import classification, correction, evaluate, prepare_entries, widen
from app.forecast_service import ForecastService
from app.routes import bp
from app.alerts import TAIPEI


def predict(inputs):
    return [[[next(x for x in reversed(values) if math.isfinite(x)) * scale
              for scale in (0.999, 1, 1.001)] for _ in range(30)] for values in inputs]


def entries():
    start = date(2026, 5, 9)
    return [(start + timedelta(days=i), 1 + i * .0003 + math.sin(i / 4) * .002)
            for i in range(180) if (start + timedelta(days=i)).weekday() < 5]


class ForecastTests(unittest.TestCase):
    def test_direction_scores_include_missing_classes(self):
        day = date(2026, 1, 1)
        rows = [{'origin': str(day), 'actual': a, 'median': p}
                for a, p in [(102, 102), (98, 100), (100, 100)]]
        result = classification(rows, {day: 100})
        self.assertEqual(result['confusion_matrix'], [[0, 1, 0], [0, 1, 0], [0, 0, 1]])
        self.assertAlmostEqual(result['accuracy'], 2 / 3)
        self.assertAlmostEqual(result['precision'], .5)
        self.assertAlmostEqual(result['recall'], 2 / 3)
        self.assertAlmostEqual(result['f1'], 5 / 9)
        self.assertIsNone(classification([], {day: 100})['accuracy'])

    def test_validation(self):
        for dates, rates in [(['2026-01-01'], [0]), (['2026-01-01'], [float('nan')]),
                             (['invalid'], [1]), (['2026-01-01'] * 2, [1, 2]), ([], [])]:
            with self.assertRaises(ValueError):
                prepare_entries(dates, rates)

    def test_calibration_uses_only_observed_targets(self):
        today = date(2026, 10, 1)
        scores = [{'target': today, 'score': i / 100} for i in range(20)]
        scores.append({'target': today + timedelta(days=1), 'score': 100})
        adjustment, n = correction(scores, today)
        self.assertEqual(n, 20)
        self.assertEqual(adjustment, .16)
        self.assertEqual(correction(scores[:19], today), (None, 19))
        lo, hi = widen(1, 2, adjustment)
        self.assertTrue(0 < lo < 1 < 2 < hi)

    def test_calendar_missing_values_and_no_future_leakage(self):
        source = entries()
        def checked(values):
            self.assertTrue(any(math.isnan(x) for row in values for x in row))
            return predict(values)
        original = evaluate(source, checked)
        cutoff = source[-25][0]
        changed = evaluate([(d, r if d <= cutoff else r * 1.2) for d, r in source], predict)
        for horizon in ('7', '14', '30'):
            for group in ('raw', 'calibrated'):
                a = {r['origin']: r for r in original['rows'][int(horizon)][group] if r['origin'] <= str(cutoff)}
                b = {r['origin']: r for r in changed['rows'][int(horizon)][group] if r['origin'] <= str(cutoff)}
                self.assertEqual(a.keys(), b.keys())
                for day in a:
                    for key in ('low', 'median', 'high'):
                        self.assertEqual(a[day][key], b[day][key])
        self.assertEqual(len(original['points']), 30)
        self.assertEqual(original['points'][-1]['date'], str(source[-1][0] + timedelta(days=30)))
        for point in original['points']:
            if point['low80'] is not None:
                self.assertTrue(0 < point['low80'] <= point['median'] <= point['high80'])

    def test_invalid_model_output(self):
        with self.assertRaises(ValueError):
            evaluate(entries(), lambda x: [[[2, 1, 3]] * 30 for _ in x])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.testing = True
        self.app.register_blueprint(bp)
        self.app.manager = Mock()
        self.app.forecasts = Mock()
        source = entries()
        self.app.manager.create_chart.return_value = {'dates': [str(d) for d, r in source], 'rates': [r for d, r in source]}
        self.client = self.app.test_client()
        self.clock = patch('app.routes.now_taipei', return_value=datetime(2026, 11, 4, tzinfo=TAIPEI))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_parameter_validation_and_pending(self):
        for url in ('?horizon=99', '?horizon=a', '?buy_currency=usd', '?buy_currency=ＡＢＣ'):
            self.assertEqual(self.client.get('/api/forecast' + url).status_code, 400)
        self.app.manager.create_chart.return_value = None
        self.assertEqual(self.client.get('/api/forecast').status_code, 202)
        self.app.forecasts.get.assert_not_called()

    def test_response_does_not_mutate_shared_forecast(self):
        report = evaluate(entries(), predict)
        report.pop('rows')
        report['status'] = 'ready'
        self.app.forecasts.get.return_value = report, 200
        response = self.client.get('/api/forecast?horizon=7').get_json()
        self.assertEqual(len(response['points']), 7)
        self.assertEqual(len(response['recent_history']), 7)
        self.assertEqual(response['recent_history'][-1]['date'], response['as_of'])
        self.assertTrue(any(p['rate'] is None for p in response['recent_history']))
        self.assertIn('classification', response['backtest'])
        self.assertEqual(len(report['points']), 30)
        self.assertIn('30', report['backtest'])

    def test_stale_data_is_not_forecast(self):
        self.app.manager.create_chart.return_value = {'dates': ['2020-01-01'], 'rates': [1]}
        self.assertEqual(self.client.get('/api/forecast').get_json()['status'], 'stale')
        self.app.forecasts.get.assert_not_called()

    def test_same_day_source_correction_bypasses_chart_cache(self):
        from threading import Lock
        self.app.manager.data_lock = Lock()
        self.app.manager.data = {str(d): {'rate': r} for d, r in entries()}
        self.app.forecasts.get.return_value = {'status': 'pending'}, 202
        self.client.get('/api/forecast')
        first = self.app.forecasts.get.call_args.args[1]
        self.app.manager.data[max(self.app.manager.data)]['rate'] = 2
        self.client.get('/api/forecast')
        second = self.app.forecasts.get.call_args.args[1]
        self.assertNotEqual(first, second)
        self.app.manager.create_chart.assert_not_called()

    def test_cache_fingerprint_and_native_worker(self):
        import gevent
        with tempfile.TemporaryDirectory() as path:
            service = ForecastService(path)
            service.predict = Mock(side_effect=predict)
            self.addCleanup(service.shutdown)
            source = entries()
            self.assertEqual(service.get(('USD', 'TWD'), source)[1], 202)
            self.assertEqual(service.get(('USD', 'TWD'), source)[1], 202)
            for _ in range(100):
                gevent.sleep(.01)
                result, status = service.get(('USD', 'TWD'), source)
                if status != 202:
                    break
            self.assertEqual(status, 200)
            self.assertEqual(service.predict.call_count, 1)
            self.assertEqual(service.get(('JPY', 'TWD'), source)[1], 202)
            self.assertEqual(service.get(('USD', 'TWD'), source[:-1] + [(source[-1][0], 2)])[1], 202)


if __name__ == '__main__':
    unittest.main()
