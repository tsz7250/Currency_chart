"""取得 Mastercard 歷史報價，重跑 Chronos 與區間校準評測。"""
import argparse
import csv
import json
import math
import sys
import time
import io
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.alerts import now_taipei
from app.forecast import ChronosPredictor, evaluate, prepare_entries
from app.mastercard_scraper import MastercardScraper


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def fetch(pair, path, refresh):
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    scraper = MastercardScraper(str(ROOT / 'mastercard_cookies.json'))
    buy, sell = pair.split('-')
    end = now_taipei().date()
    refreshed = False
    for offset in range(179, -1, -1):
        day = end - timedelta(days=offset)
        if day.isoformat() in data:
            continue
        started = time.monotonic()
        result = scraper.get_exchange_rate(datetime.combine(day, datetime.min.time()), buy, sell)
        if isinstance(result, dict) and result.get('error') == 'rate_limited' and refresh and not refreshed:
            from app.cookie_fetcher import CookieFetcher
            refreshed = True
            # CookieFetcher 的既有日誌含 Cookie 片段，評測不輸出這些內容。
            with redirect_stdout(io.StringIO()):
                success = CookieFetcher(str(ROOT / 'mastercard_cookies.json')).fetch_and_save(headless=False, wait_time=15)
            if success:
                scraper.load_cookies()
                result = scraper.get_exchange_rate(datetime.combine(day, datetime.min.time()), buy, sell)
        if isinstance(result, dict) and result.get('error') == 'rate_limited':
            raise RuntimeError(f'{pair}: HTTP 403，停止抓取；已保存成功資料')
        if result and 'conversionRate' in result.get('data', {}):
            rate = float(result['data']['conversionRate'])
            if math.isfinite(rate) and rate > 0:
                data[day.isoformat()] = {'rate': rate, 'updated': now_taipei().isoformat()}
                save(path, data)
        time.sleep(max(0, 0.5 - (time.monotonic() - started)))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--refresh-cookies', action='store_true')
    args = parser.parse_args()
    destination = ROOT / 'instance' / 'forecast-evaluation'
    destination.mkdir(parents=True, exist_ok=True)
    predict = ChronosPredictor(ROOT / 'instance' / 'models')
    reports, failures = {}, {}
    for pair in ('TWD-HKD', 'USD-TWD', 'JPY-TWD', 'EUR-USD'):
        path = ROOT / 'TWD-HKD_180d.json' if pair == 'TWD-HKD' else destination / f'{pair}.json'
        try:
            if args.fetch and pair != 'TWD-HKD':
                data = fetch(pair, path, args.refresh_cookies)
            else:
                data = json.loads(path.read_text(encoding='utf-8'))
            entries = prepare_entries(list(data), [v['rate'] for v in data.values()])
            report = evaluate(entries, predict)
            save(destination / f'{pair}-results.json', report)
            reports[pair] = report
            print(pair, {h: report['backtest'][h]['calibrated'] for h in ('7', '14', '30')}, flush=True)
        except Exception as error:
            failures[pair] = str(error)
            print(pair, 'FAILED', str(error), flush=True)
    save(destination / 'failures.json', failures)
    with (destination / 'summary.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['pair', 'horizon', 'model', 'samples', 'mae', 'coverage80', 'mean_width', 'interval_score80', 'improvement_vs_flat'])
        for pair, report in reports.items():
            for h in ('7', '14', '30'):
                groups = report['backtest'][h]
                for model, metrics in groups.items():
                    if model == 'classification':
                        continue
                    flat = groups['flat']['mae']
                    improvement = 1 - metrics['mae'] / flat if flat and metrics['mae'] is not None else None
                    writer.writerow([pair, h, model, *(metrics[k] for k in ('samples', 'mae', 'coverage80', 'mean_width', 'interval_score80')), improvement])
    print('REPORT', destination / 'summary.csv', flush=True)


if __name__ == '__main__':
    main()
