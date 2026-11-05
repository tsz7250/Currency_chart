"""通過探索性門檻才建立獨立影子期；不變更主站或舊影子設定。"""
import argparse
import hashlib
import importlib.metadata
import json
from datetime import date, timedelta
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from app.alerts import now_taipei
from app.forecast import evaluate, ChronosPredictor
from app.forecast_market import MarketHistory, download_market
from app.forecast_supervised_shadow import SupervisedShadowStore
from app.forecast_shadow import fingerprint
from app import forecast_supervised as s
from scripts.shadow_forecasts import refresh_history
from scripts.research_forecasts import write_once
from scripts.evaluate_forecasts import save


def activate(folder, research):
    config = json.loads((research/'candidate-lock.json').read_text())
    result = json.loads((research/'result.json').read_text())
    manifest = json.loads((research/'manifest.json').read_text())
    if manifest['status'] != 'complete' or result['status'] != 'complete':
        raise ValueError('研究未完成')
    for head in ('direction','price'):
        if config[f'{head}_eligible'] != all(result[f'{head}_checks'].values()):
            raise ValueError('門檻與鎖定設定不符')
    if not (config['direction_eligible'] or config['price_eligible']):
        raise ValueError('未通過探索性門檻，不啟動新影子期')
    verify(config,research)
    active_path = folder/'active.json'
    if active_path.exists():
        active = json.loads(active_path.read_text())
        if active['research'] != str(research.resolve()) or active['run'] != fingerprint(config):
            raise ValueError('已有其他影子期，必須使用新目錄')
        return active['run']
    store = SupervisedShadowStore(folder/'shadow.sqlite3')
    run = store.register(config,now_taipei().date())
    write_once(active_path,dict(run=run,research=str(research.resolve())))
    return run


def verify(config, research):
    for filename, digest in config['runtime_files'].items():
        if hashlib.sha256((ROOT/filename).read_bytes()).hexdigest() != digest:
            raise ValueError(f'影子程式已變更：{filename}；必須另開驗證期')
    for filename, digest in config['models'].items():
        if hashlib.sha256((research/filename).read_bytes()).hexdigest() != digest:
            raise ValueError('影子模型已變更')
    for package, version in config['packages'].items():
        if importlib.metadata.version(package) != version:
            raise ValueError('影子依賴版本已變更')


def assess(store, run, history, today):
    config,start = store.info(run)
    records = store.records(run)
    result = dict(status='collecting',origins=len(records),matures_on=str(start+timedelta(days=119)),eligible_for_production=False)
    if today < start+timedelta(days=119) or len(records)!=90:
        return result
    rows, direction, candidate, baseline, raw = [], [], [], [], []
    for origin,payload in records:
        for pair, data in payload['pairs'].items():
            for h in s.HORIZONS:
                target = origin+timedelta(days=h)
                if str(target) not in history[pair]:
                    return dict(result,status='awaiting_actuals')
                actual = history[pair][str(target)]['rate']
                rows.append(dict(pair=pair,origin=origin,target=target,horizon=h,actual=actual,base=data['last_rate'],
                                 label=s.category((actual-data['last_rate'])/data['last_rate'])))
                if config['direction_eligible']:
                    direction.append(data['direction'][str(h)])
                for collection,name in ((candidate,'candidate'),(baseline,'baseline'),(raw,'chronos2')):
                    collection.append([data[name][h-1][k] for k in ('low','median','high')])
    from scripts.research_supervised import price_directions
    baseline,raw,candidate = (np.array(v,dtype=float) for v in (baseline,raw,candidate))
    refs = [s.direction_report(rows,price_directions(rows,q)) for q in (baseline,raw)]
    if config['direction_eligible']:
        result['direction'] = s.direction_report(rows,np.array(direction))
        result['direction_checks'] = s.direction_checks(result['direction'],refs)
    if config['price_eligible']:
        result['price'] = s.price_report(rows,candidate)
        result['price_checks'] = s.price_checks(result['price'],s.price_report(rows,baseline),
                                              s.price_report(rows,np.array([[r['base']]*3 for r in rows])))
    result.update(status='complete',baseline=s.price_report(rows,baseline),chronos2=s.price_report(rows,raw),
        uncertainty=s.joint_uncertainty(rows,{'candidate':np.array(direction)} if direction else {},
                                        {'candidate':candidate,'baseline':baseline} if config['price_eligible'] else {}))
    return result


