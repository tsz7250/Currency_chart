import copy
import unittest
from datetime import date, timedelta

from app.forecast_market import MarketHistory, next_source_day
from app.forecast_research import metrics, new_states, replay, replacement_checks


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


if __name__ == '__main__':
    unittest.main()
