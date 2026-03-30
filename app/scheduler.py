import schedule
import time
import json
import os
import logging
from datetime import datetime, timedelta
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed
from .sse import send_sse_event

logger = logging.getLogger(__name__)
_app = None

DATA_FILE = 'TWD-HKD_180d.json'
COOKIES_FILE = 'mastercard_cookies.json'


def _fetch_missing_data():
    """檢查缺少的日期並從 Mastercard API 抓取

    核心邏輯：
    1. 判斷本地數據是否已包含最新工作日
    2. 收集缺少的工作日日期
    3. 使用 MastercardScraper 並發抓取
    4. 若全部失敗，自動透過 CookieFetcher 刷新 Cookies 並重試
    5. 儲存更新後的 JSON 資料

    Returns:
        bool: 是否成功更新（或已是最新）
    """
    from .mastercard_scraper import MastercardScraper
    from .cookie_fetcher import CookieFetcher

    # 載入本地數據
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            local_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError) as e:
        logger.warning(f"載入本地數據失敗: {e}")
        local_data = {}

    # 找出應該有數據的最新工作日
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    expected_date = today
    while expected_date.weekday() >= 5:
        expected_date -= timedelta(days=1)

    expected_date_str = expected_date.strftime('%Y-%m-%d')

    # 檢查是否需要更新
    if expected_date_str in local_data:
        logger.info(f"✅ 本地數據已是最新（{expected_date_str}）")
        return True

    logger.info(f"⚠️ 需要更新數據（缺少 {expected_date_str}）")

    # 檢查 cookies 是否存在
    if not os.path.exists(COOKIES_FILE):
        logger.info("🍪 Cookies 文件不存在，正在自動獲取...")
        try:
            fetcher = CookieFetcher(COOKIES_FILE)
            success = fetcher.fetch_and_save(headless=False, wait_time=10)
            if not success:
                logger.error("❌ 無法自動獲取 cookies")
                return False
        except Exception as e:
            logger.error(f"❌ 獲取 cookies 時發生錯誤: {e}")
            return False

    # 使用 scraper 更新數據
    logger.info("🔄 正在更新匯率數據...")
    try:
        scraper = MastercardScraper(COOKIES_FILE)

        # 找出需要更新的日期範圍
        if local_data:
            latest_date_str = max(local_data.keys())
            latest_date = datetime.strptime(latest_date_str, '%Y-%m-%d')
            logger.info(f"   本地最新數據：{latest_date_str}")
        else:
            latest_date = today - timedelta(days=181)
            logger.info("   本地無數據，將獲取最近 180 天")

        # 收集需要更新的日期（排除週末和已有數據）
        start_fetch_date = latest_date + timedelta(days=1)
        dates_to_fetch = []
        current_date = start_fetch_date
        while current_date <= today:
            if current_date.weekday() < 5:  # 只更新工作日
                date_str = current_date.strftime('%Y-%m-%d')
                if date_str not in local_data:
                    dates_to_fetch.append(current_date)
            current_date += timedelta(days=1)

        if not dates_to_fetch:
            logger.info("✅ 數據已是最新，無需更新")
            return True

        logger.info(f"🚀 開始並發抓取 {len(dates_to_fetch)} 個日期的數據...")

        # 定義單個日期的抓取函數
        def fetch_single_date(date_obj):
            try:
                data = scraper.get_exchange_rate(date_obj)
                date_str = date_obj.strftime('%Y-%m-%d')

                if data and 'data' in data and 'conversionRate' in data['data']:
                    try:
                        rate = float(data['data']['conversionRate'])
                        return (date_str, rate, None)
                    except (KeyError, ValueError) as e:
                        return (date_str, None, f"解析失敗: {e}")
                else:
                    return (date_str, None, "API 未返回有效數據")
            except Exception as e:
                return (date_obj.strftime('%Y-%m-%d'), None, f"請求失敗: {e}")

        # 並發抓取數據
        updated_count = 0
        failed_count = 0

        with ThreadPoolExecutor(max_workers=12, thread_name_prefix='ScheduledFetch') as executor:
            future_to_date = {executor.submit(fetch_single_date, d): d for d in dates_to_fetch}

            for future in as_completed(future_to_date):
                date_str, rate, error = future.result()

                if rate is not None:
                    local_data[date_str] = {
                        'rate': rate,
                        'updated': datetime.now().isoformat()
                    }
                    logger.info(f"   ✅ {date_str}: {rate}")
                    updated_count += 1
                else:
                    logger.warning(f"   ❌ {date_str}: {error}")
                    failed_count += 1

        # 如果全部失敗，嘗試自動刷新 Cookies 並重試
        if failed_count > 0 and updated_count == 0:
            logger.warning("⚠️ 所有請求都失敗了，可能是 cookies 過期")
            logger.info("🍪 嘗試自動重新獲取 Cookies...")
            try:
                fetcher = CookieFetcher(COOKIES_FILE)
                success = fetcher.fetch_and_save(headless=False, wait_time=10)
                if success:
                    logger.info("✅ Cookies 更新成功，正在重新嘗試抓取數據...")
                    scraper.load_cookies()

                    updated_count = 0
                    failed_count = 0

                    with ThreadPoolExecutor(max_workers=12, thread_name_prefix='RetryFetch') as executor:
                        future_to_date = {executor.submit(fetch_single_date, d): d for d in dates_to_fetch}

                        for future in as_completed(future_to_date):
                            date_str, rate, error = future.result()

                            if rate is not None:
                                local_data[date_str] = {
                                    'rate': rate,
                                    'updated': datetime.now().isoformat()
                                }
                                logger.info(f"   ✅ {date_str}: {rate}")
                                updated_count += 1
                            else:
                                logger.warning(f"   ❌ {date_str}: {error}")
                                failed_count += 1

                    if updated_count > 0:
                        logger.info(f"🎉 使用新 Cookies 成功獲取 {updated_count} 筆數據")
                    else:
                        logger.warning("⚠️ 使用新 Cookies 仍然無法獲取數據")
                else:
                    logger.error("❌ 無法重新獲取 cookies")
            except Exception as e:
                logger.error(f"❌ 重新獲取 cookies 時發生錯誤: {e}")

        # 保存更新的數據
        if updated_count > 0:
            sorted_data = dict(sorted(local_data.items(), key=lambda x: x[0]))
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(sorted_data, f, ensure_ascii=False, indent=2)
            logger.info(f"💾 已保存 {updated_count} 筆新數據到 {DATA_FILE}")
            if failed_count > 0:
                logger.info(f"⚠️ 有 {failed_count} 筆數據獲取失敗")
            return True
        else:
            logger.warning("⚠️ 沒有獲取到新數據")
            return False

    except Exception as e:
        logger.error(f"❌ 更新數據時發生錯誤: {e}", exc_info=True)
        return False


