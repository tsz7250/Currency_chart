"""一年資料、固定開發／封存切分的本機模型評測。"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import sys
import time
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.forecast import classification, measurement, score, summary, widen
from scripts.evaluate_forecasts import save

PAIRS = ('TWD-HKD', 'USD-TWD', 'JPY-TWD', 'EUR-USD', 'AUD-USD', 'GBP-JPY')
MODELS = ('amazon/chronos-bolt-small', 'amazon/chronos-bolt-base', 'amazon/chronos-2')
WINDOWS = (90, 180, 270)
HORIZONS = (7, 14, 30)
REVISIONS = ('772f3d25d38aec6d914c8949dab4462e2d46f5d8',
             '5d9f166d69f47aef3401367a7b842e78fe97b121',
             '29ec3766d36d6f73f0696f85560a422f50e8498c')


class Predictor:
    def __init__(self, model, deadline):
        os.environ['USE_TF'] = '0'
        os.environ['USE_FLAX'] = '0'
        os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        import torch
        from chronos import ChronosBoltPipeline, Chronos2Pipeline
        self.torch = torch
        self.deadline = deadline
        self.chronos2 = model == MODELS[2]
        torch.set_num_threads(4)
        torch.manual_seed(0)
        cls = Chronos2Pipeline if self.chronos2 else ChronosBoltPipeline
        self.pipeline = cls.from_pretrained(model, revision=REVISIONS[MODELS.index(model)],
            device_map='cuda', torch_dtype=torch.float32, cache_dir=str(ROOT / 'instance/models'))

    def __call__(self, inputs):
        output = []
        for start in range(0, len(inputs), 8):
            check_deadline(self.deadline)
            tensors = [self.torch.tensor(x, dtype=self.torch.float32) for x in inputs[start:start + 8]]
            if self.chronos2:
                q, _ = self.pipeline.predict_quantiles([{'target': x} for x in tensors],
                    prediction_length=30, quantile_levels=[.1, .5, .9], cross_learning=False,
                    batch_size=8)
                output.extend(x[0].cpu().tolist() for x in q)
            else:
                q, _ = self.pipeline.predict_quantiles(tensors, prediction_length=30,
                    quantile_levels=[.1, .5, .9])
                output.extend(q.cpu().tolist())
        return output


def inputs_for(entries, start, stop, window):
    lookup = dict(entries)
    origins, inputs = [], []
    for day, _ in entries:
        if day < start + timedelta(days=89) or day >= stop:
            continue
        first = max(start, day - timedelta(days=window - 1))
        values = [lookup.get(first + timedelta(days=i), float('nan'))
                  for i in range((day - first).days + 1)]
        if sum(math.isfinite(x) for x in values[-90:]) >= 40:
            origins.append(day)
            inputs.append(values)
    return origins, inputs


def evaluate_candidate(entries, predict, start, holdout, end, window, phase):
    # 開發期連輸入也裁掉封存資料；測試期只在鎖定選模後呼叫。
    stop = holdout if phase == 'development' else end + timedelta(days=1)
    entries = [(d, r) for d, r in entries if start <= d < stop]
    lookup = dict(entries)
    origins, inputs = inputs_for(entries, start, stop, window)
    context_lengths = {str(day): len(values) for day, values in zip(origins, inputs)}
    forecasts = predict(inputs)
    if len(forecasts) != len(origins):
        raise ValueError('模型輸出筆數不符')
    records = {h: [] for h in HORIZONS}
    rows = {h: {name: [] for name in ('raw', 'cumulative', 'rolling90', 'flat')} for h in HORIZONS}
    for origin, forecast in zip(origins, forecasts):
        if len(forecast) != 30:
            raise ValueError('模型預測期間不符')
        for h in HORIZONS:
            target = origin + timedelta(days=h)
            actual = lookup.get(target)
            low, median, high = map(float, forecast[h - 1])
            if not all(math.isfinite(v) and v > 0 for v in (low, median, high)) or not low <= median <= high:
                raise ValueError('模型回傳無效區間')
            if actual is None:
                continue
            include = phase == 'development' or origin >= holdout
            if include:
                rows[h]['raw'].append(measurement(origin, target, actual, low, median, high))
                base = lookup[origin]
                rows[h]['flat'].append(measurement(origin, target, actual, base, base, base))
                for mode in ('cumulative', 'rolling90'):
                    known = sorted(r['score'] for r in records[h] if r['target'] <= origin and
                        (mode == 'cumulative' or r['target'] >= origin - timedelta(days=89)))
                    if len(known) >= 20:
                        q = known[math.ceil((len(known) + 1) * .8) - 1]
                        lo, hi = widen(low, high, q)
                        rows[h][mode].append(measurement(origin, target, actual, lo, median, hi))
            records[h].append({'target': target, 'score': score(low, high, actual)})
    report = {}
    for h, groups in rows.items():
        report[str(h)] = {}
        # 校準模式只在共同有效起點比較，不以缺少難樣本取得優勢。
        common = set(r['origin'] for r in groups['cumulative']) & set(r['origin'] for r in groups['rolling90'])
        for name, values in groups.items():
            if name in ('cumulative', 'rolling90'):
                values = [r for r in values if r['origin'] in common]
            metrics = summary(values)
            actual_mean = sum(r['actual'] for r in values) / len(values) if values else None
            metrics['normalized_mae'] = metrics['mae'] / actual_mean if actual_mean else None
            metrics['normalized_interval_score80'] = metrics['interval_score80'] / actual_mean if actual_mean else None
            metrics['classification'] = classification(values, lookup)
            metrics['origins'] = [r['origin'] for r in values]
            lengths = [context_lengths[r['origin']] for r in values]
            metrics['input_context'] = {'min_days': min(lengths) if lengths else None,
                'max_days': max(lengths) if lengths else None,
                'full_window_samples': sum(n == window for n in lengths)}
            if name == 'flat':
                for key in ('coverage80', 'mean_width', 'interval_score80', 'normalized_interval_score80'):
                    metrics.pop(key)
            report[str(h)][name] = metrics
    lengths = [len(x) for d, x in zip(origins, inputs) if phase == 'development' or d >= holdout]
    return {'metrics': report, 'input_lengths': {'min': min(lengths), 'max': max(lengths),
            'full_window_origins': sum(n == window for n in lengths), 'origins': len(lengths)},
            'window_max_days': window, 'phase': phase}


def average(reports, group, metric):
    values = [r['metrics'][str(h)][group][metric] for r in reports.values() for h in HORIZONS]
    if not values or any(v is None for v in values):
        raise ValueError(f'{group}/{metric}: 評測樣本不足')
    return sum(values) / len(values)


def export_csv(destination):
    fields = ['phase', 'candidate', 'pair', 'horizon', 'group', 'samples', 'mae', 'normalized_mae',
              'coverage80', 'mean_width', 'interval_score80', 'accuracy', 'precision', 'recall', 'f1']
    with (destination / 'metrics.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for phase in ('development', 'holdout'):
            for path in sorted((destination / phase).glob('*/*.json')):
                report = json.loads(path.read_text(encoding='utf-8'))
                for h, groups in report['metrics'].items():
                    for group, metrics in groups.items():
                        row = {key: metrics.get(key) for key in fields[5:11]}
                        row.update({key: metrics['classification'][key] for key in fields[11:]})
                        writer.writerow(dict(phase=phase, candidate=path.parent.name, pair=path.stem,
                                             horizon=h, group=group, **row))


def benchmark(destination, end, manifest):
    import gc
    import importlib.metadata
    import torch
    start = date.fromisoformat(manifest['start'])
    holdout = date.fromisoformat(manifest['holdout_start'])
    deadline = manifest['deadline']
    data = {}
    hashes = {}
    for pair in PAIRS:
        path = destination / 'history' / f'{pair}.json'
        hashes[pair] = hashlib.sha256(path.read_bytes()).hexdigest()
        raw = json.loads(path.read_text(encoding='utf-8'))
        status_path = destination / 'history' / f'{pair}-status.json'
        statuses = json.loads(status_path.read_text(encoding='utf-8'))
        unresolved = [str(start + timedelta(days=i)) for i in range(365)
                      if str(start + timedelta(days=i)) not in raw and
                      statuses.get(str(start + timedelta(days=i))) != 'no_quote']
        if unresolved:
            raise ValueError(f'{pair}: 仍有 {len(unresolved)} 天未確認抓取結果')
        data[pair] = sorted((date.fromisoformat(d), v['rate']) for d, v in raw.items())
    manifest.update(revisions=dict(zip(MODELS, REVISIONS)), data_sha256=hashes,
                    device='cuda', dtype='float32', seed=0, status='development',
                    gpu=torch.cuda.get_device_name(0), torch_version=torch.__version__,
                    chronos_version=importlib.metadata.version('chronos-forecasting'),
                    benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    save(destination / 'manifest.json', manifest)
    candidates = {}
    for model in MODELS:
        check_deadline(deadline)
        predict = Predictor(model, deadline)
        for window in WINDOWS:
            key = f'{model.split("/")[-1]}-{window}'
            reports = {}
            for pair in PAIRS[:4]:
                check_deadline(deadline)
                report = evaluate_candidate(data[pair], predict, start, holdout, end, window, 'development')
                reports[pair] = report
                save(destination / 'development' / key / f'{pair}.json', report)
                print('DEV', key, pair, flush=True)
            candidates[key] = {'model': model, 'window': window,
                'normalized_mae': average(reports, 'raw', 'normalized_mae'),
                'calibration': min(('cumulative', 'rolling90'), key=lambda mode:
                    average(reports, mode, 'normalized_interval_score80')),
                'reports': reports}
        del predict
        gc.collect()
        torch.cuda.empty_cache()
    ranking = sorted(candidates, key=lambda key: candidates[key]['normalized_mae'])
    selected = ranking[0]
    # 主站目前以 >= end - 90 days 取值，實際最多 91 個日曆點。
    # 對照保留原行為，避免把修正窗口邊界冒稱模型提升。
    baseline = 'current-bolt-small-91'
    candidates[baseline] = {'model': MODELS[0], 'window': 91, 'calibration': 'cumulative'}
    lock = {'selected': selected, 'baseline': baseline, 'locked_at': datetime.now().isoformat(),
            'baseline_note': '現用主站 90 天設定含首尾，實際最多 91 個日曆點；保留原行為比較',
            'ranking': [{k: v for k, v in candidates[key].items() if k != 'reports'} | {'key': key} for key in ranking],
            'data_sha256': hashes, 'holdout_start': str(holdout)}
    # 寫下鎖定結果後才評分封存期與兩組新幣對。
    save(destination / 'selection-lock.json', lock)
    print('LOCKED', selected, flush=True)
    tests = {}
    for key in dict.fromkeys((selected, baseline)):
        config = candidates[key]
        predict = Predictor(config['model'], deadline)
        tests[key] = {}
        for pair in PAIRS:
            check_deadline(deadline)
            report = evaluate_candidate(data[pair], predict, start, holdout, end, config['window'], 'holdout')
            tests[key][pair] = report
            save(destination / 'holdout' / key / f'{pair}.json', report)
            print('TEST', key, pair, flush=True)
        del predict
        gc.collect()
        torch.cuda.empty_cache()
    chosen, current = tests[selected], tests[baseline]
    for pair in PAIRS:
        for h in HORIZONS:
            if chosen[pair]['metrics'][str(h)]['raw']['origins'] != current[pair]['metrics'][str(h)]['raw']['origins']:
                raise ValueError('模型比較起點不一致')
    core = lambda reports: {p: reports[p] for p in PAIRS[:4]}
    extra = lambda reports: {p: reports[p] for p in PAIRS[4:]}
    chosen_error = average(core(chosen), 'raw', 'normalized_mae')
    base_error = average(core(current), 'raw', 'normalized_mae')
    flat_error = average(core(chosen), 'flat', 'normalized_mae')
    checks = {'at_least_5_percent_better': chosen_error <= base_error * .95,
              'better_than_flat': chosen_error < flat_error,
              'no_cell_over_10_percent_worse': all(chosen[p]['metrics'][str(h)]['raw']['mae'] <=
                  current[p]['metrics'][str(h)]['raw']['mae'] * 1.1 for p in PAIRS[:4] for h in HORIZONS),
              'extra_pairs_no_worse': average(extra(chosen), 'raw', 'normalized_mae') <=
                  average(extra(current), 'raw', 'normalized_mae')}
    result = {'selected': selected, 'baseline': baseline, 'checks': checks,
              'eligible_for_replacement': all(checks.values()),
              'normalized_mae': chosen_error, 'baseline_normalized_mae': base_error,
              'flat_normalized_mae': flat_error, 'improvement': 1 - chosen_error / base_error,
              'calibration': candidates[selected]['calibration'],
              'finished': datetime.now().isoformat(), 'elapsed_seconds': time.time() - manifest['started']}
    result['direction_scores'] = {}
    for key, reports in tests.items():
        result['direction_scores'][key] = {}
        for h in HORIZONS:
            scores = [reports[p]['metrics'][str(h)]['raw']['classification'] for p in PAIRS[:4]]
            result['direction_scores'][key][str(h)] = {
                'samples': sum(v['samples'] for v in scores),
                **{metric: sum(v[metric] for v in scores) / 4
                   for metric in ('accuracy', 'precision', 'recall', 'f1')}}
    save(destination / 'result.json', result)
    export_csv(destination)
    manifest['status'] = 'complete'
    save(destination / 'manifest.json', manifest)
    print('RESULT', json.dumps(result), flush=True)


def check_deadline(deadline):
    if time.time() >= deadline:
        raise TimeoutError('已達本次 8 小時評測上限')


def fetch_year(destination, end, deadline):
    from app.mastercard_scraper import MastercardScraper
    scraper = MastercardScraper(str(ROOT / 'mastercard_cookies.json'))
    refreshed = False
    start = end - timedelta(days=364)
    for pair in PAIRS:
        path = destination / 'history' / f'{pair}.json'
        seed = ROOT / 'TWD-HKD_180d.json' if pair == 'TWD-HKD' else ROOT / 'instance/forecast-evaluation' / f'{pair}.json'
        source = path if path.exists() else seed
        data = json.loads(source.read_text(encoding='utf-8')) if source.exists() else {}
        data = {d: v for d, v in data.items() if str(start) <= d <= str(end)}
        status_path = destination / 'history' / f'{pair}-status.json'
        statuses = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
        for i in range(365):
            check_deadline(deadline)
            day = start + timedelta(days=i)
            key = str(day)
            if key in data or statuses.get(key) == 'no_quote':
                continue
            result = None
            for attempt in range(2):
                check_deadline(deadline)
                with redirect_stdout(io.StringIO()):
                    result = scraper.get_exchange_rate(datetime.combine(day, datetime.min.time()), *pair.split('-'))
                if isinstance(result, dict) and result.get('error') == 'rate_limited' and not refreshed:
                    from app.cookie_fetcher import CookieFetcher
                    refreshed = True
                    with redirect_stdout(io.StringIO()):
                        success = CookieFetcher(str(ROOT / 'mastercard_cookies.json')).fetch_and_save(headless=False, wait_time=15)
                        if success:
                            scraper.load_cookies()
                    if success:
                        continue
                if result and ('conversionRate' in result.get('data', {}) or result.get('error') == 'not_found'):
                    break
                time.sleep(2)
            if result and 'conversionRate' in result.get('data', {}):
                rate = float(result['data']['conversionRate'])
                if not math.isfinite(rate) or rate <= 0:
                    raise ValueError(f'{pair} {key}: 無效報價')
                data[key] = {'rate': rate, 'updated': datetime.now().isoformat()}
                statuses[key] = 'available'
            elif result and result.get('error') == 'not_found':
                statuses[key] = 'no_quote'
            else:
                statuses[key] = 'fetch_failed'
                save(status_path, statuses)
                save(path, data)
                reason = result.get('error', 'invalid_response') if isinstance(result, dict) else 'no_response'
                raise RuntimeError(f'{pair} {key}: {reason}，保留斷點；不可當成缺報價')
            save(path, data)
            save(status_path, statuses)
            if i % 30 == 0:
                print('DATA', pair, key, 'quotes', len(data), flush=True)
        save(path, data)
        print('DATA_COMPLETE', pair, len(data), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', default=str(date.today()))
    parser.add_argument('--fetch-only', action='store_true')
    parser.add_argument('--evaluate-only', action='store_true')
    args = parser.parse_args()
    end = date.fromisoformat(args.as_of)
    destination = ROOT / 'instance' / 'forecast-benchmark' / str(end)
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    else:
        manifest = {'started': time.time(), 'deadline': time.time() + 8 * 3600,
                    'as_of': str(end), 'start': str(end - timedelta(days=364)),
                    'holdout_start': str(end - timedelta(days=89)), 'models': MODELS,
                    'windows': WINDOWS, 'pairs': PAIRS, 'status': 'fetching'}
        save(manifest_path, manifest)
    if manifest.get('status') == 'complete' or (destination / 'selection-lock.json').exists():
        raise RuntimeError('封存結果已存在，不得重考或覆寫')
    if not args.evaluate_only:
        fetch_year(destination, end, manifest['deadline'])
    manifest['status'] = 'data_ready'
    save(manifest_path, manifest)
    if not args.fetch_only:
        try:
            benchmark(destination, end, manifest)
        except Exception as error:
            manifest.update(status='incomplete', error=str(error))
            save(destination / 'manifest.json', manifest)
            export_csv(destination)
            raise


if __name__ == '__main__':
    main()
