"""獨立方向與分位數研究；所有特徵、切分及校準只讀當時資訊。"""
import math
from collections import Counter
from datetime import date, timedelta

import numpy as np

from .forecast import classification, score
from .forecast_research import metrics, replacement_checks

PAIRS = ('TWD-HKD', 'USD-TWD', 'JPY-TWD', 'EUR-USD', 'AUD-USD', 'GBP-JPY')
HORIZONS = (7, 14, 30)
LABELS = ('down', 'flat', 'up')
CLASSIFIERS = ('flat', 'majority', 'logistic', 'cat4', 'cat4-balanced', 'cat6', 'cat6-balanced')
PARAMETERS = dict(iterations=300, learning_rate=.03, l2_leaf_reg=3,
                  random_seed=0, thread_count=4, task_type='CPU', verbose=False,
                  allow_writing_files=False)
FEATURES = tuple(f'{source}_{kind}' for source in ('mastercard', 'market')
                 for kind in ('return1', 'return7', 'return14', 'return30', 'vol20', 'vol60')) + (
    'spread', 'weekday', 'quote_age', 'repeat_ratio', 'market_vol_percentile', 'horizon')


def category(change):
    return 2 if change > .001 else 0 if change < -.001 else 1


def volatility(values):
    changes = [math.log(b / a) for a, b in zip(values, values[1:]) if a and b]
    return float(np.std(changes)) if len(changes) >= len(values) // 2 else float('nan')


def features(history, market, pair, origin):
    known = {date.fromisoformat(d): v for d, v in history.items() if d <= str(origin)}
    if origin not in known:
        raise ValueError('起點缺少 Mastercard 報價')
    rates = {d: float(v['rate']) for d, v in known.items()}
    if any(not math.isfinite(v) or v <= 0 for v in rates.values()):
        raise ValueError('報價必須為有限正數')
    # 每個日期都依當日公布時間取值，不能用起點時已知資料回填過去。
    market_rates = {origin - timedelta(days=i): market.rate(pair, origin - timedelta(days=i))
                    for i in range(426)}
    result = []
    for source in (rates, market_rates):
        latest = source.get(origin)
        result.extend(math.log(latest / source[origin - timedelta(days=h)])
                      if latest and source.get(origin - timedelta(days=h)) else float('nan')
                      for h in (1, 7, 14, 30))
        result.extend(volatility([source.get(origin - timedelta(days=i)) for i in range(h, -1, -1)])
                      for h in (20, 60))
    current_market = market_rates[origin]
    fx_date = known[origin].get('fx_date') or known[origin].get('fxDate')
    quote_age = (origin - date.fromisoformat(fx_date[:10])).days if fx_date else float('nan')
    recent = [rates.get(origin - timedelta(days=i)) for i in range(30, -1, -1)]
    adjacent = [(a, b) for a, b in zip(recent, recent[1:]) if a and b]
    past_vol = [volatility([market_rates.get(origin - timedelta(days=j+i)) for i in range(20, -1, -1)])
                for j in range(1, 366)]
    past_vol = [v for v in past_vol if math.isfinite(v)]
    percentile = sum(v <= result[10] for v in past_vol) / len(past_vol) if len(past_vol) >= 200 else float('nan')
    result.extend((math.log(rates[origin] / current_market) if current_market else float('nan'),
                   origin.weekday(), quote_age,
                   sum(a == b for a, b in adjacent) / len(adjacent) if adjacent else float('nan'), percentile))
    return result


def dataset(histories, market):
    rows = []
    for pair, history in histories.items():
        start = date.fromisoformat(min(history)) + timedelta(days=60)
        for ds in sorted(history):
            origin = date.fromisoformat(ds)
            if origin < start:
                continue
            x = features(history, market, pair, origin)
            base = float(history[ds]['rate'])
            for h in range(1, 31):
                target = origin + timedelta(days=h)
                if str(target) in history:
                    actual = float(history[str(target)]['rate'])
                    rows.append(dict(pair=pair, origin=origin, target=target, horizon=h,
                                     x=x + [h], base=base, actual=actual,
                                     y=math.log(actual / base), label=category((actual-base)/base)))
    return rows


