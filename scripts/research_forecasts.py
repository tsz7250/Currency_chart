"""探索性多折研究；不得覆寫上一輪封存結果。"""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.forecast_market import MarketHistory, download_market
from app.forecast_research import CENTERS, CONFIG, HORIZONS, INTERVALS, block_uncertainty, metrics, replay, replacement_checks
from scripts.benchmark_forecasts import MODELS, PAIRS, REVISIONS, Predictor, check_deadline, inputs_for
from scripts.evaluate_forecasts import save


def write_once(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 排他建立避免研究鎖定及影子預測被覆寫。
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False, default=str)


def predictions(entries, pair, predictor, market, auxiliary=False, window=90):
    start, end = entries[0][0], entries[-1][0]
    origins, values = inputs_for(entries, start, end + timedelta(days=1), window)
    if not auxiliary:
        return origins, predictor(values)
    output = []
    for offset in range(0, len(values), 8):
        check_deadline(predictor.deadline)
        batch = []
        for origin, target in zip(origins[offset:offset+8], values[offset:offset+8]):
            days = [origin - timedelta(days=len(target)-1-i) for i in range(len(target))]
            features = market.covariates(pair, days, target)
            if any(sum(math.isfinite(x) for x in series) < 40 for series in features.values()):
                raise ValueError(f'{pair} {origin}: 輔助資料不足，不可隱藏退回原模型')
            batch.append({'target': predictor.torch.tensor(target), 'past_covariates': {
                k: predictor.torch.tensor(v) for k, v in features.items()}})
        q, _ = predictor.pipeline.predict_quantiles(batch, prediction_length=30,
            quantile_levels=[.1, .5, .9], batch_size=32, cross_learning=False)
        output.extend(v[0].cpu().tolist() for v in q)
    return origins, output


def fold_rows(rows, start, end):
    # 每折的所有評分目標必須在該折結束前已實現。
    folds = [(start + timedelta(days=a), min(start + timedelta(days=b), end + timedelta(days=1)))
             for a, b in ((180, 240), (240, 300), (300, 365))]
    return [[r for r in rows if a <= r['origin'] < b and r['target'] < b and r['horizon'] in HORIZONS]
            for a, b in folds]


def report_rows(rows, entries, uncertainty=False):
    lookup = dict(entries)
    folds = fold_rows(rows, entries[0][0], entries[-1][0])
    result = {'folds': []}
    for fold in folds:
        result['folds'].append({str(h): metrics([r for r in fold if r['horizon'] == h], lookup) for h in HORIZONS})
    result['overall'] = {}
    for h in HORIZONS:
        selected = [r for fold in folds for r in fold if r['horizon'] == h]
        result['overall'][str(h)] = metrics(selected, lookup)
        if uncertainty:
            result['overall'][str(h)]['uncertainty'] = block_uncertainty(selected, lookup)
    return result


def objective(reports, field):
    values = [fold[str(h)][field] for pair in PAIRS[:4] for fold in reports[pair]['folds'] for h in HORIZONS]
    if any(v is None for v in values):
        raise ValueError('共同評分樣本不足')
    return statistics.mean(values)


