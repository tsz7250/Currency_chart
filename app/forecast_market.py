"""公開市場資料的來源快照及保守時點對齊；不替代 Mastercard 標籤。"""
import csv
import hashlib
import io
import json
import math
import zipfile
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ECB_URL = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip'
CBC_URL = 'https://cpx.cbc.gov.tw/api/OpenData/FTDOpenData_Day'


def next_source_day(day, calendar):
    # 下一個實際來源報價日之後才使用，跨假日亦不提前可用。
    i = bisect_right(calendar, day)
    if i < len(calendar):
        return calendar[i] + timedelta(days=1)
    return None


def normalize(ecb_bytes, cbc_bytes, as_of, fetched_at):
    start = as_of.replace(year=as_of.year - 5) if (as_of.month, as_of.day) != (2, 29) else date(as_of.year - 5, 2, 28)
    sources = {}
    ecb = {}
    for row in csv.DictReader(ecb_bytes.decode('utf-8-sig').splitlines()):
        day = date.fromisoformat(row['Date'].strip())
        values = {'EUR': 1.0}
        for currency in ('USD', 'JPY', 'GBP', 'AUD', 'HKD'):
            value = row.get(currency, '').strip()
            if value and value != 'N/A':
                values[currency] = float(value)
        ecb[day] = values
    cbc = {datetime.strptime(r['日期'], '%Y%m%d').date(): {'TWD': float(r['NTD_USD'])}
           for r in json.loads(cbc_bytes)}
    for name, raw, url, license_url in (
        ('ecb', ecb, ECB_URL, 'https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html'),
        ('cbc', cbc, CBC_URL, 'https://data.gov.tw/license')):
        calendar = sorted(d for d in raw if d <= as_of)
        rows = []
        for day in calendar:
            if day < start:
                continue
            if any(not math.isfinite(v) or v <= 0 for v in raw[day].values()):
                raise ValueError(f'{name}: 無效報價')
            available = next_source_day(day, calendar)
            rows.append({'quote_date': str(day), 'available_date': str(available) if available else None,
                         'values': raw[day]})
        if len(rows) < 1000 or (as_of - date.fromisoformat(rows[-1]['quote_date'])).days > 10:
            raise ValueError(f'{name}: 五年資料不足或過期')
        sources[name] = {'url': url, 'license_url': license_url, 'fetched_at': fetched_at,
                         'query_date': str(as_of), 'rows': rows}
    return {'as_of': str(as_of), 'start': str(start), 'sources': sources,
            'availability_policy': 'next observed source business day plus one calendar day; latest unconfirmed release excluded',
            'historical_vintage_verified': False}


def download_market(folder, as_of):
    import requests
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    fetched = datetime.now(timezone.utc).isoformat()
    ecb = requests.get(ECB_URL, timeout=60)
    ecb.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(ecb.content)) as archive:
        ecb_bytes = archive.read('eurofxref-hist.csv')
    cbc = requests.get(CBC_URL, timeout=60)
    cbc.raise_for_status()
    result = normalize(ecb_bytes, cbc.content, as_of, fetched)
    for name, content in (('ecb', ecb_bytes), ('cbc', cbc.content)):
        (folder / f'{name}.raw').write_bytes(content)
        result['sources'][name]['sha256'] = hashlib.sha256(content).hexdigest()
    (folder / 'market.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result


class MarketHistory:
    def __init__(self, snapshot):
        self.sources = {}
        for name, source in snapshot['sources'].items():
            rows = [r for r in source['rows'] if r['available_date']]
            self.sources[name] = ([(date.fromisoformat(r['available_date'])) for r in rows], rows)

    def _known(self, source, day):
        dates, rows = self.sources[source]
        i = bisect_right(dates, day) - 1
        if i < 0 or (day - date.fromisoformat(rows[i]['quote_date'])).days > 10:
            return None
        return rows[i]

    def rate(self, pair, day):
        ecb = self._known('ecb', day)
        if not ecb:
            return None
        values = dict(ecb['values'])
        buy, sell = pair.split('-')
        if 'TWD' in (buy, sell):
            cbc = self._known('cbc', day)
            if not cbc or 'USD' not in values:
                return None
            values['TWD'] = values['USD'] * cbc['values']['TWD']
        if buy not in values or sell not in values:
            return None
        return values[sell] / values[buy]

    def covariates(self, pair, days, target):
        market = [self.rate(pair, day) for day in days]
        volatility, spread = [], []
        for day, actual, rate in zip(days, target, market):
            history = [self.rate(pair, day - timedelta(days=i)) for i in range(60, -1, -1)]
            changes = [math.log(b / a) for a, b in zip(history, history[1:]) if a and b]
            volatility.append((sum(x*x for x in changes) / len(changes)) ** .5 if len(changes) >= 20 else float('nan'))
            spread.append(math.log(actual / rate) if rate and math.isfinite(actual) else float('nan'))
        return {'market_rate': [r if r else float('nan') for r in market],
                'market_volatility': volatility, 'mastercard_spread': spread}
