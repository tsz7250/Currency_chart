"""Chronos 推論、按日曆步長校準，以及不讀取未來結果的逐期評測。"""
import math
from datetime import date, timedelta

MODEL_ID = 'amazon/chronos-bolt-small'
MODEL_REVISION = '772f3d25d38aec6d914c8949dab4462e2d46f5d8'
MIN_CONTEXT = 40
MIN_CALIBRATION = 20
WINDOW_DAYS = 90
HORIZON = 30
BLEND_HORIZONS = (7, 14, 30)
# Bolt 10/90 分位數的標準常態間距。
QUANTILE_Z = 1.2815515655446004


def prepare_entries(dates, rates):
    if len(dates) != len(rates) or len(set(dates)) != len(dates):
        raise ValueError('日期與報價不一致或日期重複')
    entries = sorted((date.fromisoformat(d), float(r)) for d, r in zip(dates, rates))
    if not entries or any(not math.isfinite(r) or r <= 0 for _, r in entries):
        raise ValueError('報價必須為有限正數')
    return entries


def context(entries):
    end = entries[-1][0]
    entries = [(d, r) for d, r in entries if d >= end - timedelta(days=WINDOW_DAYS)]
    if len(entries) < MIN_CONTEXT:
        return None
    start = entries[0][0]
    lookup = dict(entries)
    return [lookup.get(start + timedelta(days=i), float('nan'))
            for i in range((end - start).days + 1)]


def score(low, high, actual):
    return max(math.log(low / actual), math.log(actual / high), 0.0)


def correction(records, as_of):
    known = sorted(r['score'] for r in records if r['target'] <= as_of)
    if len(known) < MIN_CALIBRATION:
        return None, len(known)
    rank = math.ceil((len(known) + 1) * 0.8)
    return known[rank - 1], len(known)


def widen(low, high, adjustment):
    return low * math.exp(-adjustment), high * math.exp(adjustment)


def band_sigma(low, high):
    """校準帶隱含的期末標準差（log 尺度）。"""
    return math.log(high / low) / (2 * QUANTILE_Z)


def tilt_factor(p_up, p_down, low, high):
    """融合方向機率轉成中位數路徑傾斜倍率：d·σ_H，最大 ±1 個標準差。"""
    return math.exp((p_up - p_down) * band_sigma(low, high))


def summary(rows):
    if not rows:
        return {'samples': 0, 'mae': None, 'coverage80': None,
                'mean_width': None, 'interval_score80': None}
    n = len(rows)
    return {'samples': n, 'mae': sum(r['error'] for r in rows) / n,
            'coverage80': sum(r['covered'] for r in rows) / n,
            'mean_width': sum(r['width'] for r in rows) / n,
            'interval_score80': sum(r['interval_score'] for r in rows) / n}


def measurement(origin, target, actual, low, median, high):
    return {'origin': origin.isoformat(), 'target': target.isoformat(),
            'actual': actual, 'low': low, 'median': median, 'high': high,
            'error': abs(median - actual), 'covered': low <= actual <= high,
            'width': high - low,
            'interval_score': high - low + 10 * max(low - actual, actual - high, 0)}


def classification(rows, lookup):
    labels = ('down', 'flat', 'up')
    matrix = [[0] * 3 for _ in labels]
    def category(value, base):
        change = (value - base) / base
        return 2 if change > .001 else 0 if change < -.001 else 1
    for row in rows:
        base = lookup[date.fromisoformat(row['origin'])]
        matrix[category(row['actual'], base)][category(row['median'], base)] += 1
    scores = {}
    for i, label in enumerate(labels):
        support = sum(matrix[i])
        predicted = sum(row[i] for row in matrix)
        precision = matrix[i][i] / predicted if predicted else 0
        recall = matrix[i][i] / support if support else 0
        scores[label] = {'precision': precision, 'recall': recall,
                         'f1': 2 * precision * recall / (precision + recall) if precision + recall else 0,
                         'support': support, 'predicted': predicted}
    return {'samples': len(rows), 'threshold': .001, 'labels': labels, 'confusion_matrix': matrix,
            'accuracy': sum(matrix[i][i] for i in range(3)) / len(rows) if rows else None,
            **{key: sum(value[key] for value in scores.values()) / 3 if rows else None
               for key in ('precision', 'recall', 'f1')}, 'per_class': scores}


