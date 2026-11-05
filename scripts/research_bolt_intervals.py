"""固定現用價格，只比較波動標準化的對稱區間校準。"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time
from datetime import date,timedelta
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from app import forecast_supervised as s
from app.forecast import score
from scripts.benchmark_forecasts import Predictor,MODELS,inputs_for
from scripts.research_forecasts import write_once
from scripts.evaluate_forecasts import save


def scale(lookup,origin,horizon):
    values=[lookup.get(origin-timedelta(days=i)) for i in range(20,-1,-1)]
    changes=[math.log(b/a) for a,b in zip(values,values[1:]) if a and b]
    return max(float(np.std(changes)) if len(changes)>=10 else 0,1e-5)*math.sqrt(horizon)


def adjustment(records,origin,horizon):
    values=sorted(r['score'] for r in records if r['horizon']==horizon
                  and origin-timedelta(days=90)<=r['target']<origin)
    if len(values)<40:
        return None,len(values)
    return values[min(len(values)-1,math.ceil((len(values)+1)*.8)-1)],len(values)


def run(source,output,deadline):
    data=json.loads((source/'predictions.json').read_text())
    rows=[dict(r,origin=date.fromisoformat(r['origin']),target=date.fromisoformat(r['target'])) for r in data['rows']]
    histories={p:json.loads((source/'history'/f'{p}.json').read_text()) for p in s.PAIRS}
    write_once(output/'protocol.json',dict(model=MODELS[0],context_days=91,volatility_days=20,
        scale_floor=1e-5,calibration_days=90,minimum=40,alpha=.2,
        calibration_pairs=s.PAIRS[:4],target_maturity='target strictly before origin',
        purpose='exploratory_only',sources={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [source/'predictions.json',*[source/'history'/f'{pair}.json' for pair in s.PAIRS]]},
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    predictor=Predictor(MODELS[0],deadline)
    raw={};records=[];lookups={}
    for pair,history in histories.items():
        entries=sorted((date.fromisoformat(d),v['rate']) for d,v in history.items())
        lookup=lookups[pair]=dict(entries)
        origins,inputs=inputs_for(entries,entries[0][0],entries[-1][0]+timedelta(days=1),91)
        predictions=predictor(inputs)
        write_once(output/'raw'/f'{pair}.json',dict(origins=origins,quantiles=predictions))
        for origin,forecast in zip(origins,predictions):
            raw[pair,origin]=forecast
            if pair in s.PAIRS[:4]:
                for h,q in enumerate(forecast,1):
                    target=origin+timedelta(days=h)
                    if target in lookup:
                        records.append(dict(horizon=h,target=target,score=score(q[0],q[2],lookup[target])/scale(lookup,origin,h)))
        print('RAW',pair,flush=True)
    baseline=np.array(data['price']['current'],dtype=float)
    candidate=baseline.copy();audit=[]
    for i,r in enumerate(rows):
        if time.time()>deadline:raise TimeoutError('8-hour deadline')
        q=raw[r['pair'],r['origin']][r['horizon']-1]
        if not math.isclose(q[1],baseline[i,1],rel_tol=1e-6):
            raise ValueError('現用價格與對照推論不一致')
        sigma=scale(lookups[r['pair']],r['origin'],r['horizon']);r['scale']=sigma
        delta,n=adjustment(records,r['origin'],r['horizon'])
        if delta is None:raise ValueError('共同評分起點校準不足')
        candidate[i,0]=min(q[0]*math.exp(-delta*sigma),baseline[i,1])
        candidate[i,2]=max(q[2]*math.exp(delta*sigma),baseline[i,1])
        audit.append(dict(pair=r['pair'],origin=r['origin'],horizon=r['horizon'],scale=sigma,
                          calibration_samples=n,adjustment=delta,quantiles=candidate[i].tolist()))
    assert np.array_equal(candidate[:,1],baseline[:,1])
    reports={name:s.price_report(rows,q) for name,q in [('candidate',candidate),('current',baseline)]}
    a,b=reports['candidate'],reports['current']
    avg=lambda c,k:float(np.mean([v[k] for v in c.values()]))
    checks=dict(price_unchanged=all(a[k]['normalized_mae']==b[k]['normalized_mae'] for k in a),
        coverage_target=.75<=avg(a,'coverage80')<=.85,coverage_floor=all(v['coverage80']>=.7 for v in a.values()),
        interval_score_10_percent=avg(a,'normalized_interval_score')<=.9*avg(b,'normalized_interval_score'),
        width_limit=avg(a,'normalized_width')<=1.2*avg(b,'normalized_width'))
    result=dict(status='complete',purpose='exploratory',eligible_for_production=False,checks=checks,
        reports=reports,summary={name:{f:avg(c,f) for f in ('normalized_mae','coverage80','normalized_width',
            'normalized_interval_score','lower_miss_rate','upper_miss_rate')} for name,c in reports.items()},
        uncertainty=s.joint_uncertainty(rows,{},dict(candidate=candidate,current=baseline)))
    write_once(output/'predictions.json',audit);write_once(output/'result.json',result)
    print(json.dumps(dict(checks=checks,summary=result['summary'])),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    manifest=dict(status='running',started=time.time(),deadline=time.time()+8*3600)
    write_once(args.output/'manifest.json',manifest)
    try:
        run(args.source,args.output,manifest['deadline']);manifest['status']='complete'
    except Exception as e:
        manifest.update(status='incomplete',error=str(e));raise
    finally:
        manifest['elapsed_seconds']=time.time()-manifest['started'];save(args.output/'manifest.json',manifest)


if __name__=='__main__':main()
