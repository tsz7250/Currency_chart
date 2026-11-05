import math
import unittest
from datetime import date, timedelta

import numpy as np

from app import forecast_cross_pair as cross
from app import forecast_supervised as s
from app.forecast_market import MarketHistory
from scripts.research_cross_pair import interval_predictions


class CrossPairTests(unittest.TestCase):
    def setUp(self):
        self.start = date(2025, 1, 1)
        self.origin = self.start + timedelta(days=80)
        rows = []
        for i in range(100):
            value = math.exp(.001*i + .005*math.sin(i))
            rows.append(dict(quote_date=str(self.start+timedelta(days=i)),
                available_date=str(self.start+timedelta(days=i+2)),
                values=dict(EUR=1, USD=value, JPY=100*value**2,
                            GBP=.8*value**.5, AUD=1.5*value**1.5, HKD=7.8*value)))
        self.snapshot = dict(sources=dict(ecb=dict(rows=rows), cbc=dict(rows=[
            dict(quote_date=r['quote_date'], available_date=r['available_date'], values=dict(TWD=30)) for r in rows])))

    def test_quote_orientation_returns_and_correlations(self):
        market = MarketHistory(self.snapshot)
        values = cross.features(market, 'EUR-USD', self.origin)
        self.assertEqual(len(values), 15)
        expected = math.log(market.rate('USD-EUR', self.origin)/market.rate('USD-EUR', self.origin-timedelta(days=7)))
        self.assertAlmostEqual(values[0], expected)
        self.assertAlmostEqual(values[2], -1)
        self.assertAlmostEqual(values[5], 1)

    def test_future_and_unpublished_quotes_do_not_change_features(self):
        before = cross.features(MarketHistory(self.snapshot), 'EUR-USD', self.origin)
        for source in self.snapshot['sources'].values():
            for r in source['rows']:
                if r['available_date'] > str(self.origin):
                    r['values'] = {k: v*100 for k, v in r['values'].items()}
        after = cross.features(MarketHistory(self.snapshot), 'EUR-USD', self.origin)
        np.testing.assert_equal(before, after)

    def test_missing_short_and_zero_volatility(self):
        class Empty:
            def rate(self, pair, day):
                return None
        class Constant:
            def rate(self, pair, day):
                return 1
        self.assertTrue(np.isnan(cross.features(Empty(), 'EUR-USD', self.origin)).all())
        values = cross.features(Constant(), 'EUR-USD', self.origin)
        np.testing.assert_equal(np.array(values).reshape(5, 3)[:, :2], 0)
        self.assertTrue(np.isnan(values[2::3]).all())
        early = cross.features(MarketHistory(self.snapshot), 'EUR-USD', self.start+timedelta(days=20))
        self.assertTrue(np.isnan(early[2::3]).all())

    def test_weekend_uses_available_quote_without_future_fill(self):
        friday = date(2025, 1, 3)
        snapshot = dict(sources=dict(ecb=dict(rows=[dict(quote_date=str(friday),
            available_date='2025-01-07', values=dict(EUR=1, USD=2))]), cbc=dict(rows=[])))
        market = MarketHistory(snapshot)
        self.assertIsNone(market.rate('USD-EUR', date(2025, 1, 5)))
        self.assertEqual(market.rate('USD-EUR', date(2025, 1, 7)), .5)

    def test_interval_crossings_center_and_audit(self):
        class Model:
            def predict(self, x):
                return [[.2, .1], [.1, -.1]]
        q, audit = interval_predictions(Model(), [dict(x=[0], median=2), dict(x=[1], median=3)])
        np.testing.assert_equal(q[:, 1], [2, 3])
        self.assertEqual(audit['crossings'], 2)
        self.assertEqual(audit['count'], 1)
        self.assertTrue((q > 0).all())
        self.assertTrue((np.diff(q, axis=1) >= 0).all())

    def test_classifier_missing_class_and_future_inference(self):
        rows = [dict(pair='EUR-USD', origin=self.start+timedelta(days=i), x=[i%3, i/20], label=i%2)
                for i in range(30)]
        model = s.train_classifier('cat4', rows)
        before = s.probabilities(model, rows[:1])
        s.probabilities(model, [dict(rows[0], x=[1000, -1000])])
        np.testing.assert_equal(before, s.probabilities(model, rows[:1]))
        np.testing.assert_allclose(s.probabilities(model, rows).sum(axis=1), 1)
        np.testing.assert_equal(s.probabilities(model, rows)[:, 2], 0)


if __name__ == '__main__':
    unittest.main()
