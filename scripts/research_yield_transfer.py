"""獨立測市場區間訊號與直接轉用；不調整方向機率。"""
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
from catboost import CatBoostClassifier, CatBoostRegressor
from app import forecast_evaluation as e
from app import forecast_splits as split
from app import forecast_supervised as s
from app.forecast_transfer import transfer_interval
from scripts.research_yield_regimes import market_dataset
from scripts.research_cross_pair import report
from scripts.research_forecasts import write_once
from scripts.evaluate_forecasts import save


def run(args, deadline):
    out, source = args.output, args.source
    baseline = json.loads((args.reference_source/'predictions.json').read_text())
    rows = [dict(r, origin=date.fromisoformat(r['origin']), target=date.fromisoformat(r['target'])) for r in baseline['rows']]
    histories = {p: json.loads((args.history_source/'history'/f'{p}.json').read_text()) for p in s.PAIRS}
    snapshot = json.loads((args.history_source/'market/market.json').read_text())
    treasury = json.loads((source/'treasury/yields.json').read_text())
    data, _, excluded = market_dataset(snapshot, treasury, date.fromisoformat(treasury['as_of']))
    folds = split.folds(max(date.fromisoformat(min(h)) for h in histories.values()), min(date.fromisoformat(max(h)) for h in histories.values()))
    keys = [(r['pair'], r['origin'], r['horizon']) for r in rows]
    market_previous = json.loads((source/'market-predictions.json').read_text())
    previous_key = {(r['pair'], r['origin'], r['horizon']): i for i, r in enumerate(market_previous['rows'])}
    directions = {f'direct-{n}': np.array(p)[[previous_key[p, str(d), h] for p, d, h in keys]]
                  for n, p in market_previous['direction'].items()}
    directions.update({n: np.array(baseline['direction'][n]) for n in ('bolt', 'chronos2')})
    directions['cat4-control'] = np.array(baseline['direction']['control'])
    market_rows = [dict(r, origin=date.fromisoformat(r['origin']), target=date.fromisoformat(r['target'])) for r in market_previous['rows']]
    market_keys = [(r['pair'], r['origin'], r['horizon']) for r in market_rows]
    market_prices = {f'{n}-{kind}': np.zeros((len(rows), 3)) for n in data for kind in ('raw', 'calibrated')}
    audits = []
    def mc_rows(group):
        return [dict(r, base=float(histories[r['pair']][str(r['origin'])]['rate']),
            actual=float(histories[r['pair']][str(r['target'])]['rate']),
            label=s.category(float(histories[r['pair']][str(r['target'])]['rate'])/float(histories[r['pair']][str(r['origin'])]['rate'])-1)) for r in group]
    for fold in folds:
        indices = [i for i, r in enumerate(rows) if fold['test'] <= r['origin'] < fold['end']]
        market_indices = [i for i, r in enumerate(market_rows) if fold['test'] <= r['origin'] < fold['end']]
        for name, dataset in data.items():
            if time.time() >= deadline:
                raise TimeoutError('8-hour deadline')
            train, cal, test = (split.select(dataset, fold, role) for role in ('train', 'calibration', 'test'))
            split.validate_test(test, fold, s.PAIRS, s.HORIZONS)
            split.stage_summary(train, fold['calibration']);split.stage_summary(cal, fold['test'])
            if any(split.calibration_counts(cal, h)['samples'] < 40 or split.calibration_counts(cal, h)['dates'] < 20 for h in s.HORIZONS):
                raise ValueError('市場區間校準樣本不足')
            model = CatBoostClassifier();model.load_model(str(source/f'market-fold-{fold["fold"]}-{name}.cbm'))
            p = s.probabilities(model, test)
            test_lookup = {(r['pair'], r['origin'], r['horizon']): i for i, r in enumerate(test)}
            ordered = [test_lookup[market_keys[i]] for i in market_indices]
            np.testing.assert_allclose(p[ordered], np.array(market_previous['direction'][name])[market_indices], rtol=0, atol=1e-12)
            regressor = CatBoostRegressor(**s.PARAMETERS, depth=4, loss_function='MultiQuantile:alpha=0.1,0.9')
            regressor.fit([r['x'] for r in train], [math.log(r['actual']/r['base']) for r in train], sample_weight=s.weights(train))
            regressor.save_model(str(out/f'market-fold-{fold["fold"]}-{name}-interval.cbm'))
            cal_q, cal_audit = transfer_interval(regressor, cal, cal, [r['base'] for r in cal])
            q, test_audit = transfer_interval(regressor, test, test, [r['base'] for r in test])
            corrections = s.calibration_adjustments(cal, cal_q)
            adjusted = s.apply_calibration(test, q, corrections)
            market_prices[f'{name}-raw'][market_indices] = q[ordered]
            market_prices[f'{name}-calibrated'][market_indices] = adjusted[ordered]
            audits.append(dict(fold=fold['fold'], name=name, training=split.stage_summary(train, fold['calibration']),
                calibration=split.stage_summary(cal, fold['test']), calibration_bounds=cal_audit, test_bounds=test_audit))
        print('INTERVAL_MARKET_FOLD', fold['fold'], flush=True)
    market_ev = e.Evaluation(market_rows)
    market_reports = {n: market_ev.price(q) for n, q in market_prices.items()}
    market_segments = []
    for f in folds:
        ii = [i for i, r in enumerate(market_rows) if f['test'] <= r['origin'] < f['end']]
        ev = e.Evaluation([market_rows[i] for i in ii])
        market_segments.append({n: ev.price(q[ii]) for n, q in market_prices.items()})
    screen = {n: e.worth_interval(market_reports[f'{n}-calibrated'], market_reports['control-calibrated'], [
        (v[f'{n}-calibrated'], v['control-calibrated']) for v in market_segments]) for n in ('yield', 'regime')}
    write_once(out/'market-interval-results.json', dict(price=market_reports, summary={n: e.groups(v) for n, v in market_reports.items()},
        screening=screen, folds=audits, exclusions=excluded, target='public market rates, not Mastercard'))
    prices = {n: np.array(baseline['price'][n]) for n in ('control-raw', 'control-calibrated', 'bolt')}
    eligible = [n for n, v in screen.items() if v['worth_further_testing']]
    raw = {}
    for pair in s.PAIRS:
        v = json.loads((args.bolt_source/'raw'/f'{pair}.json').read_text())
        raw[pair] = dict(zip(v['origins'], v['quantiles']))
    transfer_audits = []
    for name in eligible:
        for kind in ('raw', 'calibrated'):
            prices[f'transfer-{name}-{kind}'] = np.zeros((len(rows), 3))
        for fold in folds:
            cal, test = (split.select(data[name], fold, role) for role in ('calibration', 'test'))
            indices = [i for i, r in enumerate(rows) if fold['test'] <= r['origin'] < fold['end']]
            lookup = {(r['pair'], r['origin'], r['horizon']): r for r in test}
            selected = [lookup[keys[i]] for i in indices]
            target_cal = mc_rows(cal)
            medians = [raw[r['pair']][str(r['origin'])][r['horizon']-1][1] for r in target_cal]
            model = CatBoostRegressor();model.load_model(str(out/f'market-fold-{fold["fold"]}-{name}-interval.cbm'))
            cal_q, ca = transfer_interval(model, cal, target_cal, medians)
            q, qa = transfer_interval(model, selected, [rows[i] for i in indices], prices['bolt'][indices, 1])
            corrections = s.calibration_adjustments(target_cal, cal_q)
            prices[f'transfer-{name}-raw'][indices] = q
            prices[f'transfer-{name}-calibrated'][indices] = s.apply_calibration([rows[i] for i in indices], q, corrections)
            transfer_audits.append(dict(fold=fold['fold'], name=name, calibration=ca, test=qa, corrections=corrections))
    assert all(np.array_equal(q[:, 1], prices['bolt'][:, 1]) for q in prices.values())
    result = report(rows, directions, prices)
    block = []
    for f in folds:
        ii = [i for i, r in enumerate(rows) if f['test'] <= r['origin'] < f['end']]
        block.append(report([rows[i] for i in ii], {n: p[ii] for n, p in directions.items()}, {n: q[ii] for n, q in prices.items()}))
    result.update(status='complete', purpose='exploratory_direct_market_transfer', eligible_for_production=False,
        market_interval_screen=screen, interval_candidates=eligible,
        interval_transfer=transfer_audits, evaluation_version=e.VERSION, rules=e.RULES,
        direction_engineering={n: s.direction_checks(result['direction'][n], [result['direction'][b] for b in ('bolt', 'chronos2')])
            for n in ('direct-regime',)},
        interval_checks={n: dict(zip(('engineering', 'dual_goal'), e.interval_checks(c, result['price']['bolt'])))
            for n, c in result['price'].items() if n.startswith('transfer-')},
        direction_worth={n: e.worth_direction(result['direction'][n], result['direction']['bolt'], [
            (b['direction'][n], b['direction']['bolt']) for b in block]) for n in ('direct-regime',)},
        validity=dict(classifier_feature_extraction_reproduced=True, common_samples=len(rows),
            core_only_interval_calibration=True, market_models_frozen=True, medians_unchanged=True,
            historical_vintages_verified=False), outer_fold_reports=block)
    write_once(out/'predictions.json', dict(rows=rows, direction={n: p.tolist() for n, p in directions.items()}, price={n: q.tolist() for n, q in prices.items()}))
    write_once(out/'point-results.json', result)
    print('POINT_RESULTS_SAVED', flush=True)
    result['uncertainty'] = e.uncertainty(e.Evaluation(rows), {n: directions[n] for n in ('direct-regime', 'bolt')}, prices)
    result['market_interval_uncertainty'] = e.uncertainty(market_ev, {}, {n: market_prices[f'{n}-calibrated'] for n in data}, reference='control')
    write_once(out/'result.json', result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'history-source', 'reference-source', 'bolt-source', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = dict(status='running', started=time.time(), deadline=time.time()+8*3600)
    write_once(args.output/'manifest.json', manifest)
    old = json.loads((args.source/'protocol.json').read_text())
    for f, expected in old['files'].items():
        if hashlib.sha256(Path(f).read_bytes()).hexdigest() != expected:
            raise ValueError('原市場研究來源已改變')
    files = [Path(__file__), ROOT/'app/forecast_transfer.py', ROOT/'scripts/research_yield_regimes.py',
        *[ROOT/'app'/n for n in ('forecast_yields.py', 'forecast_supervised.py', 'forecast_evaluation.py', 'forecast_splits.py',
                                 'forecast_market.py', 'forecast_interval_shape.py', 'forecast.py', 'forecast_research.py')],
        *[ROOT/'scripts'/n for n in ('research_cross_pair.py', 'research_forecasts.py', 'evaluate_forecasts.py')],
        args.source/'treasury/yields.json', args.source/'market-predictions.json', args.source/'protocol.json',
        args.history_source/'market/market.json', args.reference_source/'predictions.json',
        *[args.history_source/'history'/f'{p}.json' for p in s.PAIRS],
        *[args.source/f'market-fold-{i}-{n}.cbm' for i in (1, 2, 3) for n in ('control', 'yield', 'regime')],
        *[args.bolt_source/'raw'/f'{p}.json' for p in s.PAIRS]]
    write_once(args.output/'protocol.json', dict(files={str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
        parameters=s.PARAMETERS, depth=4, interval_quantiles=[.1, .9],
        separate_screens=True, interval_screen='same predeclared worth_interval against calibrated market control',
        market_prices='latest-price center; public market targets only',
        transfer='MC origin price times market log-return quantiles; constrain to fixed Bolt center; core-only calibration',
        packages={p: importlib.metadata.version(p) for p in ('numpy', 'catboost', 'scikit-learn')},
        bootstrap=dict(lengths=[30, 7, 14], repetitions=2000, seed=0), rules=e.RULES, automatic_promotion=False))
    def timeout():
        save(args.output/'manifest.json', dict(manifest, status='incomplete', error='8-hour deadline'));os._exit(124)
    timer=threading.Timer(8*3600, timeout);timer.daemon=True;timer.start()
    try:
        run(args, manifest['deadline']);manifest['status']='complete'
    except Exception as error:
        manifest.update(status='incomplete', error=str(error));raise
    finally:
        timer.cancel();manifest['elapsed_seconds']=time.time()-manifest['started'];save(args.output/'manifest.json', manifest)


if __name__ == '__main__':
    main()
