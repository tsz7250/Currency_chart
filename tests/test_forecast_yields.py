import copy
import math
import unittest
from datetime import date, timedelta

import numpy as np

from app.forecast_yields import YieldHistory, parse_csv, regime_features


class YieldTests(unittest.TestCase):
    def snapshot(self):
        first = date(2025, 1, 1)
        return dict(rows=[dict(quote_date=str(first+timedelta(days=i)),
            available_date=str(first+timedelta(days=i+2)), values=[.01, .02+i/10000, .04]) for i in range(160)])

    def test_csv_percent_units_zero_negative_and_missing(self):
        first = date(2025, 1, 1)
        lines = ['Date,3 Mo,2 Yr,10 Yr']+[f'{(first+timedelta(days=i)):%m/%d/%Y},0,-0.1,2' for i in range(160)]
        lines.append('07/01/2025,N/A,1,2')
        rows = parse_csv('\n'.join(lines).encode())
        self.assertEqual(len(rows), 160)
        self.assertEqual(rows[first], [0, -.001, .02])
        with self.assertRaises(ValueError):
            parse_csv('\n'.join(lines+[lines[1]]).encode())
        with self.assertRaises(ValueError):
            parse_csv('\n'.join(lines[:10]).encode())

    def test_known_data_age_and_difference_units(self):
        history = YieldHistory(self.snapshot())
        origin = date(2025, 4, 1)
        x = history.features(origin)
        self.assertAlmostEqual(x[3], .0007)
        self.assertAlmostEqual(x[4], .003)
        self.assertEqual(x[5], 2)
        self.assertIsNone(history.known(date(2025, 1, 1)))
        self.assertIsNone(history.known(date(2026, 1, 1)))
        self.assertIsNone(history.features(date(2025, 1, 10)))

    def test_future_and_not_yet_available_values_cannot_change_features(self):
        snapshot = self.snapshot()
        origin = date(2025, 4, 1)
        before = YieldHistory(snapshot).features(origin)
        changed = copy.deepcopy(snapshot)
        for row in changed['rows']:
            if row['available_date'] > str(origin):
                row['values'] = [100, -100, 0]
        self.assertEqual(before, YieldHistory(changed).features(origin))

    def test_past_quote_not_backfilled_from_origin_quote(self):
        snapshot = self.snapshot()
        origin = date(2025, 4, 1)
        history = YieldHistory(snapshot)
        row = history.known(origin)
        previous = history.known(origin-timedelta(days=7))
        self.assertNotEqual(row['quote_date'], previous['quote_date'])
        self.assertAlmostEqual(history.features(origin)[3], row['values'][1]-previous['values'][1])

    def test_regime_orientation_and_zero_variance(self):
        origin = date(2025, 4, 1)
        class Market:
            def rate(self, pair, day):
                i=(day-date(2025, 1, 1)).days
                v=math.exp(.01*math.sin(i/4)+i*.001)
                return 1/v if pair=='EUR-USD' else v
        y=YieldHistory(self.snapshot()).features(origin)
        x=regime_features(Market(), 'EUR-USD', origin, y)
        self.assertAlmostEqual(x[1], -1)
        self.assertAlmostEqual(x[2], -y[3])
        self.assertTrue(np.isfinite(x).all())
        class Constant:
            def rate(self, pair, day):return 1
        x=regime_features(Constant(), 'EUR-USD', origin, y)
        self.assertTrue(math.isnan(x[1]))
        self.assertEqual(x[0], 0)


if __name__ == '__main__':
    unittest.main()
