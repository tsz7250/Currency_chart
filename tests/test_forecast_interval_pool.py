import unittest

import numpy as np

from app.forecast_interval_pool import pool


class IntervalPoolTests(unittest.TestCase):
    def test_log_pool_preserves_center_and_order(self):
        result, audit = pool([[8, 10, 12], [12, 14, 16]], [[9, 10, 15], [10, 14, 15]], [10, 14])
        np.testing.assert_allclose(result[:, 1], [10, 14])
        self.assertTrue((result[:, 0] <= result[:, 1]).all())
        self.assertTrue((result[:, 1] <= result[:, 2]).all())
        self.assertEqual(audit['pre_constraint_crossings'], 0)

    def test_center_constraint_and_invalid_prices_are_visible(self):
        result, audit = pool([[10, 10, 11]], [[12, 12, 13]], [9])
        self.assertEqual(result[0, 0], 9)
        self.assertGreater(audit['added_width'], 0)
        with self.assertRaises(ValueError):
            pool([[0, 1, 2]], [[1, 1, 2]], [1])


if __name__ == '__main__':
    unittest.main()