def daily(folder):
    from catboost import CatBoostClassifier, CatBoostRegressor
    from scripts.benchmark_forecasts import Predictor, MODELS
    active = json.loads((folder/'active.json').read_text())
    research = Path(active['research'])
    store = SupervisedShadowStore(folder/'shadow.sqlite3')
    config,start = store.info(active['run'])
    verify(config,research)
    today = now_taipei().date()
    if store.contains(active['run'],today):
        return
    history = refresh_history(folder,research,config['pairs'],today)
    if today < start+timedelta(days=90):
        snapshot = download_market(folder/'market',today)
        market = MarketHistory(snapshot)
        classifier = config['direction_constant']
        if config['direction_eligible'] and classifier is None:
            classifier = CatBoostClassifier().load_model(str(research/'direction.cbm'))
        price = CatBoostRegressor().load_model(str(research/'price.cbm')) if config['price_eligible'] else None
        current = ChronosPredictor(ROOT/'instance/models')
        chronos = Predictor(MODELS[2],time.time()+8*3600)
        pairs = {}
        for pair in config['pairs']:
            if max(history[pair]) != str(today):
                raise ValueError('缺少今日報價')
            x = s.features(history[pair],market,pair,today)
            base = history[pair][str(today)]['rate']
            rows = [dict(x=x+[h],horizon=h,base=base) for h in range(1,31)]
            entries = sorted((date.fromisoformat(d),v['rate']) for d,v in history[pair].items())
            baseline = [dict(median=v['median'],low=v['low80'],high=v['high80']) for v in evaluate(entries,current)['points']]
            lookup = dict(entries)
            qraw = chronos([[lookup.get(today-timedelta(days=i),float('nan')) for i in range(89,-1,-1)]])[0]
            record = dict(last_rate=base,baseline=baseline,candidate=baseline,
                          chronos2=[dict(low=q[0],median=q[1],high=q[2]) for q in qraw])
            if config['direction_eligible']:
                p = s.probabilities(classifier,[rows[h-1] for h in s.HORIZONS])
                record['direction'] = {str(h):v.tolist() for h,v in zip(s.HORIZONS,p)}
            if price is not None:
                q,crossed = s.price_predictions(price,rows)
                calibrated = s.apply_calibration(rows,q,{int(h):v for h,v in config['adjustments'].items()})
                record.update(quantiles=q.tolist(),crossings=crossed.tolist(),candidate=[dict(low=float(v[0]) if np.isfinite(v[0]) else None,
                    median=float(v[1]),high=float(v[2]) if np.isfinite(v[2]) else None) for v in calibrated])
            pairs[pair]=record
        issued = now_taipei()
        if issued.date()!=today:
            raise ValueError('推論跨日，不補造昨日預測')
        store.append(active['run'],today,dict(schema_version=2,issued_at=issued.isoformat(),versions=config['models'],
            pairs=pairs,mastercard_snapshot=history,market_snapshot=snapshot))
    result = assess(store,active['run'],history,today)
    save(folder/'status.json',result)
    if result['status']=='complete' and not (folder/'final-result.json').exists():
        write_once(folder/'final-result.json',result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',required=True,type=Path)
    parser.add_argument('--activate',type=Path)
    args=parser.parse_args()
    args.folder.mkdir(parents=True,exist_ok=True)
    # 作業系統鎖保護下載快照與推論；程序失敗時自動釋放。
    with (args.folder/'worker.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            if sys.platform=='win32':
                import msvcrt
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            return
        try:
            if args.activate:
                activate(args.folder,args.activate.resolve())
            daily(args.folder)
        except Exception as error:
            save(args.folder/'operational-status.json',dict(status='failed',error=str(error),at=now_taipei().isoformat()))
            raise


if __name__=='__main__':
    main()
