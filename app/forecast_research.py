"""可重播的中心修正與延遲校準，離線研究與影子預測共用。"""
import math
import statistics
from datetime import date, timedelta

from .forecast import classification, measurement, score, summary, widen

CENTERS = ('raw', 'bias', 'blend', 'covariates')
INTERVALS = ('cumulative', 'asymmetric', 'adaptive')
HORIZONS = (7, 14, 30)
CONFIG = {'context_days': 90, 'bias_days': 60, 'bias_min': 30,
          'interval_days': 90, 'interval_min': 40, 'volatility_days': 30,
          'alpha': .2, 'aci_step': .01, 'aci_alpha_limits': [.02, .5], 'blend_weight': .5}


def volatility(lookup, origin, horizon):
    values = [lookup.get(origin - timedelta(days=i)) for i in range(30, -1, -1)]
    changes = [math.log(b / a) for a, b in zip(values, values[1:]) if a and b]
    return max(statistics.pstdev(changes) if len(changes) > 1 else 0, 1e-6) * math.sqrt(horizon)


def tail_quantile(values, probability, upper):
    values = sorted(values)
    rank = math.ceil((len(values) + 1) * probability) if upper else math.floor((len(values) + 1) * probability)
    return values[max(0, min(len(values) - 1, rank - 1))]


def new_states():
    return {h: {'known': [], 'pending': [], 'alpha': .2} for h in range(1, 31)}


def replay(entries, origins, forecasts, center, interval, states=None):
    """只在 target <= origin 時讓結果進入校準；回傳全部已預測點與未來點。"""
    lookup = dict(entries)
    states = new_states() if states is None else states
    output = []
    for origin, quantiles in zip(origins, forecasts):
        if len(quantiles) != 30:
            raise ValueError('預測必須包含 30 天')
        for h in range(1, 31):
            state = states[h]
            due = [r for r in state['pending'] if r['target'] <= origin]
            state['pending'] = [r for r in state['pending'] if r['target'] > origin]
            for row in due:
                actual = lookup.get(row['target'])
                if actual is None:
                    continue
                record = dict(row, actual=actual,
                    raw_error=math.log(actual / row['raw_median']),
                    residual=math.log(actual / row['median']) / row['sigma'],
                    score=score(row['raw_low'], row['raw_high'], actual))
                state['known'].append(record)
                if row['low'] is not None:
                    miss = not row['low'] <= actual <= row['high']
                    state['alpha'] = max(.02, min(.5, state['alpha'] + .01 * (.2 - miss)))
            lo, median, hi = map(float, quantiles[h - 1])
            if not all(math.isfinite(v) and v > 0 for v in (lo, median, hi)) or not lo <= median <= hi:
                raise ValueError('無效模型分位數')
            raw_median = median
            if center == 'bias':
                errors = [r['raw_error'] for r in state['known'] if r['target'] >= origin - timedelta(days=59)]
                shift = statistics.median(errors) if len(errors) >= 30 else 0
                lo, median, hi = (v * math.exp(shift) for v in (lo, median, hi))
            elif center == 'blend':
                lo, median, hi = (math.sqrt(v * lookup[origin]) for v in (lo, median, hi))
            elif center not in ('raw', 'covariates'):
                raise ValueError('未知中心設定')
            sigma = volatility(lookup, origin, h)
            low = high = None
            known = state['known']
            if interval == 'cumulative':
                scores = sorted(r['score'] for r in known)
                if len(scores) >= 20:
                    low, high = widen(lo, hi, scores[math.ceil((len(scores) + 1) * .8) - 1])
            elif interval in ('asymmetric', 'adaptive'):
                residuals = [r['residual'] for r in known if r['target'] >= origin - timedelta(days=89)]
                if len(residuals) >= 40:
                    alpha = state['alpha'] if interval == 'adaptive' else .2
                    lower = min(0, tail_quantile(residuals, alpha / 2, False))
                    upper = max(0, tail_quantile(residuals, 1 - alpha / 2, True))
                    low, high = median * math.exp(lower * sigma), median * math.exp(upper * sigma)
            else:
                raise ValueError('未知區間設定')
            row = {'origin': origin, 'target': origin + timedelta(days=h), 'horizon': h,
                   'median': median, 'low': low, 'high': high, 'sigma': sigma,
                   'raw_median': raw_median, 'raw_low': lo, 'raw_high': hi,
                   'alpha': state['alpha'], 'calibration_samples': len(known)}
            state['pending'].append(row)
            output.append(row)
    return output