def scheduled_update():
    """定時更新 TWD-HKD 匯率數據

    完整流程：
    1. 從 Mastercard API 抓取缺少的資料（含 Cookie 自動刷新）
    2. 重新載入本地數據到 manager
    3. 預生成圖表
    4. 通知前端更新
    """

    if not _app:
        return

    with _app.app_context():
        manager = _app.manager
        try:
            logger.info("⏰ 排程任務開始：檢查並更新 TWD-HKD 數據...")

            # Step 1: 從 API 抓取缺少的資料
            fetch_success = _fetch_missing_data()

            # Step 2: 重新載入數據（不論抓取是否成功，都要載入最新的本地資料）
            today = datetime.now()
            today_str = today.strftime('%Y-%m-%d')

            old_count = len(manager.data)
            manager.data = manager.load_data()
            new_count = len(manager.data)

            # 清理超過 180 天的舊數據
            manager.update_data(180)

            # Step 3: 檢查今天的資料是否存在
            if today_str in manager.data:
                rate = manager.data[today_str].get('rate')
                logger.info(f"✅ 找到今天({today_str})的資料: {rate}")

                # 預生成所有圖表
                manager.warm_up_chart_cache()

                # 發送SSE事件通知前端更新
                send_sse_event('rate_updated', {
                    'date': today_str,
                    'rate': rate,
                    'updated_time': datetime.now().isoformat(),
                    'message': f'已載入 {today_str} 的匯率資料'
                })
            else:
                # 如果今天是週末，找最新的工作日
                expected_date = today.replace(hour=0, minute=0, second=0, microsecond=0)
                while expected_date.weekday() >= 5:
                    expected_date -= timedelta(days=1)
                expected_str = expected_date.strftime('%Y-%m-%d')

                if expected_str in manager.data:
                    rate = manager.data[expected_str].get('rate')
                    logger.info(f"✅ 找到最新工作日({expected_str})的資料: {rate}")
                    manager.warm_up_chart_cache()
                    send_sse_event('rate_updated', {
                        'date': expected_str,
                        'rate': rate,
                        'updated_time': datetime.now().isoformat(),
                        'message': f'已載入 {expected_str} 的匯率資料'
                    })
                else:
                    logger.warning(f"⚠️ 本地數據中沒有最新工作日的資料（{expected_str}）")
                    if not fetch_success:
                        logger.info("系統將在 09:30 重試")

            if new_count != old_count:
                logger.info(f"數據量變化：{old_count} → {new_count}")

            logger.info("⏰ 排程任務完成")

        except Exception as e:
            logger.error(f"排程更新失敗: {str(e)}", exc_info=True)

def clear_cache_with_context():
    """帶上下文清理緩存"""
    if not _app:
        return
    with _app.app_context():
        _app.manager.clear_expired_cache()

def run_scheduler():
    """在背景執行緒中執行定時任務"""
    while True:
        schedule.run_pending()
        time.sleep(60)

def init_scheduler(app):
    """初始化並啟動排程"""
    global _app
    _app = app

    schedule.every().day.at("09:00").do(scheduled_update)
    schedule.every().day.at("09:30").do(scheduled_update)  # 重試排程
    schedule.every().hour.do(clear_cache_with_context)

    scheduler_thread = Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("✅ 定時任務已啟動（09:00 + 09:30 重試）")