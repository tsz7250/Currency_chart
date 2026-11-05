"""將模型工作放到原生執行緒，HTTP 與 SSE 不等待推論。"""
import hashlib
import json
import logging
from pathlib import Path

from gevent.threadpool import ThreadPoolExecutor

from .forecast import ChronosPredictor, MODEL_REVISION, evaluate
from .utils import LRUCache

logger = logging.getLogger(__name__)


class ForecastService:
    def __init__(self, instance_path):
        self.predict = ChronosPredictor(Path(instance_path) / 'models')
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.jobs = {}
        self.cache = LRUCache(capacity=60, ttl_seconds=86400)

    def get(self, pair, entries):
        payload = [(d.isoformat(), r) for d, r in entries]
        key = hashlib.sha256(json.dumps([pair, payload, MODEL_REVISION, 'log-cqr-v1']).encode()).hexdigest()
        # 只在請求執行緒更新 jobs 和快取；背景工作只回傳結果。
        for old_key, future in list(self.jobs.items()):
            if future.done():
                try:
                    value = future.result()
                    value.pop('rows', None)
                    value['status'] = 'ready'
                except Exception:
                    logger.exception('Chronos 預測失敗')
                    value = {'status': 'error', 'message': '預測暫時無法使用，請稍後重試。'}
                self.cache.put(old_key, value, ttl=86400 if value['status'] == 'ready' else 30)
                del self.jobs[old_key]
        cached = self.cache.get(key)
        if cached:
            return cached, 200 if cached['status'] == 'ready' else 503
        if key not in self.jobs and len(self.jobs) < 4:
            self.jobs[key] = self.executor.submit(evaluate, entries, self.predict)
        elif key not in self.jobs:
            return {'status': 'busy', 'message': '預測工作已滿，請稍後重試。'}, 503
        return {'status': 'pending', 'message': '正在準備模型與校準預測區間。'}, 202

    def shutdown(self):
        self.executor.shutdown(wait=False)
