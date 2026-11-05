"""固定融合市場分位數，再以成熟 Mastercard 核心結果共同校準。"""
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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from catboost import CatBoostRegressor
from app import forecast_evaluation as e
from app import forecast_splits as split
from app import forecast_supervised as s
from app.forecast_interval_pool import pool
from app.forecast_transfer import transfer_interval
from scripts.research_cross_pair import report
from scripts.research_forecasts import write_once
from scripts.research_yield_regimes import market_dataset
from scripts.evaluate_forecasts import save


def run(args,deadline):
    previous=json.loads((args.source/'predictions.json').read_text())
    rows=[dict(r,origin=date.fromisoformat(r['origin']),target=date.fromisoformat(r['target'])) for r in previous['rows']]
    histories={p:json.loads((args.history_source/'history'/f'{p}.json').read_text()) for p in s.PAIRS}
    snapshot=json.loads((args.history_source/'market/market.json').read_text())
    treasury=json.loads((args.market_source/'treasury/yields.json').read_text())
    data,_,_=market_dataset(snapshot,treasury,date.fromisoformat(treasury['as_of']))
    folds=split.folds(max(date.fromisoformat(min(h)) for h in histories.values()),min(date.fromisoformat(max(h)) for h in histories.values()))
    keys=[(r['pair'],r['origin'],r['horizon']) for r in rows]
    prices={n:np.array(previous['price'][n]) for n in ('bolt','transfer-yield-calibrated','transfer-regime-calibrated')}
    prices['pool-raw']=np.zeros((len(rows),3));prices['pool-calibrated']=np.zeros((len(rows),3))
    raw={}
    for pair in s.PAIRS:
        value=json.loads((args.bolt_source/'raw'/f'{pair}.json').read_text());raw[pair]=dict(zip(value['origins'],value['quantiles']))
    audits=[]
    for fold in folds:
        if time.time()>=deadline:raise TimeoutError('8-hour deadline')
        calibration={name:split.select(data[name],fold,'calibration') for name in ('yield','regime')}
        lookup={name:{(r['pair'],r['origin'],r['horizon']):r for r in group} for name,group in calibration.items()}
        common=sorted(set(lookup['yield'])&set(lookup['regime']),key=lambda item:(item[1],item[0],item[2]))
        target=[]
        for pair,origin,horizon in common:
            target_date=origin+timedelta(days=horizon)
            target.append(dict(lookup['yield'][pair,origin,horizon],base=float(histories[pair][str(origin)]['rate']),
                actual=float(histories[pair][str(target_date)]['rate']),target=target_date))
        split.stage_summary(target,fold['test'])
        medians=[raw[r['pair']][str(r['origin'])][r['horizon']-1][1] for r in target]
        calibration_q=[];calibration_audit=[]
        for name in ('yield','regime'):
            model=CatBoostRegressor();model.load_model(str(args.source/f'market-fold-{fold["fold"]}-{name}-interval.cbm'))
            q,audit=transfer_interval(model,[lookup[name][key] for key in common],target,medians)
            calibration_q.append(q);calibration_audit.append(audit)
        pooled,pooled_audit=pool(calibration_q[0],calibration_q[1],np.array(medians))
        corrections=s.calibration_adjustments(target,pooled)
        if any(corrections[h] is None for h in s.HORIZONS):raise ValueError('共同區間校準樣本不足')
        indices=[i for i,r in enumerate(rows) if fold['test']<=r['origin']<fold['end']]
        first=np.array(previous['price']['transfer-yield-raw'])[indices]
        second=np.array(previous['price']['transfer-regime-raw'])[indices]
        q,test_audit=pool(first,second,prices['bolt'][indices,1])
        prices['pool-raw'][indices]=q
        prices['pool-calibrated'][indices]=s.apply_calibration([rows[i] for i in indices],q,corrections)
        audits.append(dict(fold=fold['fold'],common_keys=len(common),latest_target=max(r['target'] for r in target),cutoff=fold['test'],
            corrections=corrections,calibration_sources=calibration_audit,calibration_pool=pooled_audit,test_pool=test_audit))
    assert all(np.array_equal(q[:,1],prices['bolt'][:,1]) for q in prices.values())
    result=report(rows,{},prices)
    blocks=[]
    for fold in folds:
        indices=[i for i,r in enumerate(rows) if fold['test']<=r['origin']<fold['end']]
        blocks.append(report([rows[i] for i in indices],{},{n:q[indices] for n,q in prices.items()}))
    candidate=result['price']['pool-calibrated'];baseline=result['price']['bolt']
    result.update(status='complete',purpose='exploratory_log_quantile_pool_then_cqr',eligible_for_production=False,
        calibration=audits,outer_fold_reports=blocks,
        interval_checks=dict(zip(('engineering','dual_goal'),e.interval_checks(candidate,baseline))),
        interval_worth=e.worth_interval(candidate,baseline,[(b['price']['pool-calibrated'],b['price']['bolt']) for b in blocks]),
        validity=dict(common_samples=len(rows),global_settings=True,core_only_calibration=True,extra_pair_labels_unused=True,
                      medians_unchanged=True,historical_vintages_verified=False))
    write_once(args.output/'predictions.json',dict(rows=rows,direction={},price={n:q.tolist() for n,q in prices.items()}))
    write_once(args.output/'point-results.json',result);print('POINT_RESULTS_SAVED',flush=True)
    result['uncertainty']=e.uncertainty(e.Evaluation(rows),{},prices)
    write_once(args.output/'result.json',result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','market-source','history-source','bolt-source','output'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    manifest=dict(status='running',started=time.time(),deadline=time.time()+8*3600);write_once(args.output/'manifest.json',manifest)
    parent=json.loads((args.source/'protocol.json').read_text())
    if not all(hashlib.sha256(Path(f).read_bytes()).hexdigest()==digest for f,digest in parent['files'].items()):
        raise ValueError('來源研究檔案已改變')
    files=[Path(__file__),ROOT/'app/forecast_interval_pool.py',args.source/'predictions.json',args.source/'protocol.json',
        args.market_source/'treasury/yields.json',args.history_source/'market/market.json',ROOT/'scripts/research_yield_regimes.py',
        *[ROOT/'app'/name for name in ('forecast_transfer.py','forecast_evaluation.py','forecast_supervised.py','forecast_market.py','forecast_splits.py','forecast_interval_shape.py')],
        *[ROOT/'scripts'/name for name in ('research_cross_pair.py','research_forecasts.py','evaluate_forecasts.py')],
        *[args.history_source/'history'/f'{p}.json' for p in s.PAIRS],*[args.bolt_source/'raw'/f'{p}.json' for p in s.PAIRS],
        *[args.source/f'market-fold-{i}-{name}-interval.cbm' for i in (1,2,3) for name in ('yield','regime')]]
    write_once(args.output/'protocol.json',dict(files={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
        pooling='fixed equal mean of log lower and upper prices; constrain to Bolt center before calibration',
        calibration='existing independent symmetric CQR on mature core labels, per horizon',
        bootstrap=dict(lengths=[30,7,14],repetitions=2000,seed=0),rules=e.RULES,automatic_promotion=False,
        packages={p:importlib.metadata.version(p) for p in ('numpy','catboost','scikit-learn')}))
    def timeout():save(args.output/'manifest.json',dict(manifest,status='incomplete',error='8-hour deadline'));os._exit(124)
    timer=threading.Timer(8*3600,timeout);timer.daemon=True;timer.start()
    try:run(args,manifest['deadline']);manifest['status']='complete'
    except Exception as error:manifest.update(status='incomplete',error=str(error));raise
    finally:timer.cancel();manifest['elapsed_seconds']=time.time()-manifest['started'];save(args.output/'manifest.json',manifest)


if __name__=='__main__':main()
