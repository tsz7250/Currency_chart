"""獨立影子工作程序：新資料預測、不可覆寫保存、到期評分。"""
import argparse
import hashlib
import importlib.metadata
import io
import json
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.alerts import now_taipei
from app.forecast import evaluate, prepare_entries
from app.forecast_market import MarketHistory, download_market
from app.forecast_research import HORIZONS, block_uncertainty, metrics, new_states, replay, replacement_checks
from app.forecast_shadow import ShadowStore
from scripts.benchmark_forecasts import MODELS, Predictor
from scripts.evaluate_forecasts import save
from scripts.research_forecasts import predictions


def activate(folder, research):
    folder.mkdir(parents=True, exist_ok=True)
    config = json.loads((research / 'candidate-lock.json').read_text(encoding='utf-8'))
    if json.loads((research / 'manifest.json').read_text())['status'] != 'complete':
        raise ValueError('研究未完成，不能啟動影子驗證')
    config['baseline_code_sha256'] = hashlib.sha256((ROOT / 'app/forecast.py').read_bytes()).hexdigest()
    config['runtime_files'] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
        ('scripts/research_forecasts.py', 'scripts/benchmark_forecasts.py', 'scripts/shadow_forecasts.py', 'app/forecast_shadow.py')}
    config['packages'] = {name: importlib.metadata.version(name) for name in ('torch', 'chronos-forecasting', 'transformers')}
    store = ShadowStore(folder / 'shadow.sqlite3')
    run = store.register(config, now_taipei().date())
    active = {'run': run, 'research': str(research.resolve())}
    path = folder / 'active.json'
    if path.exists() and json.loads(path.read_text()) != active:
        raise ValueError('已有不同影子候選；請使用新目錄啟動新的驗證期')
    save(path, active)
    return run


def refresh_history(folder, research, pairs, today):
    from app.mastercard_scraper import MastercardScraper
    scraper = MastercardScraper(str(ROOT / 'mastercard_cookies.json'))
    all_data = {}
    for pair in pairs:
        path = folder / 'history' / f'{pair}.json'
        source = path if path.exists() else research / 'history' / f'{pair}.json'
        data = json.loads(source.read_text(encoding='utf-8'))
        day = date.fromisoformat(max(data)) + timedelta(days=1)
        while day <= today:
            with redirect_stdout(io.StringIO()):
                result = scraper.get_exchange_rate(datetime.combine(day, datetime.min.time()), *pair.split('-'))
            if not result or 'conversionRate' not in result.get('data', {}):
                raise RuntimeError(f'{pair} {day}: 無有效新報價；稍後重試，不補造預測')
            payload = result['data']
            rate = float(payload['conversionRate'])
            prepare_entries([str(day)], [rate])
            data[str(day)] = {'rate': rate, 'query_date': str(day), 'fx_date': payload.get('fxDate'),
                              'fetched_at': datetime.now(timezone.utc).isoformat(), 'source': 'Mastercard'}
            save(path, data)
            day += timedelta(days=1)
        all_data[pair] = data
    return all_data


def assess(store, run, data, today):
    config, start = store.info(run)
    records = store.records(run)
    output = {'status': 'collecting', 'run': run, 'origins': len(records),
              'origin_end': str(start + timedelta(days=89)), 'matures_on': str(start + timedelta(days=119)),
              'eligible_for_production': False}
    if today < start + timedelta(days=119) or len(records) != 90:
        return output
    groups = {name: {} for name in ('candidate', 'baseline', 'flat')}
    for pair in config['pairs']:
        lookup = {date.fromisoformat(d): v['rate'] for d, v in data[pair].items()}
        for h in HORIZONS:
            rows = {name: [] for name in groups}
            for origin, payload in records:
                for name in ('candidate', 'baseline'):
                    point = payload['pairs'][pair][name][h - 1]
                    target = origin + timedelta(days=h)
                    if target not in lookup:
                        return dict(output, status='awaiting_actuals')
                    rows[name].append({'origin': origin, 'target': target, 'median': point['median'],
                        'low': point['low'], 'high': point['high'], 'sigma': point.get('sigma', 1)})
                base = payload['pairs'][pair]['last_rate']
                rows['flat'].append({'origin': origin, 'target': origin + timedelta(days=h),
                                     'median': base, 'low': base, 'high': base, 'sigma': 1})
            for name in groups:
                groups[name][f'{pair}/{h}'] = metrics(rows[name], lookup)
                groups[name][f'{pair}/{h}']['uncertainty'] = block_uncertainty(rows[name], lookup)
    checks = replacement_checks(groups['candidate'], groups['baseline'], groups['flat'])
    return dict(output, status='complete', checks=checks, metrics=groups,
                eligible_for_production=all(checks.values()), automatic_promotion=False)


