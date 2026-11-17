"""依序重跑方向候選研究鏈；每支 8 小時上限由腳本自管，失敗即停。
跳過 nested 與 evaluation_v2：兩者只服務已淘汰的區間候選。
bolt_intervals 以 raw-only 模式執行（來源缺 price.current，下游只需 raw 分位數）。"""
import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', default=str(date.today()))
    args = parser.parse_args()
    day = args.as_of
    def run(name):
        return ROOT / 'instance' / name / f'run-{day}'
    benchmark = ROOT / 'instance' / 'forecast-benchmark' / day
    py = [sys.executable, '-X', 'utf8']
    plan = [
        ('benchmark_forecasts', [*py, 'scripts/benchmark_forecasts.py', '--as-of', day]),
        ('research_forecasts', [*py, 'scripts/research_forecasts.py', '--as-of', day,
            '--history', str(benchmark), '--output', str(run('forecast-research'))]),
        ('research_supervised', [*py, 'scripts/research_supervised.py',
            '--source', str(run('forecast-research')), '--output', str(run('forecast-supervised'))]),
        ('research_bolt_intervals', [*py, 'scripts/research_bolt_intervals.py',
            '--source', str(run('forecast-supervised')), '--output', str(run('forecast-bolt-intervals'))]),
        ('research_cross_pair', [*py, 'scripts/research_cross_pair.py',
            '--history-source', str(run('forecast-research')), '--baseline-source', str(run('forecast-supervised')),
            '--bolt-source', str(run('forecast-bolt-intervals')), '--output', str(run('forecast-cross-pair'))]),
        ('research_yield_regimes', [*py, 'scripts/research_yield_regimes.py',
            '--history-source', str(run('forecast-research')), '--output', str(run('forecast-yield-regimes'))]),
        ('research_yield_transfer', [*py, 'scripts/research_yield_transfer.py',
            '--source', str(run('forecast-yield-regimes')), '--history-source', str(run('forecast-research')),
            '--reference-source', str(run('forecast-cross-pair')), '--bolt-source', str(run('forecast-bolt-intervals')),
            '--output', str(run('forecast-yield-transfer'))]),
        ('research_direction_blend', [*py, 'scripts/research_direction_blend.py',
            '--source', str(run('forecast-yield-transfer')), '--output', str(run('forecast-direction-blend'))]),
    ]
    for name, command in plan:
        print(f'=== {name} 開始', flush=True)
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            print(f'=== {name} 失敗（exit {result.returncode}），鏈已停止；修復後重跑會續用未完成步驟的既有輸出。', flush=True)
            raise SystemExit(result.returncode)
        print(f'=== {name} 完成', flush=True)
    print('鏈完成。接手動步驟：把 last-fold 模型複製到 instance/models/blend-regime.cbm 與 blend-control.cbm，見 FORECAST_RESEARCH.md「模型部署」。', flush=True)


if __name__ == '__main__':
    main()
