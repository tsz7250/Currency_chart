"""在對數價格空間等權融合兩組分位數，並保留固定中心。"""
import numpy as np

from .forecast_interval_shape import constrain


def pool(first, second, center):
    first = np.asarray(first, dtype=float)
    second = np.asarray(second, dtype=float)
    center = np.asarray(center, dtype=float)
    if first.shape != second.shape or first.ndim != 2 or first.shape[1] != 3 or center.shape != (len(first),):
        raise ValueError('區間融合形狀無效')
    if not np.isfinite(first).all() or not np.isfinite(second).all() or not np.isfinite(center).all():
        raise ValueError('區間融合數值無效')
    if (first <= 0).any() or (second <= 0).any() or (center <= 0).any():
        raise ValueError('區間融合價格必須為正數')
    bounds = np.exp((np.log(first[:, [0, 2]]) + np.log(second[:, [0, 2]])) / 2)
    crossings = int((bounds[:, 0] > bounds[:, 1]).sum())
    bounds, audit = constrain(bounds, center)
    return np.column_stack((bounds[:, 0], center, bounds[:, 1])), dict(audit, pre_constraint_crossings=crossings)
