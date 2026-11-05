"""跨幣對市場特徵；每個歷史日期只讀當時可取得的報價。"""
import math
from datetime import timedelta

import numpy as np

FACTORS = ('USD-EUR', 'USD-JPY', 'USD-GBP', 'USD-AUD', 'USD-TWD')
FEATURES = tuple(f'{pair}_{kind}' for pair in FACTORS
                 for kind in ('return7', 'return30', 'correlation60'))


def features(market, pair, origin):
    days = [origin - timedelta(days=i) for i in range(60, -1, -1)]
    target = [market.rate(pair, day) for day in days]
    result = []
    for factor in FACTORS:
        values = [market.rate(factor, day) for day in days]
        result.extend(math.log(values[-1] / values[-1-h])
                      if values[-1] and values[-1-h] else float('nan') for h in (7, 30))
        paired = [(math.log(b/a), math.log(d/c))
                  for a, b, c, d in zip(target, target[1:], values, values[1:])
                  if all(v is not None and math.isfinite(v) and v > 0 for v in (a, b, c, d))]
        correlation = float('nan')
        if len(paired) >= 40:
            x, y = np.asarray(paired).T
            if np.std(x) > 0 and np.std(y) > 0:
                correlation = float(np.corrcoef(x, y)[0, 1])
        result.append(correlation)
    return result
