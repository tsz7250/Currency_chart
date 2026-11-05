"""固定模型的跨幣對特徵消融研究；保留 Bolt 中心及既有評分門檻。"""
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from catboost import CatBoostRegressor
from app import forecast_cross_pair as cross
from app import forecast_evaluation as e
from app import forecast_splits as split
from app import forecast_supervised as s
from app.forecast_interval_shape import constrain, residual_bounds
from app.forecast_market import MarketHistory
from scripts.evaluate_forecasts import save
from scripts.research_forecasts import write_once


def interval_predictions(model, rows):
    bounds, crossings = residual_bounds(model, rows)
    medians = np.array([r['median'] for r in rows])
    bounds, audit = constrain(bounds, medians)
    return np.column_stack((bounds[:, 0], medians, bounds[:, 1])), dict(
        crossings=crossings, samples=len(rows), **audit)


def report(rows, directions, prices):
    evaluator = e.Evaluation(rows)
    common, availability = e.common_intervals(prices)
    dr = {n: evaluator.direction(p, probabilistic=n not in ('bolt', 'chronos2'))
          for n, p in directions.items()}
    pr = {n: evaluator.price(q) for n, q in common.items()}
    return dict(direction=dr, price=pr, interval_availability=availability,
                direction_summary={n: e.groups(v) for n, v in dr.items()},
                price_summary={n: e.groups(v) for n, v in pr.items()})


