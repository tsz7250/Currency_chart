"""建立可審查發布包；不對外上傳。"""
import argparse
import json
import os
from pathlib import Path
import sys
import hashlib
import importlib.metadata
from datetime import date,datetime,timedelta,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.forecast_archive import ForecastArchive
from app.forecast_release import PAIRS,ReleaseStore,bundle
from app.forecast_publisher import replay,realized
from app.forecast import ChronosPredictor
from app.alerts import now_taipei


def refresh(archive,today):
    from app.mastercard_scraper import MastercardScraper
    scraper=MastercardScraper(str(ROOT/'mastercard_cookies.json'));errors=[]
    for pair in PAIRS:
        history=archive.history(pair)
        start=max(date.fromisoformat(min(history)),today-timedelta(days=364)) if history else today-timedelta(days=364)
        days=sorted(set([today-timedelta(days=i) for i in range(7)]+[start+timedelta(days=i) for i in range(max(0,(today-start).days+1)) if str(start+timedelta(days=i)) not in history]))
        for day in days:
            try:
                result=scraper.get_exchange_rate(datetime.combine(day,datetime.min.time()),*pair.split('-'))
                if not result or 'conversionRate' not in result.get('data',{}):
                    errors.append(f'{pair}/{day}: no quote');continue
                archive.add(pair,day,result['data']['conversionRate'],result['data'].get('fxDate'))
            except Exception as e:errors.append(f'{pair}/{day}: {e}')
    return errors


def build(a):
    archive=ForecastArchive(a.instance/'forecast-publisher/quotes.sqlite3')
    if a.import_history:
        for pair in PAIRS:
            for day,v in json.loads((a.import_history/f'{pair}.json').read_text(encoding='utf-8')).items():
                archive.add(pair,day,v['rate'],v.get('fx_date') or v.get('fxDate'),kind='historical_import')
    folder=a.instance/'forecast-publisher';folder.mkdir(parents=True,exist_ok=True)
    if a.refresh:
        errors=refresh(archive,now_taipei().date())
        (folder/'fetch-status.json').write_text(json.dumps(dict(at=now_taipei().isoformat(),errors=errors)),encoding='utf-8')
    end=a.as_of or now_taipei().date()
    if not a.preview and end!=now_taipei().date():raise ValueError('正式發布不得回填過去日期')
    histories={pair:{d:v for d,v in archive.history(pair).items() if d<=str(end)} for pair in PAIRS}
    if any(str(end) not in data or len(data)<270 for data in histories.values()):raise ValueError('六組共同基準日報價或暖機資料不足')
    from app.forecast_archive import encoded
    fingerprint=hashlib.sha256(encoded(histories)).hexdigest()
    if not a.preview:
        for previous in reversed(archive.records()):
            if previous.get('data_sha256')==fingerprint:
                if a.activate:
                    ReleaseStore(a.instance/'forecast-releases').install((folder/previous['version']/'forecast-release.zip').read_bytes())
                print('UNCHANGED',previous['version']);return
    predictor=ChronosPredictor(a.instance/'models');reports={}
    for pair,history in histories.items():
        entries=sorted((date.fromisoformat(d),v['rate']) for d,v in history.items())
        report,_=replay(entries,predictor)
        for h in (7,14,30):report['backtest'][str(h)]['issued']=realized(archive.records(),archive,pair,h)
        reports[pair]=report;print('READY',pair,flush=True)
    issued=now_taipei()
    if not a.preview and issued.date()!=end:raise ValueError('推論跨日，不能發布昨日預測')
    version=('preview-' if a.preview else 'official-')+issued.strftime('%Y%m%dT%H%M%S%f')
    payload=dict(schema=1,kind='preview' if a.preview else 'official',version=version,published_at=issued.isoformat(),
        forecasts=reports,histories=histories,calibration=dict(algorithm='log-cqr-nonnegative-v1',alpha=.2,minimum=20),
        model=dict(id=reports[PAIRS[0]]['model'],revision=reports[PAIRS[0]]['model_revision']),data_sha256=fingerprint,
        runtime=dict(packages={p:importlib.metadata.version(p) for p in ('torch','chronos-forecasting','transformers')},
                     code_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                                  ('app/forecast.py','app/forecast_publisher.py','scripts/publish_forecasts.py')}))
    dest=folder/version/'forecast-release.zip';digest=bundle(payload,dest)
    if not a.preview:archive.issue(version,payload)
    if a.activate:ReleaseStore(a.instance/('forecast-preview' if a.preview else 'forecast-releases'),allow_preview=a.preview).install(dest.read_bytes())
    print(json.dumps(dict(bundle=str(dest),sha256=digest,version=version,kind=payload['kind'])),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--instance',type=Path,default=ROOT/'instance')
    p.add_argument('--import-history',type=Path);p.add_argument('--refresh',action='store_true');p.add_argument('--as-of',type=date.fromisoformat)
    p.add_argument('--preview',action='store_true');p.add_argument('--activate',action='store_true');a=p.parse_args()
    with ReleaseStore(a.instance/'forecast-publisher').writer():build(a)


if __name__=='__main__':main()