def splits(start):
    return [dict(train=start + timedelta(days=60), tune=start + timedelta(days=t),
                 calibration=start + timedelta(days=t+40), test=start + timedelta(days=t+100),
                 end=start + timedelta(days=t+130)) for t in (140, 170, 200)]


def partition(rows, split, stage):
    next_stage = dict(train='tune', tune='calibration', calibration='test', test='end')[stage]
    return [r for r in rows if split[stage] <= r['origin'] < split[next_stage]
            and (stage == 'test' or r['target'] < split[next_stage])
            and (stage == 'test' or r['pair'] in PAIRS[:4])]


def weights(rows):
    counts = Counter((r['pair'], r['origin']) for r in rows)
    origins = Counter(p for p, _ in counts)
    w = np.array([1 / counts[r['pair'], r['origin']] / origins[r['pair']] for r in rows])
    return w / w.mean()


def train_classifier(name, rows):
    from catboost import CatBoostClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    y = np.array([r['label'] for r in rows])
    if name == 'flat':
        return 1
    if name == 'majority' or len(set(y)) == 1:
        return int(np.bincount(y, weights=weights(rows), minlength=3).argmax())
    x = np.array([r['x'] for r in rows])
    if name == 'logistic':
        model = make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True, add_indicator=True),
                              StandardScaler(), LogisticRegression(C=1, max_iter=2000, random_state=0))
        model.fit(x, y, logisticregression__sample_weight=weights(rows))
    else:
        model = CatBoostClassifier(**PARAMETERS, depth=int(name[3]), loss_function='MultiClass',
                                   classes_count=3, auto_class_weights='Balanced' if 'balanced' in name else 'None')
        model.fit(x, y, sample_weight=weights(rows))
    return model


def probabilities(model, rows):
    p = np.zeros((len(rows), 3))
    if isinstance(model, int):
        p[:, model] = 1
    else:
        p[:, np.asarray(model.classes_, dtype=int)] = model.predict_proba([r['x'] for r in rows])
    if not np.isfinite(p).all() or (p < 0).any() or not np.allclose(p.sum(axis=1), 1):
        raise ValueError('分類機率無效')
    return p


def direction_metrics(rows, p):
    # 沿用現有固定三類分數；以人工價格編碼獨立方向，不改寫價格模型輸出。
    values = [dict(origin=str(r['origin']), actual=(.99, 1, 1.01)[r['label']],
                   median=(.99, 1, 1.01)[int(np.argmax(v))]) for r, v in zip(rows, p)]
    result = classification(values, {r['origin']: 1 for r in rows})
    result['log_loss'] = float(np.mean([-math.log(max(float(v[r['label']]), 1e-15)) for r, v in zip(rows, p)])) if rows else None
    return result


def direction_report(rows, p):
    cells = {}
    for pair in PAIRS:
        for h in HORIZONS:
            indices = [i for i, r in enumerate(rows) if r['pair'] == pair and r['horizon'] == h]
            if indices:
                cells[f'{pair}/{h}'] = direction_metrics([rows[i] for i in indices], p[indices])
    return cells


def average(cells, field):
    return float(np.mean([v[field] for v in cells.values()]))


def train_price(depth, rows):
    from catboost import CatBoostRegressor
    model = CatBoostRegressor(**PARAMETERS, depth=depth, loss_function='MultiQuantile:alpha=0.1,0.5,0.9')
    model.fit([r['x'] for r in rows], [r['y'] for r in rows], sample_weight=weights(rows))
    return model


def price_predictions(model, rows):
    q = np.asarray(model.predict([r['x'] for r in rows]))
    crossings = np.any(np.diff(q, axis=1) < 0, axis=1)
    values = np.exp(np.sort(q, axis=1)) * np.array([r['base'] for r in rows])[:, None]
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError('無效價格分位數')
    return values, crossings


