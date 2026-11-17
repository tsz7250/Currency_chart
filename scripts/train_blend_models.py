"""最小訓練路徑：只用既有過去資料訓練融合方向模型，目標一小時內、上限四小時。
regime 模型來自五年公開市場資料；control 模型來自一年 Mastercard 歷史。
排除最後 35 天 holdout 並輸出樣本外評估；模型存 instance/models/ 後主站自動啟用。
由 app 排程在背景子程序呼叫；也可手動執行。"""
import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np

from app import forecast_supervised as s
from app.forecast_blend import get_snapshot, models_fresh
from app.forecast_market import MarketHistory, download_market
from app.forecast_yields import download as download_yields
from scripts.benchmark_forecasts import PAIRS, check_deadline, fetch_year
from scripts.evaluate_forecasts import save

HOLDOUT_DAYS = 35


def partition_rows(rows, holdout_start):
    train = [r for r in rows if r['origin'] < holdout_start]
    holdout = [r for r in rows if r['origin'] >= holdout_start]
    return train, holdout


def regime_dataset(snapshot, treasury, as_of):
    """五年市場資料的 regime 變異特徵列；重用研究腳本的資料構造。"""
    from scripts.research_yield_regimes import market_dataset
    variants, _, _ = market_dataset(snapshot, treasury, as_of)
    return variants['regime']


def evaluate_holdout(holdout, hold_regime, regime, control):
    """樣本外融合評估：兩模型機率平均後套固定三分類計分。"""
    rows = [r for r in holdout if (r['pair'], r['origin'], r['horizon']) in hold_regime]
    if not rows:
        raise ValueError('holdout 無可評估樣本')
    p_regime = s.probabilities(regime, [hold_regime[(r['pair'], r['origin'], r['horizon'])] for r in rows])
    p_control = s.probabilities(control, rows)
    report = s.direction_report(rows, (p_regime + p_control) / 2)
    return dict(samples=len(rows), accuracy=s.average(report, 'accuracy'), macro_f1=s.average(report, 'f1'),
                by_cell={k: dict(accuracy=v['accuracy'], f1=v['f1'], samples=v['samples']) for k, v in report.items()})


def train(as_of, instance):
    deadline = time.time() + 4 * 3600
    holdout_start = as_of - timedelta(days=HOLDOUT_DAYS)
    benchmark = instance / 'forecast-benchmark' / str(as_of)
    fetch_year(benchmark, as_of, deadline)
    histories = {p: json.loads((benchmark / 'history' / f'{p}.json').read_text(encoding='utf-8')) for p in PAIRS}
    snapshot = get_snapshot(instance / 'forecast-blend' / 'market', as_of, download_market, 'market.json')
    treasury = get_snapshot(instance / 'forecast-blend' / 'treasury', as_of, download_yields, 'yields.json')
    market = MarketHistory(snapshot)
    print('DATA_READY', flush=True)
    regime_rows = regime_dataset(snapshot, treasury, as_of)
    regime_train, _ = partition_rows(regime_rows, holdout_start)
    if len(regime_train) < 500:
        raise ValueError('regime 訓練資料不足')
    regime = s.train_classifier('cat4', regime_train)
    print('REGIME_TRAINED', len(regime_train), flush=True)
    control_all = [r for r in s.dataset(histories, market) if r['horizon'] in s.HORIZONS]
    control_train, control_holdout = partition_rows(control_all, holdout_start)
    if len(control_train) < 500:
        raise ValueError('control 訓練資料不足')
    control = s.train_classifier('cat4', control_train)
    print('CONTROL_TRAINED', len(control_train), flush=True)
    hold_regime = {(r['pair'], r['origin'], r['horizon']): r
                   for r in regime_rows if r['origin'] >= holdout_start}
    summary = evaluate_holdout(control_holdout, hold_regime, regime, control)
    folder = instance / 'models'
    folder.mkdir(parents=True, exist_ok=True)
    regime.save_model(str(folder / 'blend-regime.cbm'))
    control.save_model(str(folder / 'blend-control.cbm'))
    save(instance / 'forecast-blend' / 'training-summary.json',
         dict(as_of=str(as_of), holdout_days=HOLDOUT_DAYS, trained_at=time.time(), **summary))
    print('BLEND_TRAINED', json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', default=str(date.today()))
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    instance = ROOT / 'instance'
    if not args.force and models_fresh(instance):
        print('BLEND_MODELS_FRESH', flush=True)
        return
    # 作業系統鎖防同時訓練；程序結束自動釋放。
    lock_path = instance / 'forecast-blend' / 'train.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+b') as lock:
        if sys.platform == 'win32':
            import msvcrt
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                print('BLEND_TRAINING_BUSY', flush=True)
                return
        else:
            import fcntl
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                print('BLEND_TRAINING_BUSY', flush=True)
                return
        if not args.force and models_fresh(instance):
            print('BLEND_MODELS_FRESH', flush=True)
            return
        train(date.fromisoformat(args.as_of), instance)


if __name__ == '__main__':
    main()
