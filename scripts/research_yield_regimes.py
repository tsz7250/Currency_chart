"""先測多年市場訊號，再依固定門檻決定是否測 Mastercard；不擴大搜尋。"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import threading
import time
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from app import forecast_evaluation as e
from app import forecast_splits as split
from app import forecast_supervised as s
from app import forecast_yields as yields
from app.forecast_market import MarketHistory
from app.forecast_market_features import market_features
from scripts.research_forecasts import write_once
from scripts.evaluate_forecasts import save


def summarize(rows, directions):
    ev = e.Evaluation(rows)
    metrics = {n: ev.direction(p) for n, p in directions.items()}
    return dict(direction=metrics, direction_summary={n: e.groups(c) for n, c in metrics.items()})


def market_dataset(snapshot, treasury, last):
    market = MarketHistory(snapshot)
    rates = yields.YieldHistory(treasury)
    macro = {}
    start = max(date.fromisoformat(snapshot['start']), date.fromisoformat(treasury['rows'][0]['quote_date']))
    for i in range((last-start).days+1):
        day = start+timedelta(days=i)
        value = rates.features(day)
        if value is not None:
            for pair in s.PAIRS:
                macro[pair, day] = (value, yields.regime_features(market, pair, day, value))
    variants = {n: [] for n in ('control', 'yield', 'regime')}
    missing = {}
    for pair in s.PAIRS:
        lookup = {start+timedelta(days=i): market.rate(pair, start+timedelta(days=i)) for i in range((last-start).days+1)}
        lookup = {d: v for d, v in lookup.items() if v is not None}
        ages = {}
        for day in lookup:
            sources = ('ecb', 'cbc') if 'TWD' in pair else ('ecb',)
            ages[day] = max((day-date.fromisoformat(market._known(src, day)['quote_date'])).days for src in sources)
        skipped = 0
        for origin in sorted(lookup):
            if (pair, origin) not in macro:
                skipped += len(s.HORIZONS)
                continue
            for h in s.HORIZONS:
                target = origin+timedelta(days=h)
                x = market_features(lookup, ages, origin, h)
                if x is None or target not in lookup:
                    skipped += 1
                    continue
                base, actual = lookup[origin], lookup[target]
                row = dict(pair=pair, origin=origin, target=target, horizon=h, base=base, actual=actual,
                           label=s.category(actual/base-1), x=x)
                y, z = macro[pair, origin]
                variants['control'].append(row)
                variants['yield'].append(dict(row, x=x+y))
                variants['regime'].append(dict(row, x=x+y+z))
        missing[pair] = skipped
    return variants, macro, missing


def run(args, deadline):
    out = args.output
    histories = {p: json.loads((args.history_source/'history'/f'{p}.json').read_text()) for p in s.PAIRS}
    snapshot = json.loads((args.history_source/'market/market.json').read_text())
    first = max(date.fromisoformat(min(v)) for v in histories.values())
    last = min(date.fromisoformat(max(v)) for v in histories.values())
    folds = split.folds(first, last)
    treasury = yields.download(out/'treasury', last)
    variants, _, missing = market_dataset(snapshot, treasury, last)
    test_rows, directions, audits = [], {n: [] for n in variants}, []
    for fold in folds:
        if time.time() >= deadline:
            raise TimeoutError('8-hour deadline')
        train_control = split.select(variants['control'], fold, 'train')
        test = split.select(variants['control'], fold, 'test')
        split.validate_test(test, fold, s.PAIRS, s.HORIZONS)
        test_rows.extend(test)
        for name, data in variants.items():
            train = split.select(data, fold, 'train')
            selected = split.select(data, fold, 'test')
            assert [(r['pair'], r['origin'], r['horizon']) for r in train] == [(r['pair'], r['origin'], r['horizon']) for r in train_control]
            audit = split.stage_summary(train, fold['calibration'])
            model = s.train_classifier('cat4', train)
            model.save_model(str(out/f'market-fold-{fold["fold"]}-{name}.cbm'))
            directions[name].append(s.probabilities(model, selected))
            if name == 'control':
                audits.append(dict(dates=fold, training=audit))
        print('MARKET_FOLD', fold['fold'], flush=True)
    directions = {n: np.concatenate(p) for n, p in directions.items()}
    stage = summarize(test_rows, directions)
    stage.update(status='complete', target='public market exchange rates, not Mastercard',
                 training=audits, eligible_observation_exclusions=missing)
    block_reports = []
    for f in folds:
        ii = [i for i, r in enumerate(test_rows) if f['test'] <= r['origin'] < f['end']]
        block_reports.append(summarize([test_rows[i] for i in ii], {n: p[ii] for n, p in directions.items()}))
    stage['screening'] = {n: e.worth_direction(stage['direction'][n], stage['direction']['control'], [
        (r['direction'][n], r['direction']['control']) for r in block_reports]) for n in ('yield', 'regime')}
    write_once(out/'market-results.json', stage)
    write_once(out/'market-predictions.json', dict(rows=[{k: v for k, v in r.items() if k != 'x'} for r in test_rows],
        direction={n: p.tolist() for n, p in directions.items()}))
    print('MARKET_SCREEN', stage['screening'], flush=True)
    result = dict(status='complete', purpose='exploratory_yield_regime', eligible_for_production=False,
        market=stage, market_candidates=[n for n, v in stage['screening'].items() if v['worth_further_testing']],
        historical_vintages_verified=False,
        rule='Market screening is exploratory on reused dates; no independent confirmation is claimed.')
    result['market_uncertainty'] = e.uncertainty(e.Evaluation(test_rows), directions, {}, reference='control')
    write_once(out/'result.json', result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('history-source', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = dict(status='running', started=time.time(), deadline=time.time()+8*3600)
    write_once(args.output/'manifest.json', manifest)
    files = [Path(__file__), ROOT/'app/forecast_yields.py', ROOT/'app/forecast_market_features.py',
        *[ROOT/'app'/f for f in ('forecast_market.py',
        'forecast_supervised.py', 'forecast_evaluation.py', 'forecast_splits.py', 'forecast_interval_shape.py', 'forecast.py', 'forecast_research.py')],
        *[ROOT/'scripts'/f for f in ('research_forecasts.py', 'evaluate_forecasts.py')],
        args.history_source/'market/market.json', *[args.history_source/'history'/f'{p}.json' for p in s.PAIRS]]
    write_once(args.output/'protocol.json', dict(files={str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
        parameters=s.PARAMETERS, depth=4, seed=0, yield_features=yields.FEATURES, regime_features=yields.REGIME_FEATURES,
        target_policy='Five-year public market labels remain separate from Mastercard labels.',
        screen='macro F1 improves, accuracy loss <= .02, >=2/3 folds improve; same six pairs/origins; core-only training',
        packages={p: importlib.metadata.version(p) for p in ('numpy', 'catboost', 'scikit-learn', 'requests')},
        zero_shot_pairs=s.PAIRS[4:], bootstrap=dict(lengths=[30, 7, 14], repetitions=2000, seed=0), rules=e.RULES,
        data_caveat='Treasury publication delay is a conservative scenario, not verified historical vintages', automatic_promotion=False))
    def timeout():
        save(args.output/'manifest.json', dict(manifest, status='incomplete', error='8-hour deadline'))
        os._exit(124)
    timer = threading.Timer(8*3600, timeout)
    timer.daemon = True
    timer.start()
    try:
        run(args, manifest['deadline'])
        manifest['status'] = 'complete'
    except Exception as error:
        manifest.update(status='incomplete', error=str(error))
        raise
    finally:
        timer.cancel()
        manifest['elapsed_seconds'] = time.time()-manifest['started']
        save(args.output/'manifest.json', manifest)


if __name__ == '__main__':
    main()
