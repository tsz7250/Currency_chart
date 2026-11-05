"""公開市場方向模型共用的歷史特徵。"""
import math
from datetime import timedelta

from .forecast_supervised import volatility


def market_features(lookup, ages, origin, horizon):
    value = lookup.get(origin)
    if not value or any(not lookup.get(origin-timedelta(days=i)) for i in range(61)):
        return None
    return [math.log(value/lookup[origin-timedelta(days=d)]) for d in (1, 7, 14, 30)] + [
        volatility([lookup[origin-timedelta(days=i)] for i in range(d, -1, -1)]) for d in (20, 60)] + [
        ages[origin], origin.weekday(), horizon]
