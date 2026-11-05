"""補列預測時已知的趨勢分組；零樣本格保留，不以新群組重選模型。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from datetime import date
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from app.forecast_evaluation import Evaluation
from scripts.research_forecasts import write_once


def diagnostics(rows,prices,folds):
    evaluator=Evaluation(rows);base=np.array([r['base'] for r in rows])
    trend=prices['bolt'][:,1]>=base;result={}
    for number,fold in enumerate(folds,1):
        dates=fold['dates'];start=date.fromisoformat(dates['test']);end=date.fromisoformat(dates['end'])
        selected=np.array([start<=r['origin']<end for r in rows])
        result[str(number)]={}
        for group,value in (('negative',False),('nonnegative',True)):
            weights=(selected & (trend==value)).astype(float)
            result[str(number)][group]=dict(samples=int(weights.sum()),
                prices={name:evaluator.price(q,weights) for name,q in prices.items()})
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True,type=Path);a=p.parse_args()
    source=a.source;data=json.loads((source/'predictions.json').read_text());report=json.loads((source/'point-results.json').read_text())
    rows=[dict(r,origin=date.fromisoformat(r['origin']),target=date.fromisoformat(r['target'])) for r in data['rows']]
    result=dict(purpose='descriptive_only_no_selection',
        files={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in (source/'predictions.json',source/'point-results.json',Path(__file__),ROOT/'app/forecast_evaluation.py')},
        groups=diagnostics(rows,{n:np.array(q,dtype=float) for n,q in data['price'].items()},report['folds']))
    write_once(source/'trend-diagnostics.json',result)
    print('SAVED_TREND_DIAGNOSTICS',flush=True)


if __name__=='__main__':main()
