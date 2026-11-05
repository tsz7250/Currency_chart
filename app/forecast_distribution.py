"""下載正式包與發布端工作，均不阻塞 HTTP。"""
import os
from pathlib import Path
import subprocess
import sys
import logging
from gevent.threadpool import ThreadPoolExecutor
from .forecast_release import ReleaseStore


class DistributedForecastService:
    def __init__(self,instance):
        self.instance=Path(instance);self.store=ReleaseStore(self.instance/'forecast-releases',os.environ.get('FORECAST_RELEASE_REPO','tsz7250/Currency_chart'))
        self.executor=ThreadPoolExecutor(max_workers=1);self.future=None;self.publisher=None

    def maintain(self):
        if self.future is None or self.future.done():self.future=self.executor.submit(self.store.sync)
        if os.environ.get('FORECAST_ROLE')=='publisher' and (self.publisher is None or self.publisher.poll() is not None):
            root=Path(__file__).resolve().parents[1]
            folder=self.instance/'forecast-publisher';folder.mkdir(parents=True,exist_ok=True)
            try:
                with (folder/'worker.log').open('a',encoding='utf-8') as log:
                    self.publisher=subprocess.Popen([sys.executable,'-u','-X','utf8',str(root/'scripts/publish_forecasts.py'),
                        '--instance',str(self.instance),'--refresh','--activate'],cwd=root,stdout=log,stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            except Exception:logging.getLogger(__name__).exception('發布工作啟動失敗；保留既有版本')

    def get_release(self,pair,horizon,today):
        if self.future is None or self.future.done():self.future=self.executor.submit(self.store.sync)
        try:return self.store.get(pair,horizon,today)
        except Exception:
            return dict(status='error',message='正式預測版本無法讀取，請重新同步。'),503

    def shutdown(self):self.executor.shutdown(wait=False)
