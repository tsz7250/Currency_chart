"""先重評舊結果，再研究固定兩階段方向與趨勢區間；不發布或替換。"""
import argparse
import hashlib
import importlib.metadata
import json
import os
import pickle
from pathlib import Path
import sys
import threading
import time
from datetime import date
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from app import forecast_evaluation as e
from app import forecast_candidates as candidate
from app import forecast_supervised as s
from app import forecast_splits as split
from app.forecast_market import MarketHistory
from scripts.research_forecasts import write_once
from scripts.evaluate_forecasts import save


def reports(rows,directions,prices):
    evaluator=e.Evaluation(rows);common,availability=e.common_intervals(prices)
    dr={n:evaluator.direction(p,probabilistic=n not in ('bolt','chronos2')) for n,p in directions.items()}
    pr={n:evaluator.price(q) for n,q in common.items()}
    return evaluator,common,dict(direction=dr,price=pr,interval_availability=availability,
        direction_summary={n:e.groups(c) for n,c in dr.items()},price_summary={n:e.groups(c) for n,c in pr.items()})


def run(history_source,nested_source,bolt_source,output,deadline):
    old=json.loads((nested_source/'predictions.json').read_text());old_result=json.loads((nested_source/'result.json').read_text())
    rows=[dict(r,origin=date.fromisoformat(r['origin']),target=date.fromisoformat(r['target'])) for r in old['rows']]
    old_direction={n:np.array(p) for n,p in old['direction'].items()}
    old_direction['bolt']=old_direction.pop('bolt-raw');old_direction['chronos2']=old_direction.pop('chronos2-raw')
    old_prices={n:np.array(q,dtype=float) for n,q in old['price'].items()}
    old_prices['bolt']=old_prices.pop('bolt-calibrated')
    evaluator,common,legacy=reports(rows,old_direction,old_prices)
    legacy['evaluation_version']=e.VERSION;legacy['rule_versions']=e.RULES
    legacy['engineering'],legacy['dual_goal']=e.price_checks(legacy['price']['selected-calibrated'],legacy['price']['bolt'],legacy['price']['latest'])
    legacy['numeric_audit']=dict(fixed_three_class_scores_match=all(np.allclose(
        [v[f] for f in ('accuracy','precision','recall','f1')],
        [old_result['direction']['selected'][k][f] for f in ('accuracy','precision','recall','f1')]) for k,v in legacy['direction']['selected'].items()),
        fixes=['All missing interval fields are null; interval metrics use common valid rows.',
               'Supplement active-label F1; do not replace fixed-three-class F1.',
               'Separate core, extra and all groups; distinct engineering and dual-goal criteria.'])
    write_once(output/'legacy-reevaluation.json',legacy)
    print('LEGACY_REEVALUATED',flush=True)
    histories={p:json.loads((history_source/'history'/f'{p}.json').read_text()) for p in s.PAIRS}
    market=MarketHistory(json.loads((history_source/'market/market.json').read_text()))
    data=s.dataset(histories,market);lookup={(r['pair'],r['origin'],r['horizon']):r for r in data}
    all_test=[lookup[r['pair'],r['origin'],r['horizon']] for r in rows]
    raw={}
    for p in s.PAIRS:
        v=json.loads((bolt_source/'raw'/f'{p}.json').read_text());raw[p]=dict(zip(v['origins'],v['quantiles']))
    medians=lambda group:np.array([raw[r['pair']][str(r['origin'])][r['horizon']-1][1] for r in group])
    pnew=np.zeros((len(rows),3));prices={n:np.zeros((len(rows),3)) for n in ('pooled','grouped')};audits=[]
    for fold in split.folds(max(date.fromisoformat(min(v)) for v in histories.values()),min(date.fromisoformat(max(v)) for v in histories.values())):
        if time.time()>deadline:raise TimeoutError('8-hour deadline')
        train=[r for r in split.select(data,fold,'train') if r['horizon'] in s.HORIZONS]
        cal=split.select(data,fold,'calibration');test=split.select(data,fold,'test')
        split.validate_test(test,fold,s.PAIRS)
        split.stage_summary(train,fold['calibration']);split.stage_summary(cal,fold['test'])
        assert all(r['pair'] in split.CORE for r in train+cal)
        indices=[i for i,r in enumerate(rows) if fold['test']<=r['origin']<fold['end']]
        selected=[all_test[i] for i in indices]
        model=candidate.train_direction(train);pnew[indices]=candidate.direction(model,selected)
        with (output/f'fold-{fold["fold"]}-direction.pkl').open('xb') as stream:pickle.dump(model,stream)
        details={}
        for name in prices:
            q,audit=candidate.bounds(test,medians(test),cal,medians(cal),name=='grouped')
            write_once(output/f'fold-{fold["fold"]}-{name}-daily.json',[
                dict(pair=r['pair'],origin=r['origin'],target=r['target'],quantiles=v.tolist(),**a) for r,v,a in zip(test,q,audit)])
            by_key={(r['pair'],r['origin'],r['horizon']):v for r,v in zip(test,q)}
            prices[name][indices]=np.array([by_key[r['pair'],r['origin'],r['horizon']] for r in selected])
            details[name]=dict(fallbacks=sum(a['fallback'] for a in audit),constraint_cases=sum(a['constraint_added_width']>0 for a in audit))
        audits.append(dict(dates=fold,training=split.stage_summary(train,fold['calibration']),
            calibration=split.stage_summary(cal,fold['test']),details=details));print('FOLD',fold['fold'],flush=True)
    for q in prices.values():assert np.array_equal(q[:,1],np.array(old['price']['bolt-raw'])[:,1])
    directions=dict(two_stage=pnew,selected=old_direction['selected'],logistic=old_direction['logistic'],bolt=old_direction['bolt'],chronos2=old_direction['chronos2'])
    prices['bolt']=np.array(old['price']['bolt-calibrated'],dtype=float)
    evaluator,common,result=reports(all_test,directions,prices)
    result.update(status='complete',purpose='exploratory_only',evaluation_version=e.VERSION,rule_versions=e.RULES,
        eligible_for_production=False,folds=audits,validity=dict(common_samples=len(rows)==1620,common_dates=len(evaluator.dates)==90,
        complete_intervals=all(v['excluded']==0 for v in result['interval_availability'].values()),no_future_labels=True,
        heldout_pairs_never_used_for_fit_or_calibration=True,median_prices_unchanged=True,historical_vintages_verified=False))
    fold_reports=[]
    for audit in audits:
        f=audit['dates'];ii=[i for i,r in enumerate(rows) if f['test']<=r['origin']<f['end']]
        _,_,fr=reports([rows[i] for i in ii],{n:p[ii] for n,p in directions.items()},{n:q[ii] for n,q in prices.items()})
        fold_reports.append(fr)
    result['outer_fold_reports']=fold_reports
    result['worth_further_testing']=dict(direction=e.worth_direction(result['direction']['two_stage'],result['direction']['bolt'],
        [(f['direction']['two_stage'],f['direction']['bolt']) for f in fold_reports]),
        intervals={n:e.worth_interval(result['price'][n],result['price']['bolt'],[(f['price'][n],f['price']['bolt']) for f in fold_reports]) for n in ('pooled','grouped')})
    result['replacement_engineering']=dict(direction=s.direction_checks(result['direction']['two_stage'],[result['direction']['bolt'],result['direction']['chronos2']]),
        intervals={n:e.interval_checks(result['price'][n],result['price']['bolt'])[0] for n in ('pooled','grouped')})
    result['interval_dual_goal']={n:e.interval_checks(result['price'][n],result['price']['bolt'])[1] for n in ('pooled','grouped')}
    # 先保存點估計；不確定性計算中斷也不遺失已完成結果。
    write_once(output/'point-results.json',result)
    write_once(output/'predictions.json',dict(rows=[{k:v for k,v in r.items() if k!='x'} for r in all_test],direction={n:p.tolist() for n,p in directions.items()},
        price={n:[[float(v) if np.isfinite(v) else None for v in q] for q in p] for n,p in common.items()}))
    legacy_uncertainty=e.uncertainty(e.Evaluation(rows),dict(selected=old_direction['selected'],bolt=old_direction['bolt']),
        dict(selected=common_legacy(old_prices)['selected-calibrated'],bolt=common_legacy(old_prices)['bolt']))
    write_once(output/'legacy-uncertainty.json',legacy_uncertainty)
    result['uncertainty']=e.uncertainty(evaluator,dict(two_stage=pnew,bolt=directions['bolt']),common)
    result['uncertainty_note']='Intervals crossing zero mean uncertain evidence, not no improvement. 7/14/30-day sensitivity is exploratory.'
    write_once(output/'result.json',result)
    print(json.dumps(dict(direction_summary=result['direction_summary'],price_summary=result['price_summary'],worth=result['worth_further_testing'],engineering=result['replacement_engineering'])),flush=True)


