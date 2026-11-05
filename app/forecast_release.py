"""跨部署只讀發布包；完整驗證後切換指標。"""
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
import uuid
import zipfile
import sqlite3
from contextlib import contextmanager
from datetime import date,datetime,timedelta
from .forecast_archive import encoded

PAIRS=('TWD-HKD','USD-TWD','JPY-TWD','EUR-USD','AUD-USD','GBP-JPY')
MAX_BYTES=100*1024*1024


def validate(payload):
    if payload.get('schema')!=1 or payload.get('kind') not in ('official','preview'):raise ValueError('未知發布格式')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',payload['version']):raise ValueError('無效版本')
    if set(payload['forecasts'])!=set(PAIRS):raise ValueError('發布幣對不完整')
    issued=datetime.fromisoformat(payload['published_at'])
    if issued.tzinfo is None:raise ValueError('發布時間缺少時區')
    baselines=set()
    for report in payload['forecasts'].values():
        baseline=date.fromisoformat(report['as_of']);baselines.add(baseline)
        if baseline>issued.date():raise ValueError('發布資料日期晚於發布時間')
        if not all(str(h) in report['backtest'] for h in (7,14,30)):raise ValueError('評分步長不完整')
        if len(report['points'])!=30:raise ValueError('發布需包含 30 天')
        for h,p in enumerate(report['points'],1):
            if date.fromisoformat(p['date'])!=baseline+timedelta(days=h):raise ValueError('目標日期不符')
            values=[p['median']]+([p['low80'],p['high80']] if p['low80'] is not None else [])
            import math
            if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in values):raise ValueError('無效預測價格')
            if (p['low80'] is None)!=(p['high80'] is None) or (p['low80'] is not None and not p['low80']<=p['median']<=p['high80']):raise ValueError('無效區間')
    if len(baselines)!=1:raise ValueError('六組資料基準日不一致')


def bundle(payload,path):
    validate(payload);content=encoded(payload)
    manifest=dict(schema=1,version=payload['version'],sha256=hashlib.sha256(content).hexdigest())
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('manifest.json',encoded(manifest));z.writestr('release.json',content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReleaseStore:
    def __init__(self,folder,repo='',allow_preview=False):
        self.folder=Path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.repo=repo;self.allow_preview=allow_preview;self.lock=threading.Lock();self.last_check=0;self.sync_error=None

    @contextmanager
    def writer(self):
        db=sqlite3.connect(self.folder/'writer.sqlite3',timeout=30)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield
            db.commit()
        finally:db.close()

    def install(self,content,activate=True,allow_rollback=False):
        if len(content)>MAX_BYTES:raise ValueError('發布包過大')
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            if len(z.infolist())!=2 or set(z.namelist())!={'manifest.json','release.json'}:raise ValueError('發布包含非允許檔案')
            if sum(i.file_size for i in z.infolist())>MAX_BYTES:raise ValueError('解壓資料過大')
            manifest=json.loads(z.read('manifest.json'));data=z.read('release.json')
        if hashlib.sha256(data).hexdigest()!=manifest['sha256']:raise ValueError('發布雜湊不符')
        payload=json.loads(data);validate(payload)
        if manifest['version']!=payload['version']:raise ValueError('版本不一致')
        if payload['kind']!='official' and not self.allow_preview:raise ValueError('預覽包不得成為正式預測')
        with self.lock,self.writer():
            current=self.current()
            if activate and current and not allow_rollback and datetime.fromisoformat(payload['published_at'])<datetime.fromisoformat(current['published_at']):
                raise ValueError('不可自動回退正式版本')
            target=self.folder/f"{payload['version']}.json"
            if target.exists():
                if target.read_bytes()!=data:raise ValueError('同版本內容衝突')
            else:
                temp=self.folder/f'download-{uuid.uuid4().hex}.tmp'
                temp.write_bytes(data);os.replace(temp,target)
            if activate:
                temp=self.folder/f'active-{uuid.uuid4().hex}.tmp'
                temp.write_bytes(encoded(dict(version=payload['version'],sha256=manifest['sha256'])))
                os.replace(temp,self.folder/'active.json')
        return payload['version']

    def current(self):
        path=self.folder/'active.json'
        if not path.exists():return None
        active=json.loads(path.read_bytes());name=active['version']
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,100}',name):raise ValueError('無效版本指標')
        content=(self.folder/f'{name}.json').read_bytes()
        if hashlib.sha256(content).hexdigest()!=active['sha256']:raise ValueError('本地版本損壞')
        return json.loads(content)

    def sync(self,force=False):
        if not self.repo or (not force and time.monotonic()-self.last_check<300):return
        self.last_check=time.monotonic()
        try:
            import requests
            if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',self.repo):raise ValueError('無效發布倉庫')
            response=requests.get(f'https://api.github.com/repos/{self.repo}/releases/tags/forecast-current',timeout=20)
            response.raise_for_status();release=response.json()
            assets=[a for a in release.get('assets',[]) if a['name']=='forecast-release.zip']
            if len(assets)!=1:raise ValueError('找不到正式發布包')
            url=assets[0]['browser_download_url']
            if not url.startswith(f'https://github.com/{self.repo}/releases/download/'):raise ValueError('發布下載來源不符')
            with requests.get(url,timeout=60,stream=True) as result:
                result.raise_for_status();parts=[];size=0
                for part in result.iter_content(1024*1024):
                    size+=len(part)
                    if size>MAX_BYTES:raise ValueError('發布包過大')
                    parts.append(part)
            self.install(b''.join(parts));self.sync_error=None
        except Exception as error:
            self.sync_error=str(error);logging.getLogger(__name__).warning('發布同步失敗：%s',error)

    def get(self,pair,horizon,today):
        payload=self.current()
        if pair not in PAIRS:return dict(status='unsupported',message='此幣對尚未納入正式預測。'),200
        if payload is None:return dict(status='pending',message='尚未取得完整正式預測版本。'),202
        import copy
        report=copy.deepcopy(payload['forecasts'][pair]);age=(today-date.fromisoformat(report['as_of'])).days
        if age<0 or age>7:return dict(status='stale',message='正式預測已過期，等待新發布版本。',release_version=payload['version']),200
        report.update(status='ready',release_version=payload['version'],published_at=payload['published_at'],
            data_age_days=age,sync_state='offline' if self.sync_error else ('delayed' if age>1 else 'current'),
            buy_currency=pair.split('-')[0],sell_currency=pair.split('-')[1],horizon=horizon,
            experimental=report['backtest'][str(horizon)]['calibrated']['samples']<20)
        report['points']=report['points'][:horizon];report['backtest']=report['backtest'][str(horizon)]
        return report,200