def calibration_adjustments(rows, predictions):
    adjustments = {}
    for h in range(1, 31):
        scores = sorted(score(q[0], q[2], r['actual']) for r, q in zip(rows, predictions) if r['horizon'] == h)
        adjustments[h] = scores[math.ceil((len(scores)+1)*.8)-1] if len(scores) >= 20 else None
    return adjustments


def apply_calibration(rows, q, adjustments):
    values = q.copy()
    for i, r in enumerate(rows):
        delta = adjustments[r['horizon']]
        if delta is None:
            values[i, [0, 2]] = np.nan
        else:
            values[i, 0] *= math.exp(-delta)
            values[i, 2] *= math.exp(delta)
    return values


def price_report(rows, q):
    cells = {}
    for pair in PAIRS:
        lookup = {r['origin']: r['base'] for r in rows if r['pair'] == pair}
        lookup.update({r['target']: r['actual'] for r in rows if r['pair'] == pair})
        for h in HORIZONS:
            selected = [dict(origin=r['origin'], target=r['target'], sigma=r.get('scale', 1),
                             low=float(v[0]) if math.isfinite(v[0]) else None,
                             median=float(v[1]), high=float(v[2]) if math.isfinite(v[2]) else None)
                        for r, v in zip(rows, q) if r['pair'] == pair and r['horizon'] == h]
            if selected:
                cells[f'{pair}/{h}'] = metrics(selected, lookup)
    return cells


def direction_checks(candidate, references):
    required = {f'{p}/{h}' for p in PAIRS for h in HORIZONS}
    if any(set(c) != required for c in [candidate, *references]):
        return {'complete': False}
    return dict(complete=True,
        f1_gain_3_points=all(average(candidate, 'f1') >= average(ref, 'f1')+.03 for ref in references),
        accuracy_loss_at_most_2_points=all(average(candidate, 'accuracy') >= average(ref, 'accuracy')-.02 for ref in references),
        each_horizon_no_worse=all(average({k:v for k,v in candidate.items() if k.endswith(f'/{h}')}, 'f1') >=
            average({k:v for k,v in ref.items() if k.endswith(f'/{h}')}, 'f1') for ref in references for h in HORIZONS))


def price_checks(candidate, baseline, flat):
    checks = replacement_checks(candidate, baseline, flat)
    checks.pop('f1_no_worse', None)
    return checks


def joint_uncertainty(rows, probabilities_by_name, prices_by_name, repetitions=200):
    """按日期取完整 30 日區塊；同日期所有幣對及步長共同保留。"""
    dates = sorted(set(r['origin'] for r in rows))
    if len(dates) < 60:
        return {'status': 'insufficient', 'block_days': 30}
    rng = np.random.default_rng(0)
    blocks = [[i for i, r in enumerate(rows) if d <= r['origin'] < d+timedelta(days=30)]
              for d in dates if d+timedelta(days=29) <= dates[-1]]
    estimates = {}
    for _ in range(repetitions):
        indices = [i for b in rng.choice(len(blocks), math.ceil(len(dates)/30)) for i in blocks[b]]
        sample = [rows[i] for i in indices]
        scores = {}
        for name, p in probabilities_by_name.items():
            m = direction_report(sample, p[indices])
            scores[name] = average(m, 'f1')
            for field in ('accuracy', 'precision', 'recall', 'f1'):
                estimates.setdefault(f'{name}/{field}', []).append(average(m, field))
        if 'current' in scores:
            for name, value in scores.items():
                if name != 'current':
                    estimates.setdefault(f'{name}-minus-current/f1', []).append(value-scores['current'])
        for name, q in prices_by_name.items():
            m = price_report(sample, q[indices])
            for field in ('normalized_mae', 'coverage80', 'normalized_width', 'normalized_interval_score'):
                vals = [v[field] for v in m.values() if v[field] is not None]
                if len(vals) == len(m):
                    estimates.setdefault(f'{name}/{field}', []).append(float(np.mean(vals)))
    return dict(status='estimated', block_days=30, repetitions=repetitions,
                intervals95={k: np.quantile(v, [.025, .975]).tolist() for k,v in estimates.items()})
