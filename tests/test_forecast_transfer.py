import unittest

import numpy as np

from app.forecast_transfer import transfer_interval


class TransferTests(unittest.TestCase):
    def test_market_return_quantiles_transport_without_moving_bolt(self):
        class Model:
            def predict(self, x): return [[.1, -.1], [.1, .2]]
        target = [dict(base=10, actual=100), dict(base=20, actual=100)]
        q, audit = transfer_interval(Model(), [dict(x=[1]), dict(x=[2])], target, [50, 20])
        np.testing.assert_equal(q[:, 1], [50, 20])
        self.assertEqual(audit['crossings'], 1)
        self.assertEqual(audit['count'], 2)
        self.assertTrue((q > 0).all())
        self.assertTrue((np.diff(q, axis=1) >= 0).all())
        changed = [dict(r, actual=.0001) for r in target]
        q2, _ = transfer_interval(Model(), [dict(x=[1]), dict(x=[2])], changed, [50, 20])
        np.testing.assert_equal(q, q2)
        with self.assertRaises(ValueError):
            transfer_interval(Model(), [dict(x=[1])], target, [50, 20])


if __name__ == '__main__':
    unittest.main()
