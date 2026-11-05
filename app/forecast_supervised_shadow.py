"""新版影子紀錄；沿用不可覆寫儲存，舊版紀錄及程式不變。"""
import math
import logging
import os
from pathlib import Path
import subprocess
import sys
from .forecast_shadow import ShadowStore

_process = None


def launch_supervised(instance_path):
    global _process
    try:
        folder = Path(instance_path) / 'forecast-supervised-shadow'
        if not (folder/'active.json').exists() or (_process is not None and _process.poll() is None):
            return False
        root = Path(__file__).resolve().parents[1]
        with (folder/'worker.log').open('a',encoding='utf-8') as stream:
            _process = subprocess.Popen([sys.executable,'-u','-X','utf8',str(root/'scripts/shadow_supervised.py'),
                                         '--folder',str(folder)],cwd=root,stdout=stream,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        return True
    except Exception:
        logging.getLogger(__name__).exception('監督式影子工作失敗；主站維持原服務')
        return False


class SupervisedShadowStore(ShadowStore):
    def append(self, run, origin, payload):
        config, _ = self.info(run)
        if config.get('schema_version') == 2:
            if payload.get('schema_version') != 2 or payload.get('versions') != config['models']:
                raise ValueError('影子模型版本不符')
            for pair in payload['pairs'].values():
                if config['direction_eligible']:
                    if set(pair.get('direction', {})) != {'7','14','30'}:
                        raise ValueError('方向步長不完整')
                    for p in pair['direction'].values():
                        if len(p) != 3 or any(not math.isfinite(v) or v < 0 or v > 1 for v in p) or not math.isclose(sum(p),1,abs_tol=1e-8):
                            raise ValueError('無效方向機率')
                if config['price_eligible']:
                    q = pair.get('quantiles', [])
                    if len(q) != 30 or any(len(v)!=3 or not all(math.isfinite(x) for x in v)
                        or not 0 < v[0] <= v[1] <= v[2] for v in q):
                        raise ValueError('無效價格分位數')
        return super().append(run,origin,payload)