def metrics(rows, lookup):
    values = []
    for r in rows:
        actual = lookup.get(r['target'])
        if actual is None:
            continue
        lo = r['low'] if r['low'] is not None else r['median']
        hi = r['high'] if r['high'] is not None else r['median']
        v = measurement(r['origin'], r['target'], actual, lo, r['median'], hi)
        v.update(interval_available=r['low'] is not None, sigma=r['sigma'],
                 signed_log_error=math.log(r['median'] / actual),
                 lower_miss=actual < lo, upper_miss=actual > hi)
        values.append(v)
    result = summary(values)
    if not values:
        return result
    mean = statistics.mean(r['actual'] for r in values)
    result.update(normalized_mae=result['mae'] / mean,
                  mean_log_bias=statistics.mean(r['signed_log_error'] for r in values),
                  classification=classification(values, lookup))
    calibrated = [r for r in values if r['interval_available']]
    interval = summary(calibrated)
    result.update(interval_samples=len(calibrated), coverage80=interval['coverage80'],
                  normalized_width=interval['mean_width'] / mean if calibrated else None,
                  normalized_interval_score=interval['interval_score80'] / mean if calibrated else None,
                  lower_miss_rate=statistics.mean(r['lower_miss'] for r in calibrated) if calibrated else None,
                  upper_miss_rate=statistics.mean(r['upper_miss'] for r in calibrated) if calibrated else None)
    pivot = statistics.median(v['sigma'] for v in values)
    result['volatility_regimes'] = {name: {'samples': len(group),
        'normalized_mae': statistics.mean(v['error'] for v in group) / statistics.mean(v['actual'] for v in group) if group else None}
        for name, group in [('low', [v for v in values if v['sigma'] <= pivot]),
                            ('high', [v for v in values if v['sigma'] > pivot])]}
    return result


def block_uncertainty(rows, lookup, seed=0, repetitions=200):
    """30 日日期區塊重抽樣；這是單格估計，不能當成獨立日樣本區間。"""
    import random
    rng = random.Random(seed)
    rows = sorted([r for r in rows if r['target'] in lookup], key=lambda r: r['origin'])
    if len(rows) < 60:
        return {'status': 'insufficient', 'block_days': 30}
    start, end = rows[0]['origin'], rows[-1]['origin']
    blocks = [[r for r in rows if d <= r['origin'] < d + timedelta(days=30)]
              for d in (start + timedelta(days=i) for i in range((end - start).days - 28))]
    estimates = {'normalized_mae': [], 'coverage80': [], 'macro_f1': []}
    for _ in range(repetitions):
        sample = []
        while len(sample) < len(rows):
            sample.extend(rng.choice(blocks))
        m = metrics(sample[:len(rows)], lookup)
        estimates['normalized_mae'].append(m['normalized_mae'])
        estimates['macro_f1'].append(m['classification']['f1'])
        if m['coverage80'] is not None:
            estimates['coverage80'].append(m['coverage80'])
    return {'status': 'estimated', 'block_days': 30, 'repetitions': repetitions,
            'intervals95': {k: [tail_quantile(v, .025, False), tail_quantile(v, .975, True)]
                            for k, v in estimates.items() if v}}


def replacement_checks(candidate, baseline, flat):
    """鍵為 pair/horizon；缺格、樣本不足或缺區間時不允許替換。"""
    required = {f'{p}/{h}' for p in ('TWD-HKD', 'USD-TWD', 'JPY-TWD', 'EUR-USD', 'AUD-USD', 'GBP-JPY') for h in HORIZONS}
    if any(set(x) != required for x in (candidate, baseline, flat)):
        return {'complete': False}
    if any(v.get('interval_samples', 0) < 60 for x in (candidate, baseline) for v in x.values()):
        return {'complete': False}
    core = [k for k in required if not k.startswith(('AUD-', 'GBP-'))]
    extra = list(required - set(core))
    avg = lambda group, keys, field: statistics.mean(group[k][field] for k in keys)
    return {'complete': True,
        'price_5_percent': avg(candidate, core, 'normalized_mae') <= .95 * avg(baseline, core, 'normalized_mae'),
        'better_than_flat': avg(candidate, core, 'normalized_mae') < avg(flat, core, 'normalized_mae'),
        'no_cell_10_percent_worse': all(candidate[k]['normalized_mae'] <= baseline[k]['normalized_mae'] * 1.1 for k in core),
        'extra_pairs_no_worse': avg(candidate, extra, 'normalized_mae') <= avg(baseline, extra, 'normalized_mae'),
        'f1_no_worse': statistics.mean(v['classification']['f1'] for v in candidate.values()) >= statistics.mean(v['classification']['f1'] for v in baseline.values()),
        'coverage_target': .75 <= avg(candidate, required, 'coverage80') <= .85,
        'coverage_floor': all(v['coverage80'] >= .7 for v in candidate.values()),
        'interval_score_10_percent': avg(candidate, required, 'normalized_interval_score') <= .9 * avg(baseline, required, 'normalized_interval_score'),
        'width_limit': avg(candidate, required, 'normalized_width') <= 1.2 * avg(baseline, required, 'normalized_width')}