def run(destination, history, end, deadline):
    import gc
    market_data = download_market(destination / 'market', end)
    market = MarketHistory(market_data)
    data, diagnostics = {}, {}
    for pair in PAIRS:
        raw = json.loads((history / f'{pair}.json').read_text(encoding='utf-8'))
        entries = sorted((date.fromisoformat(d), float(v['rate'])) for d, v in raw.items() if d <= str(end))[-365:]
        if len(entries) < 300 or entries[-1][0] != end:
            raise ValueError(f'{pair}: Mastercard 資料不足或截止日不符')
        data[pair] = entries
        write_once(destination / 'history' / f'{pair}.json', raw)
        duplicates = sum(a[1] == b[1] for a, b in zip(entries, entries[1:]))
        gaps = [math.log(rate / market.rate(pair, day)) for day, rate in entries if market.rate(pair, day)]
        diagnostics[pair] = {'quotes': len(entries), 'repeated_adjacent': duplicates,
            'weekend_quotes': sum(d.weekday() >= 5 for d, _ in entries),
            'fx_date_known': sum(bool(v.get('fx_date') or v.get('fxDate')) for v in raw.values()),
            'market_log_spread_median': statistics.median(gaps) if gaps else None,
            'market_log_spread_min': min(gaps) if gaps else None,
            'market_log_spread_max': max(gaps) if gaps else None}
    save(destination / 'diagnostics.json', diagnostics)
    predictor = Predictor(MODELS[2], deadline)
    predictions_by_pair = {}
    centers = {name: {} for name in CENTERS}
    for pair in PAIRS:
        plain = predictions(data[pair], pair, predictor, market)
        auxiliary = predictions(data[pair], pair, predictor, market, True)
        if plain[0] != auxiliary[0]:
            raise ValueError('輔助模型起點不一致')
        predictions_by_pair[pair] = (plain, auxiliary)
        write_once(destination / 'predictions' / f'{pair}.json', {'origins': plain[0], 'raw': plain[1], 'covariates': auxiliary[1]})
        for name in CENTERS:
            origins, quantiles = auxiliary if name == 'covariates' else plain
            centers[name][pair] = report_rows(replay(data[pair], origins, quantiles, name, 'cumulative'), data[pair])
        print('CENTER', pair, flush=True)
    ranking = sorted(CENTERS, key=lambda name: objective(centers[name], 'normalized_mae'))
    selected = ranking[0]
    write_once(destination / 'center-lock.json', {'center': selected, 'ranking': {k: objective(centers[k], 'normalized_mae') for k in ranking}})
    intervals = {name: {} for name in INTERVALS}
    for interval in INTERVALS:
        for pair in PAIRS:
            plain, auxiliary = predictions_by_pair[pair]
            origins, quantiles = auxiliary if selected == 'covariates' else plain
            intervals[interval][pair] = report_rows(replay(data[pair], origins, quantiles, selected, interval), data[pair])
    selected_interval = min(INTERVALS, key=lambda name: objective(intervals[name], 'normalized_interval_score'))
    # 研究鎖定不代表准許替換；未來影子驗證維持同一設定。
    config = {'version': 1, 'center': selected, 'interval': selected_interval, 'parameters': CONFIG,
              'model': MODELS[2], 'revision': REVISIONS[2], 'baseline_model': MODELS[0], 'baseline_revision': REVISIONS[0],
              'pairs': PAIRS, 'research_as_of': str(end), 'classification_threshold': .001,
              'baseline_policy': 'production evaluate(), trailing 180 calendar days, context inclusive 91',
              'origin_days': 90, 'maturation_days': 30,
              'locked_at': datetime.now(timezone.utc).isoformat(),
              'research_code_sha256': hashlib.sha256((ROOT / 'app/forecast_research.py').read_bytes()).hexdigest(),
              'market_code_sha256': hashlib.sha256((ROOT / 'app/forecast_market.py').read_bytes()).hexdigest()}
    write_once(destination / 'candidate-lock.json', config)
    del predictor
    gc.collect()
    import torch
    torch.cuda.empty_cache()
    baseline_predictor = Predictor(MODELS[0], deadline)
    baseline, chosen, flat = {}, {}, {}
    for pair in PAIRS:
        origins, quantiles = predictions(data[pair], pair, baseline_predictor, market, window=91)
        baseline[pair] = report_rows(replay(data[pair], origins, quantiles, 'raw', 'cumulative'), data[pair])
        plain, auxiliary = predictions_by_pair[pair]
        origins, quantiles = auxiliary if selected == 'covariates' else plain
        chosen[pair] = report_rows(replay(data[pair], origins, quantiles, selected, selected_interval), data[pair], True)
        lookup = dict(data[pair])
        flat_quantiles = [[[lookup[d]] * 3 for _ in range(30)] for d in origins]
        flat[pair] = report_rows(replay(data[pair], origins, flat_quantiles, 'raw', 'cumulative'), data[pair])
    flatten = lambda reports: {f'{p}/{h}': reports[p]['overall'][str(h)] for p in PAIRS for h in HORIZONS}
    result = {'status': 'complete', 'purpose': 'exploratory_only_old_holdout_already_used',
              'center': selected, 'interval': selected_interval,
              'checks_exploratory': replacement_checks(flatten(chosen), flatten(baseline), flatten(flat)),
              'eligible_for_production': False, 'centers': centers, 'intervals': intervals,
              'selected': chosen, 'baseline': baseline, 'flat': flat,
              'limitations': ['Historical publication vintages unverified; conservative availability lag used.',
                              'Legacy Mastercard fxDate unavailable; not fabricated.',
                              'Five years of market history are auxiliary only, not five years of target labels.']}
    save(destination / 'result.json', result)
    with (destination / 'metrics.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['center','interval','pair','horizon','n','normalized_mae','accuracy','precision','recall','f1','coverage80','normalized_width','normalized_interval_score'])
        for name, reports in centers.items():
            for pair, report in reports.items():
                for h, m in report['overall'].items():
                    writer.writerow([name,'cumulative',pair,h,m['samples'],m['normalized_mae'],
                        *(m['classification'][k] for k in ('accuracy','precision','recall','f1')),
                        m['coverage80'],m['normalized_width'],m['normalized_interval_score']])
        for name, reports in intervals.items():
            for pair, report in reports.items():
                for h, m in report['overall'].items():
                    writer.writerow([selected,name,pair,h,m['samples'],m['normalized_mae'],
                        *(m['classification'][k] for k in ('accuracy','precision','recall','f1')),
                        m['coverage80'],m['normalized_width'],m['normalized_interval_score']])
    print('SELECTED', selected, selected_interval, result['checks_exploratory'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--history', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {'started': time.time(), 'deadline': time.time() + 8*3600, 'status': 'running', 'as_of': args.as_of}
    write_once(destination / 'manifest.json', manifest)
    try:
        run(destination, Path(args.history), date.fromisoformat(args.as_of), manifest['deadline'])
        manifest['status'] = 'complete'
    except Exception as error:
        manifest.update(status='incomplete', error=str(error))
        raise
    finally:
        manifest['elapsed_seconds'] = time.time() - manifest['started']
        save(destination / 'manifest.json', manifest)


if __name__ == '__main__':
    main()
