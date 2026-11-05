"""三折外層、兩折內層選參數；舊研究入口與報告保持可重播。"""
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
from app import forecast_supervised as s
from app import forecast_splits as split
from app.forecast_market import MarketHistory
from scripts.research_forecasts import write_once
from scripts.evaluate_forecasts import save
from scripts.research_supervised import raw_reference,price_directions


def tuning(rows,fold,deadline):
    direction={n:[] for n in s.CLASSIFIERS};price={d:[] for d in (4,6)};validation=[];audit=[]
    for i in range(2):
        train=split.select(rows,fold,'inner_train',i);valid=split.select(rows,fold,'inner_validation',i)
        train_d=[r for r in train if r['horizon'] in s.HORIZONS];valid_d=[r for r in valid if r['horizon'] in s.HORIZONS]
        audit.append(dict(train=split.stage_summary(train,fold['inner'][i]['start']),
                          validation=split.stage_summary(valid,fold['calibration'])))
        validation.extend(valid_d)
        for name in s.CLASSIFIERS:
            if time.time()>deadline:raise TimeoutError('8-hour deadline')
            model=s.train_classifier(name,train_d);direction[name].append(s.probabilities(model,valid_d))
        for depth in (4,6):
            model=s.train_price(depth,train);q,_=s.price_predictions(model,valid_d);price[depth].append(q)
    dr={n:s.direction_report(validation,np.concatenate(p)) for n,p in direction.items()}
    pr={d:s.price_report(validation,np.concatenate(q)) for d,q in price.items()}
    winner_d=max(s.CLASSIFIERS,key=lambda n:s.average(dr[n],'f1'))
    winner_p=min((4,6),key=lambda d:s.average(pr[d],'normalized_mae'))
    return winner_d,winner_p,dict(direction=dr,price=pr,inner=audit)


def calibrated(rows,q,calibration,cal_q):
    counts={h:split.calibration_counts(calibration,h) for h in range(1,31)}
    adjustments=s.calibration_adjustments(calibration,cal_q)
    for h,c in counts.items():
        if c['samples']<40 or c['dates']<20:adjustments[h]=None
    return s.apply_calibration(rows,q,adjustments),counts,adjustments


