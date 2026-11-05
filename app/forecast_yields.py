"""美國公債期限結構的獨立研究資料；不冒充 Mastercard 標籤。"""
import csv
import hashlib
import math
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .forecast_market import next_source_day

TENORS = ('3 Mo', '2 Yr', '10 Yr')
FEATURES = ('us_2y', 'slope_10y_2y', 'slope_2y_3m', 'change_2y_7', 'change_2y_30', 'yield_quote_age')
REGIME_FEATURES = ('global_fx_vol20', 'usd_correlation60', 'oriented_change_2y_7',
                   'oriented_change_2y_30', 'slope_10y_2y_times_vol', 'slope_2y_3m_times_vol')
URL = 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&page&_format=csv'


def parse_csv(content):
    rows = {}
    for row in csv.DictReader(content.decode('utf-8-sig').splitlines()):
        day = datetime.strptime(row['Date'], '%m/%d/%Y').date()
        if any(row.get(k, '').strip() in ('', 'N/A') for k in TENORS):
            continue
        values = [float(row[k])/100 for k in TENORS]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('殖利率包含無效數值')
        if day in rows:
            raise ValueError('殖利率來源日期重複')
        rows[day] = values
    if len(rows) < 150:
        raise ValueError('年度殖利率資料不足')
    return rows


def download(folder, as_of):
    import requests
    from scripts.research_forecasts import write_once
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    all_rows, sources = {}, []
    for year in range(as_of.year-5, as_of.year+1):
        url = URL.format(year=year)
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        with (folder/f'{year}.csv').open('xb') as stream:
            stream.write(response.content)
        all_rows.update(parse_csv(response.content))
        sources.append(dict(url=url, fetched_at=datetime.now(timezone.utc).isoformat(),
                            sha256=hashlib.sha256(response.content).hexdigest()))
        print('TREASURY_YEAR', year, flush=True)
    calendar = sorted(d for d in all_rows if d <= as_of)
    start = as_of.replace(year=as_of.year-5)
    rows = []
    for day in calendar:
        if day < start:
            continue
        available = next_source_day(day, calendar)
        rows.append(dict(quote_date=str(day), available_date=str(available) if available else None,
                         values=all_rows[day]))
    if len(rows) < 1000 or (as_of-date.fromisoformat(rows[-1]['quote_date'])).days > 10:
        raise ValueError('五年殖利率資料不足或過期')
    snapshot = dict(as_of=str(as_of), rows=rows, sources=sources, tenors=TENORS,
        availability='next observed US source business day plus one calendar day; latest unconfirmed row excluded',
        historical_vintage_verified=False, interpretation='annualized par yields; not policy rates or future realized rates')
    write_once(folder/'yields.json', snapshot)
    return snapshot


class YieldHistory:
    def __init__(self, snapshot):
        self.rows = [r for r in snapshot['rows'] if r['available_date']]
        self.dates = [date.fromisoformat(r['available_date']) for r in self.rows]

    def known(self, day):
        i = bisect_right(self.dates, day)-1
        if i < 0 or (day-date.fromisoformat(self.rows[i]['quote_date'])).days > 10:
            return None
        return self.rows[i]

    def features(self, day):
        row = self.known(day)
        if row is None:
            return None
        a, b = self.known(day-timedelta(days=7)), self.known(day-timedelta(days=30))
        if a is None or b is None:
            return None
        short, middle, long = row['values']
        return [middle, long-middle, middle-short, middle-a['values'][1], middle-b['values'][1],
                (day-date.fromisoformat(row['quote_date'])).days]


def regime_features(market, pair, origin, yields):
    days = [origin-timedelta(days=i) for i in range(60, -1, -1)]
    target = [market.rate(pair, d) for d in days]
    dollar = [market.rate('USD-EUR', d) for d in days]
    paired = [(math.log(b/a), math.log(d/c)) for a, b, c, d in zip(target, target[1:], dollar, dollar[1:]) if all((a, b, c, d))]
    correlation = float('nan')
    if len(paired) >= 40:
        x, y = np.asarray(paired).T
        if np.std(x) > 0 and np.std(y) > 0:
            correlation = float(np.corrcoef(x, y)[0, 1])
    returns = []
    for factor in ('USD-EUR', 'USD-JPY', 'USD-GBP', 'USD-AUD', 'USD-TWD'):
        values = [market.rate(factor, d) for d in days[-21:]]
        r = [math.log(b/a) for a, b in zip(values, values[1:]) if a and b]
        if len(r) >= 10:
            returns.append(float(np.std(r)))
    vol = float(np.mean(returns)) if returns else float('nan')
    return [vol, correlation, correlation*yields[3], correlation*yields[4], yields[1]*vol, yields[2]*vol]
