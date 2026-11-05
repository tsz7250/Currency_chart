import unittest
from datetime import date, timedelta

from app.forecast_market_features import market_features


class MarketFeatureTests(unittest.TestCase):
    def test_future_data_does_not_change_features(self):
        day = date(2026, 1, 1)
        lookup = {day-timedelta(days=i): 1+i*.001 for i in range(70)}
        ages = {d: 2 for d in lookup}
        expected = market_features(lookup, ages, day, 30)
        lookup[day+timedelta(days=1)] = 100
        self.assertEqual(expected, market_features(lookup, ages, day, 30))
        self.assertIsNone(market_features({}, ages, day, 30))


if __name__ == '__main__':
    unittest.main()
