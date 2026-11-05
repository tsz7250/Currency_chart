"""將市場報酬分位數轉成保留 Bolt 中心的 Mastercard 區間。"""
import numpy as np

from .forecast_interval_shape import constrain


def transfer_interval(model, covariates, target_rows, medians):
    if len(covariates) != len(target_rows) or len(medians) != len(target_rows):
        raise ValueError('區間轉用樣本數不同')
    q = np.asarray(model.predict([r['x'] for r in covariates]), dtype=float)
    crossings = int((q[:, 0] > q[:, 1]).sum())
    proposed = np.exp(np.sort(q, axis=1))*np.array([r['base'] for r in target_rows])[:, None]
    b, cost = constrain(proposed, medians)
    return np.column_stack((b[:, 0], medians, b[:, 1])), dict(crossings=crossings, samples=len(target_rows), **cost)
