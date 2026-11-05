import math
import unittest
import numpy as np
from app.forecast_interval_shape import quantile,signed_score,constrain,residual_bounds


class ShapeTests(unittest.TestCase):
    def test_signed_scores_allow_shrinkage(self):
        value=signed_score(.9,1.1,1)
        self.assertLess(value,0)
        correction=quantile([value]*40,.2)
        self.assertGreater(.9*math.exp(-correction),.9)
        self.assertLess(1.1*math.exp(correction),1.1)
        with self.assertRaises(ValueError):quantile([0]*39,.2)

    def test_median_constraint_cost_is_visible(self):
        bounds,cost=constrain([[1.1,1.2],[.8,.9]],[1,1])
        np.testing.assert_equal(bounds,[[1,1.2],[.8,1]])
        self.assertEqual(cost['count'],2)
        self.assertAlmostEqual(cost['added_width'],.2)

    def test_crossings_and_positive_prices(self):
        class Model:
            def predict(self,x):return [[.2,-.1]]
        bounds,cross=residual_bounds(Model(),[dict(x=[1],median=2)])
        self.assertEqual(cross,1)
        self.assertTrue(0<bounds[0,0]<bounds[0,1])
        with self.assertRaises(ValueError):constrain([[float('nan'),1]],[1])


if __name__=='__main__':unittest.main()
