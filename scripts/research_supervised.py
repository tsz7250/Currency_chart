"""三折監督式研究。獨立入口避免變更舊影子期鎖定的程式。"""
import argparse
import hashlib
import importlib.metadata
import json
from datetime import date, timedelta
from pathlib import Path
import sys
import time
import threading
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from app.forecast import evaluate
from app.forecast_market import MarketHistory
from app import forecast_supervised as s
from scripts.benchmark_forecasts import Predictor, MODELS, check_deadline
from scripts.research_forecasts import write_once
from scripts.evaluate_forecasts import save


def baseline_predictions(histories, rows, deadline, destination):
    """重播現用模型真正的 180 天裁切及校準；只共用完全相同的輸入。"""
    inputs = {}
    def collect(values):
        for value in values:
            inputs.setdefault(tuple(value), None)
        return [[[v[-1]]*3 for _ in range(30)] for v in values]
    requests = sorted(set((r['pair'], r['origin']) for r in rows))
    for pair, origin in requests:
        entries = sorted((date.fromisoformat(d), v['rate']) for d,v in histories[pair].items() if d <= str(origin))
        evaluate(entries, collect)
    check_deadline(deadline)
    predictor = Predictor(MODELS[0], deadline)
    keys = list(inputs)
    print('BASELINE_CONTEXTS', len(keys), flush=True)
    values = predictor([list(k) for k in keys])
    inputs.update(zip(keys, values))
    output = {}
    for pair, origin in requests:
        check_deadline(deadline)
        entries = sorted((date.fromisoformat(d), v['rate']) for d,v in histories[pair].items() if d <= str(origin))
        result = evaluate(entries, lambda batch: [inputs[tuple(v)] for v in batch])
        output[pair, origin] = result['points']
    write_once(destination / 'baseline.json', {f'{p}/{d}': v for (p,d),v in output.items()})
    return np.array([[output[r['pair'],r['origin']][r['horizon']-1][k] for k in ('low80','median','high80')] for r in rows], dtype=float)


def raw_reference(source, rows):
    cached = {}
    for pair in s.PAIRS:
        data = json.loads((source / 'predictions' / f'{pair}.json').read_text())
        cached[pair] = dict(zip(data['origins'], data['raw']))
    return np.array([cached[r['pair']][str(r['origin'])][r['horizon']-1] for r in rows])


def price_directions(rows, q):
    p = np.zeros((len(rows), 3))
    for i, (r, v) in enumerate(zip(rows, q)):
        p[i, s.category((v[1]-r['base']) / r['base'])] = 1
    return p