def common_legacy(prices):return e.common_intervals(prices)[0]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--history-source',required=True,type=Path);p.add_argument('--nested-source',required=True,type=Path)
    p.add_argument('--bolt-source',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True);m=dict(status='running',started=time.time(),deadline=time.time()+8*3600)
    write_once(a.output/'manifest.json',m)
    files=[a.nested_source/'predictions.json',a.nested_source/'result.json',a.history_source/'market/market.json',
        *[a.history_source/'history'/f'{pair}.json' for pair in s.PAIRS],*[a.bolt_source/'raw'/f'{pair}.json' for pair in s.PAIRS],
        ROOT/'app/forecast_evaluation.py',ROOT/'app/forecast_candidates.py',ROOT/'app/forecast_splits.py',Path(__file__)]
    write_once(a.output/'protocol.json',dict(version=e.VERSION,rules=e.RULES,files={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
        packages={p:importlib.metadata.version(p) for p in ('numpy','scikit-learn')},bootstrap=dict(method='circular shared-date blocks',lengths=[30,7,14],repetitions=2000,seed=0),
        direction=dict(C=1,iterations=2000,seed=0,flat_balanced=True,sign_balanced=False),interval=dict(window='fixed independent calibration',minimum_samples=40,minimum_dates=20,shrinkage=60),
        zero_shot_pairs=['AUD-USD','GBP-JPY'],automatic_promotion=False))
    def timeout():save(a.output/'manifest.json',dict(m,status='incomplete',error='8-hour deadline'));os._exit(124)
    timer=threading.Timer(8*3600,timeout);timer.daemon=True;timer.start()
    try:run(a.history_source,a.nested_source,a.bolt_source,a.output,m['deadline']);m['status']='complete'
    except Exception as error:m.update(status='incomplete',error=str(error));raise
    finally:timer.cancel();m['elapsed_seconds']=time.time()-m['started'];save(a.output/'manifest.json',m)


if __name__=='__main__':main()
