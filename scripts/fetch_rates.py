"""
GitHub Actions 用獨立匯率抓取腳本
自動取得 cookies（有頭 Playwright）+ 抓取 TWD-HKD 匯率
"""

import json
import os
import shutil
import sys
import time
import random
from datetime import datetime, timedelta

import requests

# 專案根目錄
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(ROOT_DIR, 'TWD-HKD_180d.json')
DOCS_DATA_FILE = os.path.join(ROOT_DIR, 'docs', 'data', 'TWD-HKD_180d.json')
COOKIES_FILE = os.path.join(ROOT_DIR, 'mastercard_cookies.json')


def load_cookies():
    """從檔案載入 cookies，回傳 dict 或 None"""
    try:
        with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
            cookies_list = json.load(f)
        return {c['name']: c['value'] for c in cookies_list}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def fetch_cookies_with_playwright():
    """用有頭 Playwright 自動取得 cookies（CI 環境透過 xvfb-run）"""
    print("[fetch_rates] 啟動 Playwright 有頭模式取得 cookies...")

    # 將 app/ 加入 path 以匯入 CookieFetcher
    sys.path.insert(0, os.path.join(ROOT_DIR, 'app'))
    from cookie_fetcher import CookieFetcher

    fetcher = CookieFetcher(cookies_file=COOKIES_FILE)
    success = fetcher.fetch_and_save(headless=False, wait_time=15)

    if not success:
        print("[fetch_rates] ❌ Playwright 取得 cookies 失敗")
        return None

    return load_cookies()


def get_exchange_rate(date, cookies_dict):
    """抓取單日匯率，回傳 rate (float) 或 None，403 時回傳 'expired'"""
    url = "https://www.mastercard.com/marketingservices/public/mccom-services/currency-conversions/conversion-rates"
    params = {
        'exchange_date': date.strftime('%Y-%m-%d'),
        'transaction_currency': 'TWD',
        'cardholder_billing_currency': 'HKD',
        'bank_fee': '0',
        'transaction_amount': '1'
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh-HK;q=0.8,zh;q=0.6,en-US;q=0.4,en;q=0.2',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'DNT': '1',
        'Sec-GPC': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
    }

    date_str = date.strftime('%Y-%m-%d')
    time.sleep(random.uniform(0.5, 1.0))

    try:
        resp = requests.get(url, params=params, headers=headers, cookies=cookies_dict, timeout=15)

        if resp.status_code == 200:
            data = resp.json()
            rate = float(data['data']['conversionRate'])
            print(f"  ✓ {date_str}: {rate}")
            return rate
        elif resp.status_code == 403:
            print(f"  ✗ {date_str}: HTTP 403 - cookies 過期")
            return 'expired'
        elif resp.status_code == 400:
            try:
                err = resp.json()
                if err.get('data', {}).get('errorCode') == '114':
                    # 該日無數據（正常，例如假日）
                    return None
            except Exception:
                pass
            print(f"  ✗ {date_str}: HTTP 400")
            return None
        else:
            print(f"  ✗ {date_str}: HTTP {resp.status_code}")
            return None
    except Exception as e:
        print(f"  ✗ {date_str}: 錯誤 - {e}")
        return None


def load_data():
    """載入現有數據（優先根目錄，fallback 到 docs/data/）"""
    for file_path in [DATA_FILE, DOCS_DATA_FILE]:
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if data:
                        print(f"[fetch_rates] 從 {file_path} 載入 {len(data)} 筆資料")
                        return data
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return {}


def save_data(data):
    """儲存數據到主檔與 docs/ 副本"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[fetch_rates] ✓ 已儲存至 {DATA_FILE}")

    # 同步到 docs/data/
    os.makedirs(os.path.dirname(DOCS_DATA_FILE), exist_ok=True)
    shutil.copy2(DATA_FILE, DOCS_DATA_FILE)
    print(f"[fetch_rates] ✓ 已同步至 {DOCS_DATA_FILE}")


def fetch_missing_rates(cookies_dict, data, days=180):
    """補齊缺少的工作日匯率，回傳 (更新筆數, 是否遇到 403)"""
    end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_date = end_date - timedelta(days=days)

    # 從最新已有日期之後開始
    if data:
        latest = max(data.keys())
        latest_dt = datetime.strptime(latest, '%Y-%m-%d')
        start_date = latest_dt + timedelta(days=1)
        print(f"[fetch_rates] 最新數據: {latest}")

    print(f"[fetch_rates] 需更新: {start_date.strftime('%Y-%m-%d')} ~ {end_date.strftime('%Y-%m-%d')}")

    updated = 0
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:  # 跳過週末
            date_str = current.strftime('%Y-%m-%d')
            if date_str not in data:
                result = get_exchange_rate(current, cookies_dict)
                if result == 'expired':
                    return updated, True  # 遇到 403
                elif result is not None:
                    data[date_str] = {
                        'rate': result,
                        'updated': datetime.now().isoformat()
                    }
                    updated += 1
        current += timedelta(days=1)

    return updated, False


def main():
    print("=" * 60)
    print("  TWD-HKD 匯率自動抓取（GitHub Actions 版）")
    print("=" * 60)

    data = load_data()
    print(f"[fetch_rates] 現有 {len(data)} 筆記錄")

    # 嘗試載入現有 cookies
    cookies = load_cookies()

    if cookies:
        print(f"[fetch_rates] 已載入 {len(cookies)} 個 cookies，嘗試抓取...")
        updated, expired = fetch_missing_rates(cookies, data)

        if expired:
            print("[fetch_rates] Cookies 過期，啟動 Playwright 刷新...")
            cookies = fetch_cookies_with_playwright()
            if cookies:
                # 重試
                updated2, expired2 = fetch_missing_rates(cookies, data)
                updated += updated2
                if expired2:
                    print("[fetch_rates] ❌ 刷新後仍被 403 阻擋")
                    sys.exit(1)
            else:
                print("[fetch_rates] ❌ 無法取得新 cookies")
                sys.exit(1)
    else:
        print("[fetch_rates] 無現有 cookies，啟動 Playwright 取得...")
        cookies = fetch_cookies_with_playwright()
        if not cookies:
            print("[fetch_rates] ❌ 無法取得 cookies")
            sys.exit(1)

        updated, expired = fetch_missing_rates(cookies, data)
        if expired:
            print("[fetch_rates] ❌ 新 cookies 仍被 403 阻擋")
            sys.exit(1)

    if updated > 0:
        save_data(data)
        print(f"\n✅ 成功更新 {updated} 筆匯率數據")
    else:
        # 即使沒有新數據，也同步 docs/data/ 確保存在
        save_data(data)
        print("\n✅ 數據已是最新，無需更新")


if __name__ == '__main__':
    main()
