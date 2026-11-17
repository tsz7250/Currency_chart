"""融合方向機率（regime＋control CatBoost 固定 50/50）。
模型檔不存在或幣對未研究時整模組停用，主站行為不變。
特徵契約與研究腳本一致：control 用 forecast_supervised.features，
regime 用 market_features＋殖利率＋regime 特徵。"""
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

from . import forecast_supervised as supervised
from .forecast_market import MarketHistory, download_market
from .forecast_market_features import market_features
from .forecast_yields import YieldHistory, download as download_yields, regime_features

logger = logging.getLogger(__name__)

PAIRS = supervised.PAIRS
HORIZONS = supervised.HORIZONS
MODEL_FILES = ('blend-regime.cbm', 'blend-control.cbm')

_models = {}


def disabled(instance_path):
    """停用標記：instance/models/blend-disabled 存在即不啟用融合推論（模型檔與訓練保留）。"""
    return (Path(instance_path) / 'models' / 'blend-disabled').exists()


def available(instance_path):
    if disabled(instance_path):
        return False
    folder = Path(instance_path) / 'models'
    return all((folder / name).exists() for name in MODEL_FILES)


def models_fresh(instance, stale_days=7):
    """兩個融合模型檔皆存在且未超過 stale_days 天——單一新鮮度實作，排程與訓練腳本共用。"""
    folder = Path(instance) / 'models'
    now = time.time()
    return all((folder / name).exists() and now - (folder / name).stat().st_mtime < stale_days * 86400
               for name in MODEL_FILES)


def _load_models(instance_path):
    from catboost import CatBoostClassifier
    loaded = []
    for name in MODEL_FILES:
        path = str(Path(instance_path) / 'models' / name)
        if path not in _models:
            _models[path] = CatBoostClassifier().load_model(path)
        loaded.append(_models[path])
    return loaded


def get_snapshot(folder, today, download, filename):
    """當日快照快取；過期即重抓（treasury 年檔用獨佔寫入，只移除自己的舊檔，不清整個目錄）。"""
    path = folder / filename
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('as_of') == str(today):
                return data
        except (ValueError, OSError):
            pass
    if filename == 'yields.json' and folder.exists():
        for old in (path, *folder.glob('*.csv')):
            try:
                old.unlink()
            except OSError:
                pass
    return download(folder, today)


def quote_ages(market, sources, days):
    """各日報價年齡（天）；快照 JSON 的 quote_date 是字串。來源缺日回傳 None。"""
    known = {}
    for day in days:
        rows = [market.known(src, day) for src in sources]
        if any(r is None for r in rows):
            return None
        known[day] = max((day - date.fromisoformat(r['quote_date'])).days for r in rows)
    return known


def make_directions(instance_path, buy, sell, entries):
    """回傳 directions(origin, base, horizon) 或 None；重活都在回呼內延遲執行。"""
    pair = f'{buy}-{sell}'
    if pair not in PAIRS or not available(instance_path):
        return None
    history = {d.isoformat(): {'rate': r} for d, r in entries}
    root = Path(instance_path) / 'forecast-blend'
    cache = {}

    def prepare(today):
        if 'day' in cache and cache['day'] == today:
            return cache['prep']
        models = _load_models(instance_path)
        snapshot = get_snapshot(root / 'market', today, download_market, 'market.json')
        market = MarketHistory(snapshot)
        treasury = get_snapshot(root / 'treasury', today, download_yields, 'yields.json')
        prep = (market, YieldHistory(treasury), models)
        cache['day'], cache['prep'] = today, prep
        return prep

    def directions(origin, base, horizon):
        try:
            if horizon not in HORIZONS:
                return None
            if origin in cache:
                return cache[origin][horizon]
            market, rate_history, models = prepare(date.today())
            days = [origin - timedelta(days=i) for i in range(800, -1, -1)]
            lookup = {d: market.rate(pair, d) for d in days}
            lookup = {d: v for d, v in lookup.items() if v is not None}
            sources = ('ecb', 'cbc') if 'TWD' in pair else ('ecb',)
            known = quote_ages(market, sources, lookup)
            if not lookup or known is None:
                return None
            y = rate_history.features(origin)
            if y is None:
                return None
            rows_regime = []
            for h in HORIZONS:
                x = market_features(lookup, known, origin, h)
                if x is None:
                    return None
                rows_regime.append(dict(x=x + y + regime_features(market, pair, origin, y),
                                        horizon=h, base=base))
            rows_control = [dict(x=supervised.features(history, market, pair, origin) + [h],
                                 horizon=h, base=base) for h in HORIZONS]
            p = (supervised.probabilities(models[0], rows_regime)
                 + supervised.probabilities(models[1], rows_control)) / 2
            probs = {h: p[i].tolist() for i, h in enumerate(HORIZONS)}
            cache[origin] = probs
            return probs[horizon]
        except Exception:
            logger.warning('融合方向機率推論失敗，該起點停用', exc_info=True)
            return None

    return directions