def evaluate(entries, predict, directions=None):
    """predict 接受多筆日曆數列，回傳 [批次, 30, 3]，便於隔離模型測試。
    directions(origin, base, horizon) 選用：回傳 (跌, 平, 漲) 機率時，
    7／14／30 天滾動回測同步評分傾斜中心與平移帶；blend 序列自校準生效（約第 20 個起點）開始。"""
    latest = entries[-1][0]
    entries = [(d, r) for d, r in entries if d >= latest - timedelta(days=364)]
    lookup = dict(entries)
    origins, inputs = [], []
    for i, (day, _) in enumerate(entries):
        values = context(entries[:i + 1])
        if values is not None:
            origins.append((day, entries[:i + 1]))
            inputs.append(values)
    if not inputs:
        raise ValueError('至少需要 40 筆最近 90 天內的有效報價')
    forecasts = predict(inputs)
    if len(forecasts) != len(origins):
        raise ValueError('模型輸出筆數不符')
    records = {h: [] for h in range(1, HORIZON + 1)}
    groups = ('raw', 'calibrated', 'raw_matched', 'flat', 'drift') + (('blend',) if directions is not None else ())
    rows = {h: {m: [] for m in groups} for h in range(1, HORIZON + 1)}
    latest_directions = {}
    points = []
    for (day, train), forecast in zip(origins, forecasts):
        if len(forecast) != HORIZON:
            raise ValueError('模型預測期間不符')
        recent = [(d, r) for d, r in train if d >= day - timedelta(days=WINDOW_DAYS)]
        changes = [((b[0] - a[0]).days, math.log(b[1] / a[1]))
                   for a, b in zip(recent, recent[1:])]
        span = sum(t for t, _ in changes)
        drift = sum(r for _, r in changes) / span
        for h, quantiles in enumerate(forecast, 1):
            low, median, high = map(float, quantiles)
            if not all(math.isfinite(x) and x > 0 for x in (low, median, high)) or not low <= median <= high:
                raise ValueError('模型回傳無效的預測區間')
            target = day + timedelta(days=h)
            adjustment, count = correction(records[h], day)
            bounds = widen(low, high, adjustment) if adjustment is not None else (None, None)
            blend_probs = None
            if directions is not None and h in BLEND_HORIZONS:
                probs = directions(day, train[-1][1], h)
                if (probs is not None and len(probs) == 3
                        and all(math.isfinite(v) and 0 <= v <= 1 for v in probs)
                        and math.isclose(sum(probs), 1, abs_tol=1e-6)):
                    blend_probs = probs
                    if day == latest:
                        latest_directions[h] = [float(v) for v in probs]
            if day == latest:
                points.append({'date': target.isoformat(), 'median': median,
                               'low80': bounds[0], 'high80': bounds[1],
                               'calibration_samples': count})
            actual = lookup.get(target)
            if actual is None:
                continue
            rows[h]['raw'].append(measurement(day, target, actual, low, median, high))
            if adjustment is not None:
                rows[h]['raw_matched'].append(measurement(day, target, actual, low, median, high))
                rows[h]['calibrated'].append(measurement(day, target, actual, bounds[0], median, bounds[1]))
            for name, mu in (('flat', 0), ('drift', drift)):
                variance = sum((r - mu * t) ** 2 / t for t, r in changes) / (len(changes) - (name == 'drift'))
                sd = math.sqrt(variance * (h + (h * h / span if name == 'drift' else 0)))
                center = math.log(train[-1][1]) + mu * h
                rows[h][name].append(measurement(day, target, actual,
                    math.exp(center - QUANTILE_Z * sd), math.exp(center), math.exp(center + QUANTILE_Z * sd)))
            records[h].append({'target': target, 'score': score(low, high, actual)})
            if blend_probs is not None and bounds[0] is not None:
                # 只用校準帶計債，與前端 bandSigma 同源；校準生效前（前 20 個起點）不計入 blend。
                factor = tilt_factor(blend_probs[2], blend_probs[0], bounds[0], bounds[1])
                rows[h]['blend'].append(measurement(day, target, actual,
                    bounds[0] * factor, median * factor, bounds[1] * factor))
    backtest = {}
    for h, values in rows.items():
        entry = {**{m: summary(v[-90:]) for m, v in values.items() if m != 'blend'},
                 'classification': classification(values['raw'][-90:], lookup)}
        if 'blend' in values and values['blend']:
            entry['blend'] = {**summary(values['blend'][-90:]),
                              'classification': classification(values['blend'][-90:], lookup)}
        backtest[str(h)] = entry
    result = {'model': MODEL_ID, 'model_revision': MODEL_REVISION,
              'as_of': latest.isoformat(), 'training_start': entries[0][0].isoformat(),
              'context_days': WINDOW_DAYS, 'points': points, 'backtest': backtest, 'rows': rows}
    if latest_directions:
        result['directions'] = {str(h): latest_directions[h] for h in sorted(latest_directions)}
    return result


class ChronosPredictor:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self.pipeline = None

    def __call__(self, inputs):
        import os
        os.environ.setdefault('USE_TF', '0')
        os.environ.setdefault('USE_FLAX', '0')
        os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
        import torch
        from chronos import ChronosBoltPipeline
        if self.pipeline is None:
            torch.set_num_threads(4)
            self.pipeline = ChronosBoltPipeline.from_pretrained(
                MODEL_ID, revision=MODEL_REVISION, device_map='cpu',
                torch_dtype=torch.float32, cache_dir=str(self.cache_dir))
        output = []
        for start in range(0, len(inputs), 16):
            batch = [torch.tensor(x, dtype=torch.float32) for x in inputs[start:start + 16]]
            quantiles, _ = self.pipeline.predict_quantiles(
                batch, prediction_length=HORIZON, quantile_levels=[0.1, 0.5, 0.9])
            output.extend(quantiles.tolist())
        return output
