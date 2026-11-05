"""固定等權融合市場情境與 Mastercard CatBoost 方向機率。"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import threading
import time
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from app import forecast_evaluation as evaluation
from app import forecast_supervised as supervised
from scripts.evaluate_forecasts import save
from scripts.research_forecasts import write_once


def direction_report(rows, probabilities):
    evaluator = evaluation.Evaluation(rows)
    cells = {name: evaluator.direction(values, probabilistic=name not in ('bolt', 'chronos2'))
             for name, values in probabilities.items()}
    return dict(direction=cells, direction_summary={name:evaluation.groups(value) for name,value in cells.items()})


def run(args):
    previous = json.loads((args.source/'predictions.json').read_text())
    rows = [dict(row, origin=date.fromisoformat(row['origin']), target=date.fromisoformat(row['target']))
            for row in previous['rows']]
    probabilities = {name:np.asarray(previous['direction'][name], dtype=float)
                     for name in ('direct-regime','cat4-control','bolt','chronos2')}
    for values in probabilities.values():
        if values.shape != (len(rows),3) or not np.isfinite(values).all() or not np.allclose(values.sum(1),1):
            raise ValueError('來源分類機率無效')
    probabilities['blend'] = (probabilities['direct-regime']+probabilities['cat4-control'])/2
    result = direction_report(rows, probabilities)
    origins = sorted({row['origin'] for row in rows})
    if len(origins) != 90:
        raise ValueError('方向融合需要 90 個共同測試起點')
    blocks = []
    for start in range(0,90,30):
        selected_origins = set(origins[start:start+30])
        indices = [i for i,row in enumerate(rows) if row['origin'] in selected_origins]
        blocks.append(direction_report([rows[i] for i in indices],
            {name:values[indices] for name,values in probabilities.items()}))
    result.update(status='complete', purpose='final_fixed_direction_blend', eligible_for_production=False,
        outer_fold_reports=blocks,
        direction_engineering=supervised.direction_checks(result['direction']['blend'],
            [result['direction'][name] for name in ('bolt','chronos2')]),
        direction_worth=evaluation.worth_direction(result['direction']['blend'],result['direction']['bolt'],[
            (block['direction']['blend'],block['direction']['bolt']) for block in blocks]),
        validity=dict(common_samples=len(rows),global_weight=.5,historical_vintages_verified=False))
    write_once(args.output/'predictions.json',dict(rows=rows,direction={name:value.tolist() for name,value in probabilities.items()}))
    write_once(args.output/'point-results.json',result)
    result['uncertainty']=evaluation.uncertainty(evaluation.Evaluation(rows),
        {name:probabilities[name] for name in ('blend','bolt')},{})
    write_once(args.output/'result.json',result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    manifest=dict(status='running',started=time.time(),deadline=time.time()+8*3600);write_once(args.output/'manifest.json',manifest)
    parent=json.loads((args.source/'protocol.json').read_text())
    if not all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==digest for path,digest in parent['files'].items()):
        raise ValueError('來源研究檔案已改變')
    files=[Path(__file__),args.source/'predictions.json',args.source/'protocol.json',
        *[ROOT/'app'/name for name in ('forecast_evaluation.py','forecast_supervised.py')],
        ROOT/'scripts/research_forecasts.py',ROOT/'scripts/evaluate_forecasts.py']
    write_once(args.output/'protocol.json',dict(files={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
        blend='fixed arithmetic mean of direct-regime and cat4-control probabilities',weight=.5,
        bootstrap=dict(lengths=[30,7,14],repetitions=2000,seed=0),rules=evaluation.RULES,automatic_promotion=False,
        packages={name:importlib.metadata.version(name) for name in ('numpy','scikit-learn')}))
    def timeout():save(args.output/'manifest.json',dict(manifest,status='incomplete',error='8-hour deadline'));os._exit(124)
    timer=threading.Timer(8*3600,timeout);timer.daemon=True;timer.start()
    try:run(args);manifest['status']='complete'
    except Exception as error:manifest.update(status='incomplete',error=str(error));raise
    finally:timer.cancel();manifest['elapsed_seconds']=time.time()-manifest['started'];save(args.output/'manifest.json',manifest)


if __name__=='__main__':main()
