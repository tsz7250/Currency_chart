"""安裝已審查的本地包，或同步正式 GitHub Release。"""
import argparse
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.forecast_release import ReleaseStore


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--instance',type=Path,default=ROOT/'instance')
    p.add_argument('--install',type=Path);p.add_argument('--rollback',action='store_true')
    p.add_argument('--repo',default='tsz7250/Currency_chart');a=p.parse_args()
    store=ReleaseStore(a.instance/'forecast-releases',a.repo)
    if a.install:print(store.install(a.install.read_bytes(),allow_rollback=a.rollback))
    else:
        if a.rollback:p.error('--rollback 需要 --install 指定舊版發布包')
        store.sync(force=True)
        if store.sync_error:raise RuntimeError(store.sync_error)
        print(store.current()['version'])


if __name__=='__main__':main()