def run(source, destination, deadline):
    histories = {p: json.loads((source/'history'/f'{p}.json').read_text(encoding='utf-8')) for p in s.PAIRS}
    snapshot = json.loads((source/'market/market.json').read_text(encoding='utf-8'))
    market = MarketHistory(snapshot)
    start = max(date.fromisoformat(min(v)) for v in histories.values())
    end = min(date.fromisoformat(max(v)) for v in histories.values())
    if end < start+timedelta(days=359):
        raise ValueError('三折驗證至少需要 360 日共同資料')
    for p, v in histories.items():
        write_once(destination/'history'/f'{p}.json', v)
    write_once(destination/'market/market.json', snapshot)
    provenance = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in [source/'market/market.json', *[source/'history'/f'{pair}.json' for pair in s.PAIRS],
                            *[source/'predictions'/f'{pair}.json' for pair in s.PAIRS]]}
    write_once(destination/'protocol.json', dict(purpose='exploratory_old_holdout_used', sources=provenance,
        features=s.FEATURES, parameters=s.PARAMETERS, folds=s.splits(start), threshold=.001,
        selection='per-fold core-pair tuning only; last-fold choice locked for prospective validation',
        calibration='core-pair independent calibration; pooled by horizon; frozen during each test',
        packages={p: importlib.metadata.version(p) for p in ('catboost','scikit-learn','numpy')},
        limits=['Historical publication vintages unverified.', 'Unknown Mastercard quote dates remain missing.',
                'Only one year of Mastercard labels; all results exploratory.']))
    rows = s.dataset(histories, market)
    all_test, direction_outputs, price_outputs, folds = [], {}, {}, []
    last_models = None
    for index, split in enumerate(s.splits(start)):
        check_deadline(deadline)
        stages = {stage: s.partition(rows, split, stage) for stage in ('train','tune','calibration','test')}
        train, tune, calibration, test = (stages[k] for k in ('train','tune','calibration','test'))
        expected = {(p,split['test']+timedelta(days=d),h) for p in s.PAIRS for d in range(30) for h in range(1,31)}
        if {(r['pair'],r['origin'],r['horizon']) for r in test} != expected:
            raise ValueError('共同評分起點或目標缺值；不可只評分有利的子集')
        train_d, tune_d = ([r for r in group if r['horizon'] in s.HORIZONS] for group in (train,tune))
        # 所有候選包含每日價格預測；分類評分只取 7／14／30 天。
        test_d = [r for r in test if r['horizon'] in s.HORIZONS]
        classifier_models, direction_tuning = {}, {}
        for name in s.CLASSIFIERS:
            check_deadline(deadline)
            model = s.train_classifier(name, train_d)
            classifier_models[name] = model
            direction_tuning[name] = s.direction_report(tune_d, s.probabilities(model,tune_d))
        winner_d = max([n for n in s.CLASSIFIERS if n.startswith('cat')],
                       key=lambda n: s.average(direction_tuning[n], 'f1'))
        for name, model in classifier_models.items():
            direction_outputs.setdefault(name, []).append(s.probabilities(model,test_d))
        direction_outputs.setdefault('selected', []).append(s.probabilities(classifier_models[winner_d],test_d))
        price_models, price_tuning, adjustments, crossing = {}, {}, {}, {}
        for depth in (4,6):
            check_deadline(deadline)
            model = s.train_price(depth,train)
            price_models[depth] = model
            tune_q, _ = s.price_predictions(model,tune)
            price_tuning[str(depth)] = s.price_report(tune,tune_q)
            calibration_q, _ = s.price_predictions(model,calibration)
            adjustments[depth] = s.calibration_adjustments(calibration,calibration_q)
            test_q, crossed = s.price_predictions(model,test)
            crossing[str(depth)] = dict(count=int(crossed.sum()), samples=len(test), rate=float(crossed.mean()))
            mask = [i for i,r in enumerate(test) if r['horizon'] in s.HORIZONS]
            price_outputs.setdefault(f'depth{depth}-raw', []).append(test_q[mask])
            price_outputs.setdefault(f'depth{depth}-calibrated', []).append(s.apply_calibration(test,test_q,adjustments[depth])[mask])
            write_once(destination/f'fold-{index+1}-depth{depth}-daily.json', [dict(pair=r['pair'], origin=r['origin'],
                target=r['target'], quantiles=q.tolist(), crossed=bool(c)) for r,q,c in zip(test,test_q,crossed)])
        winner_p = min((4,6), key=lambda d: s.average(price_tuning[str(d)],'normalized_mae'))
        for variant in ('raw','calibrated'):
            price_outputs.setdefault(f'selected-{variant}', []).append(price_outputs[f'depth{winner_p}-{variant}'][-1])
        all_test.extend(test_d)
        fold = dict(dates=split, sizes={k:len(v) for k,v in stages.items()},
            stage_target_max={k:max(r['target'] for r in v) for k,v in stages.items()},
            direction_selected=winner_d, price_selected=winner_p,
            direction_tuning=direction_tuning, price_tuning=price_tuning, crossings=crossing,
            calibration_counts={str(h):sum(r['horizon']==h for r in calibration) for h in range(1,31)})
        folds.append(fold)
        write_once(destination/f'fold-{index+1}.json',fold)
        print('FOLD',index+1,winner_d,winner_p,flush=True)
        last_models = classifier_models[winner_d], price_models[winner_p], adjustments[winner_p]
    dp = {n:np.concatenate(v) for n,v in direction_outputs.items()}
    pq = {n:np.concatenate(v) for n,v in price_outputs.items()}
    pq['chronos2'] = raw_reference(source,all_test)
    pq['current'] = baseline_predictions(histories,all_test,deadline,destination)
    pq['latest'] = np.array([[r['base']]*3 for r in all_test])
    for name in ('chronos2','current'):
        dp[name] = price_directions(all_test,pq[name])
    direction = {n:s.direction_report(all_test,p) for n,p in dp.items()}
    price = {n:s.price_report(all_test,q) for n,q in pq.items()}
    checks_d = s.direction_checks(direction['selected'],[direction['current'],direction['chronos2']])
    checks_p = s.price_checks(price['selected-calibrated'],price['current'],price['latest'])
    check_deadline(deadline)
    uncertainty = s.joint_uncertainty(all_test,{n:dp[n] for n in ('selected','current','chronos2')},
                                    {n:pq[n] for n in ('selected-calibrated','current')})
    # 先鎖定最後一折的模型；不以測試成績再選參數或重新擬合。
    last_models[0].save_model(str(destination/'direction.cbm')) if not isinstance(last_models[0],int) else None
    last_models[1].save_model(str(destination/'price.cbm'))
    lock = dict(schema_version=2, pairs=s.PAIRS, direction=folds[-1]['direction_selected'],
                direction_constant=last_models[0] if isinstance(last_models[0],int) else None,
                price_depth=folds[-1]['price_selected'], adjustments=last_models[2],
                direction_eligible=all(checks_d.values()), price_eligible=all(checks_p.values()),
                models={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.glob('*.cbm')},
                runtime_files={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                               [ROOT/'app/forecast_supervised.py', ROOT/'app/forecast_supervised_shadow.py',
                                ROOT/'scripts/shadow_supervised.py', ROOT/'app/forecast_market.py',ROOT/'app/forecast.py',
                                ROOT/'app/forecast_shadow.py',ROOT/'app/forecast_research.py',
                                ROOT/'scripts/research_supervised.py',ROOT/'scripts/shadow_forecasts.py',
                                ROOT/'scripts/benchmark_forecasts.py',ROOT/'scripts/research_forecasts.py']},
                packages={p:importlib.metadata.version(p) for p in ('catboost','scikit-learn','numpy','torch','chronos-forecasting','transformers')})
    write_once(destination/'candidate-lock.json',lock)
    result = dict(status='complete', purpose='exploratory_only', eligible_for_production=False,
                  direction_checks=checks_d, price_checks=checks_p, folds=folds,
                  direction=direction, price=price, uncertainty=uncertainty,
                  direction_summary={n:{k:s.average(v,k) for k in ('accuracy','precision','recall','f1','log_loss')} for n,v in direction.items()},
                  note='Price-derived reference log loss uses one-hot directions, not model probabilities.')
    write_once(destination/'result.json',result)
    write_once(destination/'predictions.json',dict(rows=[{k:v for k,v in r.items() if k!='x'} for r in all_test],
        direction={n:p.tolist() for n,p in dp.items()},
        price={n:[[float(x) if np.isfinite(x) else None for x in q] for q in v] for n,v in pq.items()}))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--output',required=True)
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    destination.mkdir(parents=True,exist_ok=True)
    manifest = dict(started=time.time(),deadline=time.time()+8*3600,status='running')
    write_once(destination/'manifest.json',manifest)
    def timeout():
        save(destination/'manifest.json',dict(manifest,status='incomplete',error='8-hour deadline exceeded',elapsed_seconds=time.time()-manifest['started']))
        os._exit(124)
    timer = threading.Timer(max(0,manifest['deadline']-time.time()),timeout)
    timer.daemon=True
    timer.start()
    try:
        result = run(Path(args.source).resolve(),destination,manifest['deadline'])
        manifest['status']='complete'
        print(json.dumps({k:result[k] for k in ('direction_checks','price_checks','direction_summary')}),flush=True)
    except Exception as error:
        manifest.update(status='incomplete',error=str(error))
        raise
    finally:
        timer.cancel()
        manifest['elapsed_seconds']=time.time()-manifest['started']
        save(destination/'manifest.json',manifest)
    if all(result['direction_checks'].values()) or all(result['price_checks'].values()):
        from scripts.shadow_supervised import activate
        from app.forecast_supervised_shadow import launch_supervised
        activate(ROOT/'instance/forecast-supervised-shadow',destination)
        launch_supervised(ROOT/'instance')


if __name__ == '__main__':
    main()