def run(history_source, baseline_source, bolt_source, output, deadline):
    previous = json.loads((baseline_source / 'predictions.json').read_text())
    rows = [dict(r, origin=date.fromisoformat(r['origin']), target=date.fromisoformat(r['target']))
            for r in previous['rows']]
    histories = {p: json.loads((history_source / 'history' / f'{p}.json').read_text()) for p in s.PAIRS}
    market = MarketHistory(json.loads((history_source / 'market/market.json').read_text()))
    raw = {}
    for p in s.PAIRS:
        data = json.loads((bolt_source / 'raw' / f'{p}.json').read_text())
        raw[p] = dict(zip(data['origins'], data['quantiles']))
    data = [r for r in s.dataset(histories, market) if str(r['origin']) in raw[r['pair']]]
    extras = {}
    for r in data:
        key = (r['pair'], r['origin'])
        if key not in extras:
            extras[key] = cross.features(market, *key)
        r['median'] = raw[r['pair']][str(r['origin'])][r['horizon']-1][1]
    variants = dict(control=data, cross=[dict(r, x=r['x'] + extras[r['pair'], r['origin']]) for r in data])
    directions = {n: np.zeros((len(rows), 3)) for n in variants}
    prices = {f'{n}-{kind}': np.zeros((len(rows), 3)) for n in variants for kind in ('raw', 'calibrated')}
    keys = [(r['pair'], r['origin'], r['horizon']) for r in rows]
    audits = []
    folds = split.folds(max(date.fromisoformat(min(v)) for v in histories.values()),
                        min(date.fromisoformat(max(v)) for v in histories.values()))
    for fold in folds:
        details = {}
        indices = [i for i, r in enumerate(rows) if fold['test'] <= r['origin'] < fold['end']]
        for name, dataset in variants.items():
            if time.time() > deadline:
                raise TimeoutError('8-hour deadline')
            train = split.select(dataset, fold, 'train')
            calibration = split.select(dataset, fold, 'calibration')
            test = split.select(dataset, fold, 'test')
            split.validate_test(test, fold, s.PAIRS)
            training_audit = split.stage_summary(train, fold['calibration'])
            calibration_audit = split.stage_summary(calibration, fold['test'])
            assert all(r['pair'] in split.CORE for r in train + calibration)
            lookup = {(r['pair'], r['origin'], r['horizon']): r for r in test}
            selected = [lookup[keys[i]] for i in indices]
            classifier = s.train_classifier('cat4', [r for r in train if r['horizon'] in s.HORIZONS])
            classifier.save_model(str(output / f'fold-{fold["fold"]}-{name}-direction.cbm'))
            directions[name][indices] = s.probabilities(classifier, selected)
            model = CatBoostRegressor(**s.PARAMETERS, depth=4, loss_function='MultiQuantile:alpha=0.1,0.9')
            model.fit([r['x'] for r in train], [math.log(r['actual']/r['median']) for r in train],
                      sample_weight=s.weights(train))
            model.save_model(str(output / f'fold-{fold["fold"]}-{name}-interval.cbm'))
            cal_q, cal_audit = interval_predictions(model, calibration)
            corrections = s.calibration_adjustments(calibration, cal_q)
            if any(v is None for v in corrections.values()):
                raise ValueError('校準不足，不得刪除測試起點')
            q, test_audit = interval_predictions(model, test)
            calibrated = s.apply_calibration(test, q, corrections)
            daily = {(r['pair'], r['origin'], r['horizon']): (v, c)
                     for r, v, c in zip(test, q, calibrated)}
            for kind, position in (('raw', 0), ('calibrated', 1)):
                prices[f'{name}-{kind}'][indices] = [daily[keys[i]][position] for i in indices]
            write_once(output / f'fold-{fold["fold"]}-{name}-daily.json', [
                dict(pair=r['pair'], origin=r['origin'], target=r['target'], horizon=r['horizon'],
                     raw=v.tolist(), calibrated=c.tolist()) for r, v, c in zip(test, q, calibrated)])
            details[name] = dict(training=training_audit, calibration=calibration_audit,
                                 calibration_bounds=cal_audit, test_bounds=test_audit, corrections=corrections)
        assert details['control']['training'] == details['cross']['training']
        audits.append(dict(dates=fold, details=details))
        print('FOLD', fold['fold'], flush=True)
    directions.update({n: np.array(previous['direction'][n]) for n in ('bolt', 'chronos2', 'selected', 'logistic')})
    prices['bolt'] = np.array(previous['price']['bolt'])
    for q in prices.values():
        assert np.array_equal(q[:, 1], prices['bolt'][:, 1])
    result = report(rows, directions, prices)
    fold_reports = []
    for fold in folds:
        ii = [i for i, r in enumerate(rows) if fold['test'] <= r['origin'] < fold['end']]
        fold_reports.append(report([rows[i] for i in ii], {n: p[ii] for n, p in directions.items()},
                                   {n: q[ii] for n, q in prices.items()}))
    comparisons = {}
    for reference in ('control', 'bolt'):
        comparisons[reference] = dict(direction=e.worth_direction(result['direction']['cross'], result['direction'][reference], [
            (f['direction']['cross'], f['direction'][reference]) for f in fold_reports]), intervals={})
        for kind in ('raw', 'calibrated'):
            name = f'cross-{kind}'
            base = f'control-{kind}' if reference == 'control' else 'bolt'
            comparisons[reference]['intervals'][kind] = e.worth_interval(result['price'][name], result['price'][base], [
                (f['price'][name], f['price'][base]) for f in fold_reports])
    result.update(status='complete', purpose='exploratory_only', eligible_for_production=False,
                  evaluation_version=e.VERSION, rule_versions=e.RULES, folds=audits, outer_fold_reports=fold_reports,
                  comparisons=comparisons, validity=dict(no_future_labels=True, medians_unchanged=True,
                  heldout_pairs_never_used_for_fit_or_calibration=True, historical_vintages_verified=False,
                  common_samples=len(rows), common_dates=len({r['origin'] for r in rows})),
                  direction_engineering=s.direction_checks(result['direction']['cross'], [result['direction'][n] for n in ('bolt', 'chronos2')]),
                  interval_checks={n: dict(zip(('engineering', 'dual_goal'), e.interval_checks(c, result['price']['bolt'])))
                                   for n, c in result['price'].items() if n != 'bolt'})
    write_once(output / 'predictions.json', dict(rows=rows, direction={n: p.tolist() for n, p in directions.items()},
                                               price={n: q.tolist() for n, q in prices.items()}))
    write_once(output / 'point-results.json', result)
    print('POINT_RESULTS_SAVED', flush=True)
    evaluator = e.Evaluation(rows)
    result['uncertainty'] = e.uncertainty(evaluator, {n: directions[n] for n in ('cross', 'control', 'bolt')}, prices)
    result['ablation_uncertainty'] = {}
    for kind in ('raw', 'calibrated'):
        result['ablation_uncertainty'][kind] = e.uncertainty(evaluator,
            {n: directions[n] for n in ('cross', 'control')} if kind == 'calibrated' else {},
            {n: prices[f'{n}-{kind}'] for n in ('cross', 'control')}, reference='control')
        print('UNCERTAINTY', kind, flush=True)
    write_once(output / 'result.json', result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('history-source', 'baseline-source', 'bolt-source', 'output'):
        parser.add_argument('--' + option, required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = dict(status='running', started=time.time(), deadline=time.time()+8*3600)
    write_once(args.output / 'manifest.json', manifest)
    files = [Path(__file__), *[ROOT/'app'/f for f in ('forecast_cross_pair.py', 'forecast_supervised.py',
             'forecast_splits.py', 'forecast_evaluation.py', 'forecast_interval_shape.py', 'forecast_market.py', 'forecast.py', 'forecast_research.py')],
             args.baseline_source/'predictions.json', args.history_source/'market/market.json',
             *[args.history_source/'history'/f'{p}.json' for p in s.PAIRS],
             *[args.bolt_source/'raw'/f'{p}.json' for p in s.PAIRS]]
    write_once(args.output / 'protocol.json', dict(files={str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
        packages={p: importlib.metadata.version(p) for p in ('catboost', 'numpy', 'scikit-learn')},
        parameters=s.PARAMETERS, depth=4, class_weights='None', features=list(s.FEATURES), added_features=cross.FEATURES,
        evaluation_version=e.VERSION, rules=e.RULES, bootstrap=dict(lengths=[30, 7, 14], repetitions=2000, seed=0),
        interval=dict(target='log(actual/Bolt median)', quantiles=[.1, .9], calibration='existing nonnegative symmetric log score; per horizon; fixed independent phase'),
        training_origins='common intersection with saved Bolt predictions; both variants identical',
        zero_shot_pairs=s.PAIRS[4:], automatic_promotion=False))
    def timeout():
        save(args.output/'manifest.json', dict(manifest, status='incomplete', error='8-hour deadline'))
        os._exit(124)
    timer = threading.Timer(8*3600, timeout)
    timer.daemon = True
    timer.start()
    try:
        run(args.history_source, args.baseline_source, args.bolt_source, args.output, manifest['deadline'])
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