def run(source,bolt_source,output,deadline):
    histories={p:json.loads((source/'history'/f'{p}.json').read_text(encoding='utf-8')) for p in s.PAIRS}
    start=max(date.fromisoformat(min(v)) for v in histories.values());end=min(date.fromisoformat(max(v)) for v in histories.values())
    snapshot=json.loads((source/'market/market.json').read_text(encoding='utf-8'))
    rows=s.dataset(histories,MarketHistory(snapshot));folds=split.folds(start,end)
    files=[source/'market/market.json',*[source/'history'/f'{p}.json' for p in s.PAIRS],
           *[source/'predictions'/f'{p}.json' for p in s.PAIRS],*[bolt_source/'raw'/f'{p}.json' for p in s.PAIRS],
           ROOT/'app/forecast_splits.py',ROOT/'app/forecast_supervised.py',Path(__file__)]
    write_once(output/'protocol.json',dict(version=split.VERSION,purpose='exploratory_only',folds=folds,
        files={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in files},
        parameters=s.PARAMETERS,calibration_minimum=dict(samples=40,dates=20),
        calibration='same refitted model; frozen throughout each outer test',
        baseline='cached fixed-revision Bolt raw prices; identical independent calibration protocol',
        selection='all existing classifiers eligible; core pairs only; equal pair/horizon metrics',
        packages={p:importlib.metadata.version(p) for p in ('catboost','scikit-learn','numpy')},
        limitations=['Historical publication vintages unverified.', 'All dates have been used in prior research.',
                    'No automatic promotion or prospective validation.']))
    raw={}
    for p in s.PAIRS:
        data=json.loads((bolt_source/'raw'/f'{p}.json').read_text());raw[p]=dict(zip(data['origins'],data['quantiles']))
    reference=lambda group:np.array([raw[r['pair']][str(r['origin'])][r['horizon']-1] for r in group])
    all_test=[];dp={};pq={};audits=[]
    with (output/'assignments.jsonl').open('x',encoding='utf-8') as stream:
        for fold in folds:
            if time.time()>deadline:raise TimeoutError('8-hour deadline')
            stages={role:split.select(rows,fold,role) for role in ('train','calibration','test')}
            train,calibration,test=(stages[k] for k in ('train','calibration','test'))
            split.validate_test(test,fold,s.PAIRS)
            for i in range(2):
                for role in ('inner_train','inner_validation'):stages[f'{role}_{i+1}']=split.select(rows,fold,role,i)
            for role,group in stages.items():
                cutoff=fold['calibration'] if role in ('train','inner_validation_1','inner_validation_2') else fold['test'] if role=='calibration' else fold['inner'][int(role[-1])-1]['start'] if role.startswith('inner_train') else None
                for r in group:
                    stream.write(json.dumps(dict(fold=fold['fold'],role=role,pair=r['pair'],origin=str(r['origin']),target=str(r['target']),horizon=r['horizon'],
                        label_cutoff=str(cutoff) if cutoff else None,label_available=r['target']<cutoff if cutoff else False,
                        historical_vintage_verified=False))+'\n')
            winner_d,winner_p,tune=tuning(rows,fold,deadline)
            train_d=[r for r in train if r['horizon'] in s.HORIZONS];test_d=[r for r in test if r['horizon'] in s.HORIZONS]
            for name in s.CLASSIFIERS:
                model=s.train_classifier(name,train_d);p=s.probabilities(model,test_d)
                dp.setdefault(name,[]).append(p)
                if name==winner_d:
                    dp.setdefault('selected',[]).append(p)
                    with (output/f'fold-{fold["fold"]}-direction.pkl').open('xb') as model_file:
                        pickle.dump(model,model_file)
            model=s.train_price(winner_p,train)
            cal_q,_=s.price_predictions(model,calibration);q,cross=s.price_predictions(model,test)
            adjusted,counts,adjustments=calibrated(test,q,calibration,cal_q)
            mask=[i for i,r in enumerate(test) if r['horizon'] in s.HORIZONS]
            pq.setdefault('selected-raw',[]).append(q[mask]);pq.setdefault('selected-calibrated',[]).append(adjusted[mask])
            bolt=reference(test);bolt_cal=reference(calibration)
            base,_,_=calibrated(test,bolt,calibration,bolt_cal)
            pq.setdefault('bolt-raw',[]).append(bolt[mask]);pq.setdefault('bolt-calibrated',[]).append(base[mask])
            model.save_model(str(output/f'fold-{fold["fold"]}-price.cbm'))
            write_once(output/f'fold-{fold["fold"]}-daily.json',[dict(pair=r['pair'],origin=r['origin'],target=r['target'],
                raw=v.tolist(),low=float(a[0]) if np.isfinite(a[0]) else None,median=float(a[1]),high=float(a[2]) if np.isfinite(a[2]) else None,
                crossing=bool(c)) for r,v,a,c in zip(test,q,adjusted,cross)])
            summary=dict(dates=fold,direction_selected=winner_d,price_selected=winner_p,tuning=tune,
                stages={k:split.stage_summary(v,fold['calibration'] if k=='train' else fold['test'] if k=='calibration' else None) for k,v in stages.items() if k in ('train','calibration','test')},
                calibration_counts=counts,adjustments=adjustments,crossings=int(cross.sum()))
            summary['outer_direction']={n:s.direction_report(test_d,values[-1]) for n,values in dp.items()}
            summary['outer_price']={n:s.price_report(test_d,values[-1]) for n,values in pq.items()}
            audits.append(summary);write_once(output/f'fold-{fold["fold"]}.json',summary)
            all_test.extend(test_d);print('FOLD',fold['fold'],winner_d,winner_p,flush=True)
    dp={n:np.concatenate(v) for n,v in dp.items()};pq={n:np.concatenate(v) for n,v in pq.items()}
    pq['chronos2-raw']=raw_reference(source,all_test);pq['latest']=np.array([[r['base']]*3 for r in all_test])
    for name in ('bolt-raw','chronos2-raw'):dp[name]=price_directions(all_test,pq[name])
    direction={n:s.direction_report(all_test,p) for n,p in dp.items()};price={n:s.price_report(all_test,q) for n,q in pq.items()}
    result=dict(status='complete',purpose='exploratory_only',eligible_for_production=False,folds=audits,direction=direction,price=price,
        direction_summary={n:{k:s.average(c,k) for k in ('accuracy','precision','recall','f1','log_loss')} for n,c in direction.items()},
        direction_checks=s.direction_checks(direction['selected'],[direction['bolt-raw'],direction['chronos2-raw']]),
        price_checks=s.price_checks(price['selected-calibrated'],price['bolt-calibrated'],price['latest']),
        uncertainty=s.joint_uncertainty(all_test,dict(selected=dp['selected'],current=dp['bolt-raw']),
                                        dict(selected=pq['selected-calibrated'],current=pq['bolt-calibrated'])),
        comparison_note='Different test dates and fixed calibration; do not compare aggregate scores directly to old reports.')
    write_once(output/'result.json',result)
    write_once(output/'predictions.json',dict(rows=[{k:v for k,v in r.items() if k!='x'} for r in all_test],
        direction={n:p.tolist() for n,p in dp.items()},price={n:[[float(v) if np.isfinite(v) else None for v in q] for q in p] for n,p in pq.items()}))
    print(json.dumps(dict(direction_summary=result['direction_summary'],direction_checks=result['direction_checks'],price_checks=result['price_checks'])),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True,type=Path);p.add_argument('--bolt-source',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True);m=dict(status='running',started=time.time(),deadline=time.time()+8*3600)
    write_once(a.output/'manifest.json',m)
    def timeout():save(a.output/'manifest.json',dict(m,status='incomplete',error='8-hour deadline'));os._exit(124)
    timer=threading.Timer(8*3600,timeout);timer.daemon=True;timer.start()
    try:run(a.source,a.bolt_source,a.output,m['deadline']);m['status']='complete'
    except Exception as e:m.update(status='incomplete',error=str(e));raise
    finally:timer.cancel();m['elapsed_seconds']=time.time()-m['started'];save(a.output/'manifest.json',m)


if __name__=='__main__':main()