def daily(folder):
    active = json.loads((folder / 'active.json').read_text())
    research = Path(active['research'])
    store = ShadowStore(folder / 'shadow.sqlite3')
    config, start = store.info(active['run'])
    previous_records = store.records(active['run'])
    for filename, key in [('forecast_research.py', 'research_code_sha256'), ('forecast_market.py', 'market_code_sha256'), ('forecast.py', 'baseline_code_sha256')]:
        if hashlib.sha256((ROOT / 'app' / filename).read_bytes()).hexdigest() != config[key]:
            raise ValueError('影子推論程式版本已變更，必須開新驗證期')
    for name, digest in config['runtime_files'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'影子執行版本已改變：{name}')
    for name, version in config['packages'].items():
        if importlib.metadata.version(name) != version:
            raise ValueError(f'影子套件版本已改變：{name}')
    today = now_taipei().date()
    if store.contains(active['run'], today):
        print('ALREADY_SAVED', today)
        return
    data = refresh_history(folder, research, config['pairs'], today)
    if today < start + timedelta(days=90):
        market_data = download_market(folder / 'market', today)
        market = MarketHistory(market_data)
        deadline = time.time() + 3600
        predictor = Predictor(MODELS[2], deadline)
        pairs = {}
        for pair, raw in data.items():
            entries = sorted((date.fromisoformat(d), v['rate']) for d, v in raw.items())[-365:]
            if entries[-1][0] != today:
                raise ValueError('基準日報價尚未取得')
            origins, quantiles = predictions(entries, pair, predictor, market, config['center'] == 'covariates')
            states = new_states()
            if previous_records:
                saved = previous_records[-1][1]['pairs'][pair]['calibration_state']
                states = {int(h): state for h, state in saved.items()}
                for state in states.values():
                    for row in state['known'] + state['pending']:
                        row['origin'] = date.fromisoformat(row['origin'])
                        row['target'] = date.fromisoformat(row['target'])
                origins, quantiles = origins[-1:], quantiles[-1:]
            rows = replay(entries, origins, quantiles, config['center'], config['interval'], states)
            pairs[pair] = {'candidate': [r for r in rows if r['origin'] == today],
                           'calibration_state': states, 'last_rate': entries[-1][1]}
        del predictor
        import gc, torch
        gc.collect()
        torch.cuda.empty_cache()
        baseline = Predictor(MODELS[0], deadline)
        for pair, raw in data.items():
            entries = sorted((date.fromisoformat(d), v['rate']) for d, v in raw.items())
            report = evaluate(entries, baseline)
            pairs[pair]['baseline'] = [{'median': p['median'], 'low': p['low80'], 'high': p['high80']} for p in report['points']]
        payload = {'issued_at': now_taipei().isoformat(), 'config': config,
                   'market_snapshot': market_data, 'mastercard_snapshot': data, 'pairs': pairs}
        if now_taipei().date() != today:
            raise ValueError('推論跨日，不得保存昨日預測')
        print('SAVED', store.append(active['run'], today, payload), today)
    report = assess(store, active['run'], data, today)
    save(folder / 'status.json', report)
    if report['status'] == 'complete':
        from scripts.research_forecasts import write_once
        if not (folder / 'final-result.json').exists():
            write_once(folder / 'final-result.json', report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', default=str(ROOT / 'instance/forecast-shadow'))
    parser.add_argument('--activate', type=Path)
    args = parser.parse_args()
    folder = Path(args.folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    # OS 鎖隨程序結束釋放，避免多個主站 worker 重複下載、推論或破壞快照。
    lock = (folder / 'worker.lock').open('a+b')
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    try:
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('WORKER_ALREADY_RUNNING')
        return
    if args.activate:
        print('ACTIVATED', activate(folder, args.activate))
    try:
        daily(folder)
    except Exception as error:
        save(folder / 'operational-status.json', {'status': 'failed', 'at': now_taipei().isoformat(), 'error': str(error)})
        raise


if __name__ == '__main__':
    main()
