import os
import json
import time
import hashlib
import asyncio
import logging
import requests
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from threading import Lock, Thread
from concurrent.futures import ThreadPoolExecutor, as_completed
import concurrent.futures
from matplotlib.ticker import MaxNLocator, FuncFormatter
from flask import current_app

from .utils import LRUCache, RateLimiter
from .sse import send_sse_event

logger = logging.getLogger(__name__)

# 數據文件路徑
DATA_FILE = 'TWD-HKD_180d.json'
rate_limiter = RateLimiter(max_requests_per_second=5)


class ExchangeRateManager:
    def __init__(self):
        self.data = self.load_data()
        self._network_paused = False
        self._pause_until = 0
        self._pause_lock = Lock()
        self._pause_message_printed = False

        # 確保圖表目錄存在
        self.charts_dir = os.path.join('static', 'charts')
        if not os.path.exists(self.charts_dir):
            os.makedirs(self.charts_dir)

        # 初始化 LRU 快取
        self.lru_cache = LRUCache(capacity=60, ttl_seconds=86400)

        # 新增：用於今日匯率的快取 (與圖表快取使用相同的 TTL)
        self.latest_rate_cache = LRUCache(capacity=50, ttl_seconds=86400) # 24 hours

        # 新增：用於協調背景抓取的屬性
        self.background_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix='ChartGen')
        self._active_fetch_lock = Lock()
        self._active_fetches = set()

        # 主數據鎖
        self.data_lock = Lock()

    def load_data(self):
        """載入本地數據"""
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"載入數據時發生錯誤: {e}", exc_info=True)
                return {}
        return {}

    def save_data(self):
        """保存數據到本地"""
        with self.data_lock:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get_sorted_dates(self):
        """獲取排序後的日期列表"""
        dates = list(self.data.keys())
        dates.sort()
        return dates
    
    def shutdown(self):
        """清理資源"""
        if hasattr(self, 'background_executor'):
            print("🛑 正在關閉 ThreadPoolExecutor...")
            self.background_executor.shutdown(wait=True, timeout=10)
            print("✅ ThreadPoolExecutor 已關閉")
    
    def __enter__(self):
        """上下文管理器支援"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器退出時清理"""
        self.shutdown()

    

    def get_exchange_rate(self, date, buy_currency='TWD', sell_currency='HKD'):
        """
        獲取指定日期的匯率
        - TWD-HKD: 從本地數據讀取（快速）
        - 其他貨幣對: 實時從 Mastercard 獲取（使用 cookies）
        """
        date_str = date.strftime('%Y-%m-%d')
        
        # 如果是 TWD-HKD，從本地數據讀取
        if buy_currency == 'TWD' and sell_currency == 'HKD':
            if date_str in self.data:
                rate = self.data[date_str].get('rate')
                if rate:
                    return {
                        'data': {
                            'conversionRate': str(rate)
                        }
                    }
            # 本地數據中沒有該日期的數據
            return None
        
        # 其他貨幣對：使用 mastercard_scraper 實時獲取
        try:
            from .mastercard_scraper import MastercardScraper
            import os
            
            COOKIES_FILE = 'mastercard_cookies.json'
            
            # 檢查 cookies 是否存在
            if not os.path.exists(COOKIES_FILE):
                print(f"⚠️ 獲取 {buy_currency}-{sell_currency} 失敗：缺少 cookies 文件")
                print(f"   請運行：python app\\cookie_fetcher.py")
                return None
            
            scraper = MastercardScraper(COOKIES_FILE)
            data = scraper.get_exchange_rate(date, buy_currency, sell_currency)
            
            return data
            
        except Exception as e:
            print(f"❌ 獲取 {buy_currency}-{sell_currency} 時發生錯誤: {e}")
            return None

    def update_data(self, days=180):
        """清理舊數據並重新載入（不再從 API 獲取新數據）
        
        數據更新由應用啟動邏輯與排程自動完成，無需另外執行外部腳本。
        """
        # 如需更新數據，請運行：python update_twd_hkd_data.py
        
        end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start_date = end_date - timedelta(days=days)
        
        print(f"🔍 開始清理 {days} 天以外的舊數據...")
        
        # 重新載入數據（可能已被外部腳本更新）
        self.data = self.load_data()
        
        # 清理超過指定天數的舊數據
        old_count = len(self.data)
        cleaned_data = {}
        removed_count = 0
        
        for date_str, data_entry in self.data.items():
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            if date_obj >= start_date:
                cleaned_data[date_str] = data_entry
            else:
                removed_count += 1
        
        self.data = cleaned_data
        
        # 顯示數據狀態
        if self.data:
            latest_date_str = max(self.data.keys())
            print(f"📅 數據中最新日期：{latest_date_str}")
            print(f"📊 當前數據量：{len(self.data)} 筆")
        else:
            print(f"⚠️ 沒有本地數據，請運行：python update_twd_hkd_data.py")
        
        # 保存清理結果
        if removed_count > 0:
            self.save_data()
            print(f"🗑️ 清理了 {removed_count} 筆舊數據")
        
        if removed_count == 0 and old_count > 0:
            print("✅ 數據已是最新狀態")
        
        return 0  # 不再返回更新數量，因為不從 API 獲取

    def _fetch_single_rate(self, date, buy_currency, sell_currency, max_retries=1):
        """獲取單一日期的匯率數據（用於並行查詢，含重試機制）"""
        date_str = date.strftime('%Y-%m-%d')

        for attempt in range(max_retries):
            try:
                data = self.get_exchange_rate(date, buy_currency, sell_currency)

                if data and 'data' in data:
                    conversion_rate = float(data['data']['conversionRate'])
                    return date_str, conversion_rate

                # 如果 get_exchange_rate 回傳 None (網路暫停或已處理的錯誤)，直接返回
                if data is None:
                    return date_str, None

                # 如果 API 回傳的 JSON 結構不完整，但不是網路錯誤
                if attempt < max_retries - 1:
                    print(f"🔄 {date_str}: 無數據，重試 ({attempt + 1}/{max_retries})")
                    time.sleep(1)  # 等待1秒後重試
                    continue
                else:
                    return date_str, None

            except Exception as e:
                print(f"❌ {date_str}: 未知錯誤 - {e}")
                return date_str, None

        return date_str, None

    def get_live_rates_for_period(self, days, buy_currency, sell_currency):
        """
        獲取指定天數的即時匯率數據（使用並發抓取）
        
        Args:
            days: 要獲取的天數
            buy_currency: 交易貨幣
            sell_currency: 帳單貨幣
            
        Returns:
            dict: {date_str: rate} 的字典
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # 收集所有需要查詢的日期（排除週末）
        query_dates = []
        current_date = start_date
        while current_date <= end_date:
            if current_date.weekday() < 5:  # Monday=0, Friday=4
                query_dates.append(current_date)
            current_date += timedelta(days=1)
        
        if not query_dates:
            print(f"⚠️ {buy_currency}-{sell_currency}: 沒有需要查詢的日期")
            return {}
        
        print(f"🚀 開始並發獲取 {buy_currency}-{sell_currency} 最近 {days} 天的數據（共 {len(query_dates)} 天）")
        
        # 使用 ThreadPoolExecutor 並發抓取
        rates_data = {}
        with ThreadPoolExecutor(max_workers=5, thread_name_prefix='LiveRateFetch') as executor:
            future_to_date = {
                executor.submit(self._fetch_single_rate, d, buy_currency, sell_currency): d 
                for d in query_dates
            }
            
            for future in as_completed(future_to_date):
                date_str, rate = future.result()
                if rate is not None:
                    rates_data[date_str] = rate
        
        print(f"✅ 成功獲取 {len(rates_data)}/{len(query_dates)} 天的數據")
        return rates_data

    def extract_local_rates(self, days):
        """獲取指定天數的匯率數據"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        dates = []
        rates = []

        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            if date_str in self.data:
                dates.append(current_date)
                # 顯示 1/rate，即 1 港幣等於多少台幣
                rates.append(self.data[date_str]['rate'])
            current_date += timedelta(days=1)

        return dates, rates

    def _background_fetch_and_generate(self, buy_currency, sell_currency, flask_app):
        """
        非同步抓取180天歷史數據，並在過程中流式生成圖表、發送進度。
        """
        with flask_app.app_context():
            try:
                logger.info(f"🌀 事件驅動背景任務開始：為 {buy_currency}-{sell_currency} 抓取180天數據")

                # 1. 收集日期，從最新到最舊
                end_date = datetime.now()
                start_date = end_date - timedelta(days=180)
                query_dates = sorted([d for d in (end_date - timedelta(days=i) for i in range(181)) if d.weekday() < 5], reverse=True)
                total_days_to_fetch = len(query_dates)

                if total_days_to_fetch == 0:
                    print(f"🔚 {buy_currency}-{sell_currency}: 無需抓取任何日期。")
                    return

                # 2. 初始化變量
                rates_data = {}
                fetched_count = 0
                generated_periods = set()
                chart_generation_checkpoints = {7: 5, 30: 21, 90: 65, 180: 129}

                # 3. 並行抓取
                with ThreadPoolExecutor(max_workers=5, thread_name_prefix='RateFetch') as executor:
                    future_to_date = {executor.submit(self._fetch_single_rate, d, buy_currency, sell_currency): d for d in query_dates}
                    
                    for future in as_completed(future_to_date):
                        date_str, rate = future.result()
                        fetched_count += 1
                        if rate is not None:
                            rates_data[date_str] = rate

                        # 發送進度更新（加入各 period 進度）
                        progress = int((fetched_count / total_days_to_fetch) * 100)
                        # 以已成功取得的資料量來估算各期間進度（更貼近實際可生成狀態）
                        current_points = len(rates_data)
                        period_progress = {}
                        for p, needed in chart_generation_checkpoints.items():
                            # 防止除以零並限制 0-100
                            pct = int(min(100, max(0, (current_points / max(1, needed)) * 100)))
                            period_progress[str(p)] = pct
                        # 也將每個 period 所需門檻與目前累計成功點數傳給前端
                        period_needed = {str(p): needed for p, needed in chart_generation_checkpoints.items()}
                        send_sse_event('progress_update', {
                            'progress': progress,
                            'buy_currency': buy_currency,
                            'sell_currency': sell_currency,
                            'message': f'已獲取 {fetched_count}/{total_days_to_fetch} 天數據...',
                            'fetched_count': fetched_count,
                            'total_days': total_days_to_fetch,
                            'period_progress': period_progress,
                            'current_points': current_points,
                            'period_needed': period_needed
                        })

                        # 4. 帶前置條件的漸進式生成
                        for period in chart_generation_checkpoints:
                            if period not in generated_periods and len(rates_data) >= chart_generation_checkpoints[period]:
                                # 檢查是否有足夠時間範圍的數據
                                required_start_date = end_date - timedelta(days=period)
                                has_relevant_data = any(datetime.strptime(d, '%Y-%m-%d') >= required_start_date for d in rates_data)
                                
                                if has_relevant_data:
                                    chart_info = self.build_chart_with_cache(period, buy_currency, sell_currency, live_rates_data=rates_data)
                                    if chart_info:
                                        print(f"✅ 背景任務：成功生成並快取了 {period} 天圖表。")
                                        generated_periods.add(period)
                                        # 修正：傳送前端期望的扁平化資料結構
                                        send_sse_event('chart_ready', {
                                            'buy_currency': buy_currency,
                                            'sell_currency': sell_currency,
                                            'period': period,
                                            'chart_url': chart_info['chart_url'],
                                            'stats': chart_info['stats']
                                        })

                # 5. 最終補全
                final_periods_to_generate = set(chart_generation_checkpoints.keys()) - generated_periods
                if final_periods_to_generate:
                    print(f"背景任務：獲取完所有數據，嘗試補全未生成的圖表: {final_periods_to_generate}")
                    for period in final_periods_to_generate:
                        chart_info = self.build_chart_with_cache(period, buy_currency, sell_currency, live_rates_data=rates_data)
                        if chart_info:
                            generated_periods.add(period)
                            # 修正：傳送前端期望的扁平化資料結構
                            send_sse_event('chart_ready', {
                                'buy_currency': buy_currency,
                                'sell_currency': sell_currency,
                                'period': period,
                                'chart_url': chart_info['chart_url'],
                                'stats': chart_info['stats']
                            })

                # 6. 最終日誌
                if len(generated_periods) == 4:
                    print(f"✅ 背景任務圓滿完成: {buy_currency}-{sell_currency} 的全部4張圖表均已生成。")
                else:
                    print(f"⚠️ 背景任務結束，但有缺漏: 為 {buy_currency}-{sell_currency} 生成了 {len(generated_periods)}/{4} 張圖表。")

            except Exception as e:
                logger.error(f"❌ 背景任務失敗 ({buy_currency}-{sell_currency}): {e}", exc_info=True)
            finally:
                with self._active_fetch_lock:
                    self._active_fetches.discard((buy_currency, sell_currency))
                    print(f"🔑 背景任務解鎖: {buy_currency}-{sell_currency}。")

    def create_chart(self, days, buy_currency, sell_currency):
        """創建圖表（帶 LRU Cache 和背景抓取協調）"""
        cache_key = f"chart_{buy_currency}_{sell_currency}_{days}"

        # 1. 檢查快取
        cached_info = self.lru_cache.get(cache_key)
        if cached_info:
            chart_url = cached_info.get('chart_url', '')
            if chart_url and os.path.exists(os.path.join(self.charts_dir, os.path.basename(chart_url))):
                # 對於 TWD-HKD，額外檢查數據是否有更新
                if buy_currency == 'TWD' and sell_currency == 'HKD':
                    # 檢查數據文件的最新日期
                    if self.data:
                        sorted_dates = self.get_sorted_dates()
                        if sorted_dates:
                            latest_data_date = sorted_dates[-1]
                            # 從圖表 URL 提取日期（格式：chart_TWD-HKD_180d_2025-12-17_hash.png）
                            url_parts = chart_url.split('_')
                            if len(url_parts) >= 4:
                                cached_date = url_parts[3]  # 2025-12-17
                                # 如果數據更新了，清除快取重新生成
                                if latest_data_date > cached_date:
                                    print(f"🔄 檢測到數據更新（{cached_date} -> {latest_data_date}），重新生成圖表")
                                    with self.lru_cache.lock:
                                        if cache_key in self.lru_cache.cache:
                                            del self.lru_cache.cache[cache_key]
                                        if cache_key in self.lru_cache.access_order:
                                            self.lru_cache.access_order.remove(cache_key)
                                    cached_info = None
                
                if cached_info:
                    return cached_info

        # --- 快取未命中 ---
        
        # 對於 TWD-HKD，邏輯很簡單，直接同步重新生成
        if buy_currency == 'TWD' and sell_currency == 'HKD':
            return self.build_chart_with_cache(days, buy_currency, sell_currency)

        # --- 對於其他貨幣對，需要協調背景抓取 ---
        with self._active_fetch_lock:
            if (buy_currency, sell_currency) not in self._active_fetches:
                print(f"🌀 {buy_currency}-{sell_currency} 的背景抓取尚未啟動，現在於背景開始...")
                self._active_fetches.add((buy_currency, sell_currency))
                # 傳入 Flask app 物件，確保背景執行可建立 app_context
                flask_app = current_app._get_current_object()
                self.background_executor.submit(self._background_fetch_and_generate, buy_currency, sell_currency, flask_app)
            else:
                print(f"✅ 預生成: {buy_currency}-{sell_currency} 的背景抓取已在進行中。")

        # 改為快速返回，讓前端透過 SSE 的 chart_ready 事件更新，不阻塞請求
        return None

    def build_chart_with_cache(self, days, buy_currency, sell_currency, live_rates_data=None):
        """
        內部輔助函數：重新生成圖表並更新快取。
        可選擇傳入已獲取的即時數據以避免重複請求。
        """
        all_dates_str, all_rates = [], []
        is_pinned = False

        if buy_currency == 'TWD' and sell_currency == 'HKD':
            # 對於 TWD-HKD，從本地數據獲取
            all_dates_obj, all_rates = self.extract_local_rates(days)
            if not all_dates_obj:
                return None
            all_dates_str = [d.strftime('%Y-%m-%d') for d in all_dates_obj]
            is_pinned = True
        elif live_rates_data:
            # 如果傳入了預加載的數據，直接使用
            all_dates_str_sorted = sorted(live_rates_data.keys())
            
            # 根據天數篩選數據
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            # 從已有的數據中篩選出符合期間的
            filtered_dates = [d for d in all_dates_str_sorted if start_date <= datetime.strptime(d, '%Y-%m-%d') <= end_date]
            
            # 如果篩選後數據不足，則不生成圖表
            if not filtered_dates:
                 return None

            all_dates_str = filtered_dates
            all_rates = [live_rates_data[d] for d in all_dates_str]
            is_pinned = False
        else:
            # 對於其他貨幣對，從即時 API 獲取
            live_rates_data = self.get_live_rates_for_period(days, buy_currency, sell_currency)
            if not live_rates_data:
                return None
            all_dates_str = sorted(live_rates_data.keys())
            all_rates = [live_rates_data[d] for d in all_dates_str]
            is_pinned = False

        # --- 數據獲取完成後 ---
        if not all_dates_str or not all_rates:
            return None # 沒有足夠數據生成圖表

        # --- 生成圖表和統計數據 ---
        chart_url = self.render_chart_image(days, all_dates_str, all_rates, buy_currency, sell_currency)
        if not chart_url:
            return None

        all_dates_obj = [datetime.strptime(d, '%Y-%m-%d') for d in all_dates_str]
        stats = self._calculate_stats(all_rates, [d.strftime('%Y-%m-%d') for d in all_dates_obj])
        
        # --- 建立完整的圖表資訊對象 (已移除數據指紋) ---
        chart_info = {
            'chart_url': chart_url,
            'stats': stats,
            'generated_at': datetime.now().isoformat(),
            'is_pinned': is_pinned
        }
        
        # --- 更新快取 ---
        # 這是關鍵的修復：確保 build_chart_with_cache 自身就能更新快取
        cache_key = f"chart_{buy_currency}_{sell_currency}_{days}"
        self.lru_cache.put(cache_key, chart_info)
        current_app.logger.info(f"💾 CACHE SET (from regenerate): Stored chart for {buy_currency}-{sell_currency} ({days} days)")

        return chart_info

    def render_chart_image(self, days, all_dates_str, all_rates, buy_currency, sell_currency):
        """
        從提供的數據生成圖表，並將其保存為文件，返回其 URL 路徑。
        all_dates_str 應為 'YYYY-MM-DD' 格式的字符串列表。
        """
        if not all_dates_str or not all_rates:
            return None

        # 生成可讀性更高且唯一的檔名（使用關鍵資訊而非全部資料以提升效能）
        latest_date_str = all_dates_str[-1] if all_dates_str else "nodate"
        first_date_str = all_dates_str[0] if all_dates_str else "nodate"
        first_rate = all_rates[0] if all_rates else 0
        last_rate = all_rates[-1] if all_rates else 0
        data_count = len(all_dates_str)
        
        # 輕量級雜湊字串：只使用關鍵資訊確保唯一性
        data_str = f"{days}-{buy_currency}-{sell_currency}-{first_date_str}-{latest_date_str}-{data_count}-{first_rate}-{last_rate}"
        chart_hash = hashlib.md5(data_str.encode('utf-8')).hexdigest()
        filename = f"chart_{buy_currency}-{sell_currency}_{days}d_{latest_date_str}_{chart_hash[:8]}.png"

        relative_path = os.path.join('charts', filename)
        full_path = os.path.join(self.charts_dir, filename)

        if os.path.exists(full_path):
            return f"/static/{relative_path.replace(os.path.sep, '/')}"

        # 創建圖表
        fig, ax = plt.subplots(figsize=(15, 8.5))
        
        # 轉換日期
        dates = [datetime.strptime(d, '%Y-%m-%d') for d in all_dates_str]
        rates = all_rates

        # 改成使用索引作為 X 軸，以確保間距相等
        x_indices = range(len(dates))
        ax.plot(x_indices, rates, marker='o', linewidth=2, markersize=4, color='#2E86AB')
        
        # 設定標題
        period_names = {7: '近1週', 30: '近1個月', 90: '近3個月', 180: '近6個月'}
        # 假設匯率是 TWD -> HKD，標題顯示 HKD -> TWD，所以是 1 TWD = X HKD
        title = f'{buy_currency} 到 {sell_currency} 匯率走勢圖 ({period_names.get(days, f"近{days}天")})'
        ax.set_title(title, fontsize=16, fontweight='bold', pad=20)
        ax.set_xlabel('日期', fontsize=12)
        ax.set_ylabel('匯率', fontsize=12)
        
        # 使用 MaxNLocator 自動決定 X 軸刻度，並確保最後一天總是被顯示
        
        # 根據圖表天數設定理想的刻度數量
        if days <= 10:
            nbins = 10
        elif days <= 30:
            nbins = 15
        elif days <= 90:
            nbins = 12
        else:  # 180 days
            nbins = 15

        if len(x_indices) > 1:
            locator = MaxNLocator(nbins=nbins, integer=True, min_n_ticks=3)
            # 獲取自動計算的刻度位置，並過濾掉超出範圍的值
            tick_indices = [int(i) for i in locator.tick_values(0, len(x_indices) - 1) 
                          if 0 <= i < len(x_indices)]
            
            # 確保最後一個數據點總是被顯示
            last_index = len(x_indices) - 1
            if last_index not in tick_indices and tick_indices:
                # 計算平均刻度間距
                if len(tick_indices) > 1:
                    avg_spacing = (tick_indices[-1] - tick_indices[0]) / (len(tick_indices) - 1)
                else:
                    avg_spacing = last_index / max(1, nbins)
                
                distance_to_last = last_index - tick_indices[-1]
                
                # 策略：如果距離太近（< 40% 平均間距），則替換最後一個刻度
                # 如果距離適中（>= 40%），則添加最後一個刻度
                if distance_to_last < avg_spacing * 0.4:
                    # 距離太近，替換最後一個刻度
                    tick_indices[-1] = last_index
                else:
                    # 距離足夠，直接添加
                    tick_indices.append(last_index)
            
            # 移除重複值並排序
            tick_indices = sorted(list(set(tick_indices)))

        elif x_indices:
            tick_indices = [x_indices[0]]
        else:
            tick_indices = []
        
        if tick_indices:
            # 設置刻度和標籤
            ax.set_xticks(tick_indices)
            ax.set_xticklabels([dates[i].strftime('%m/%d') for i in tick_indices])

        ax.tick_params(axis='x', which='major', pad=8)
        
        # 添加網格
        ax.grid(True, alpha=0.3)
        
        # 為 Y 軸設定 MaxNLocator 和 Formatter 以獲得更清晰且格式統一的刻度
        ax.yaxis.set_major_locator(MaxNLocator(nbins=10, prune='both', min_n_ticks=5))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f'{y:.4f}'))
        
        # 添加平均線
        if rates:
            avg_rate = sum(rates) / len(rates)
            ax.axhline(y=avg_rate, color='orange', linestyle='--', linewidth=1.5, alpha=0.8, label=f'平均值: {avg_rate:.4f}')
            ax.legend(loc='upper right', fontsize=10)
        
        # 設定 Y 軸範圍
        if rates:
            y_min, y_max = min(rates), max(rates)
            y_range = y_max - y_min if y_max > y_min else 0.1
            if days >= 30:
                ax.set_ylim(y_min - y_range * 0.05, y_max + y_range * 0.15)
            else:
                ax.set_ylim(y_min - y_range * 0.05, y_max + y_range * 0.12)
        
        # 標記最高點和最低點
        if rates:
            max_rate = max(rates)
            min_rate = min(rates)
            max_index = rates.index(max_rate)
            min_index = rates.index(min_rate)
            
            # 標記最高點
            ax.annotate(f'{max_rate:.4f}', 
                       (max_index, max_rate), 
                       textcoords="offset points", 
                       xytext=(0,10), 
                       ha='center',
                       va='bottom',
                       fontsize=9,
                       color='red',
                       fontweight='bold',
                       bbox=dict(boxstyle="round", facecolor='white', alpha=0.6, edgecolor='none'))
            
            # 標記最低點
            ax.annotate(f'{min_rate:.4f}', 
                       (min_index, min_rate), 
                       textcoords="offset points", 
                       xytext=(0,10), # 調整y偏移以避免重疊
                       ha='center',
                       va='bottom',
                       fontsize=9,
                       color='green',
                       fontweight='bold',
                       bbox=dict(boxstyle="round", facecolor='white', alpha=0.6, edgecolor='none'))
        
        # 手動調整佈局
        fig.subplots_adjust(left=0.08, right=0.95, top=0.85, bottom=0.20)
        
        try:
            fig.savefig(full_path, format='png', transparent=False, bbox_inches='tight', facecolor='white')
        except Exception as e:
            print(f"儲存圖表時出錯: {e}")
            plt.close(fig)
            return None
        finally:
            plt.close(fig)
        
        self._cleanup_charts_directory(self.charts_dir, max_age_days=1)
        
        # 返回 Flask 能識別的靜態文件 URL
        return f"/static/{relative_path.replace(os.path.sep, '/')}"

    def warm_up_chart_cache(self, buy_currency='TWD', sell_currency='HKD'):
        """
        為常用週期預熱圖表快取。
        此函數只提交任務，不阻塞。
        會根據貨幣對類型選擇不同的執行策略。
        """
        flask_app = current_app._get_current_object()

        # 策略一：對於 TWD-HKD，我們有本地數據，可以直接生成圖表並通知
        if buy_currency == 'TWD' and sell_currency == 'HKD':
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 觸發 {buy_currency}-{sell_currency} 圖表直接生成...")

            for period in [7, 30, 90, 180]:
                def generate_and_notify(manager_instance, period, app_context):
                    with app_context.app_context():
                        try:
                            chart_info = manager_instance.create_chart(period, buy_currency, sell_currency)
                            if not chart_info or not chart_info.get('chart_url'):
                                raise ValueError("圖表生成返回了無效的數據")
                            
                            # 修正：傳送前端期望的扁平化資料結構
                            send_sse_event('chart_ready', {
                                'message': f'圖表 {buy_currency}-{sell_currency} ({period}d) 已生成',
                                'buy_currency': buy_currency,
                                'sell_currency': sell_currency,
                                'period': period,
                                'chart_url': chart_info['chart_url'],
                                'stats': chart_info['stats']
                            })
                        except Exception as e:
                            error_message = f"背景任務中為 {buy_currency}-{sell_currency} ({period}d) 生成圖表時出錯: {e}"
                            print(f"❌ {error_message}")
                            send_sse_event('chart_error', {
                                'message': error_message, 'buy_currency': buy_currency,
                                'sell_currency': sell_currency, 'period': period
                            })
                
                self.background_executor.submit(generate_and_notify, self, period, flask_app)

        # 策略二：對於其他貨幣對，我們需要先抓取數據，然後再生成圖表
        else:
            with self._active_fetch_lock:
                if (buy_currency, sell_currency) not in self._active_fetches:
                    print(f"🌀 {buy_currency}-{sell_currency} 的背景抓取任務已啟動...")
                    self._active_fetches.add((buy_currency, sell_currency))
                    # 提交的是 _background_fetch_and_generate 任務，並傳遞 flask_app
                    self.background_executor.submit(self._background_fetch_and_generate, buy_currency, sell_currency, flask_app)
                else:
                    print(f"✅ {buy_currency}-{sell_currency} 的背景抓取已在進行中，無需重複啟動。")

    @staticmethod
    def _cleanup_charts_directory(directory, max_age_days=1):
        """清理超過指定天數的舊圖表檔案"""
        try:
            current_time = time.time()
            for filename in os.listdir(directory):
                file_path = os.path.join(directory, filename)
                if os.path.isfile(file_path):
                    file_age = current_time - os.path.getmtime(file_path)
                    if file_age > max_age_days * 24 * 3600:
                        os.remove(file_path)
        except Exception as e:
            print(f"清理圖表目錄時出錯: {e}")

    def clear_expired_cache(self):
        """清理過期的快取項目"""
        cleared_count = self.lru_cache.clear_expired()
        if cleared_count > 0:
            print(f"🧹 快取清理完成：圖表快取過期 {cleared_count} 項")
        return cleared_count

    def _calculate_stats(self, rates, dates_str):
        if not rates or not dates_str:
            return None
        return {
            'max_rate': max(rates),
            'min_rate': min(rates),
            'avg_rate': sum(rates) / len(rates),
            'data_points': len(rates),
            'date_range': f"{dates_str[0]} 至 {dates_str[-1]}"
        }

    def get_current_rate(self, buy_currency, sell_currency):
        """
        獲取最新匯率，整合了 TWD-HKD 本地數據、其他貨幣對的 LRU 快取和 API 後備機制。
        這是獲取最新匯率的唯一真實來源 (Single Source of Truth)。
        """
        # --- 優先處理 TWD-HKD: 從本地 JSON 數據獲取 ---
        if buy_currency == 'TWD' and sell_currency == 'HKD':
            current_app.logger.info(f"從本地文件獲取 TWD-HKD 最新匯率")
            with self.data_lock:
                if not self.data:
                    return None
                sorted_dates = self.get_sorted_dates()
                if not sorted_dates:
                    return None
                
                latest_date_str = sorted_dates[-1]
                latest_data = self.data[latest_date_str]
                latest_rate = latest_data['rate']
                
                trend, trend_value = None, 0
                if len(sorted_dates) > 1:
                    previous_date_str = sorted_dates[-2]
                    previous_rate = self.data[previous_date_str]['rate']
                    trend_value = latest_rate - previous_rate
                    if trend_value > 0.00001: trend = 'up'
                    elif trend_value < -0.00001: trend = 'down'
                    else: trend = 'same'
                
                return {
                    'date': latest_date_str, 'rate': latest_rate, 'trend': trend,
                    'trend_value': trend_value, 'source': 'local_file',
                    'updated_time': latest_data.get('updated', datetime.now().isoformat())
                }

        # --- 其他貨幣對：走 LRU 快取 -> API 抓取 的流程 ---
        cache_key = (buy_currency, sell_currency)
        
        # 1. 嘗試從快取中獲取數據
        cached_rate = self.latest_rate_cache.get(cache_key)
        if cached_rate:
            current_app.logger.info(f"✅ API LATEST (CACHE): {buy_currency}-{sell_currency} - 成功從快取提供")
            response_data = cached_rate.copy()
            response_data['source'] = 'cache'
            return response_data

        # 2. 如果快取未命中，則從 API 即時抓取
        current_app.logger.info(f"🔄 API LATEST (FETCH): {buy_currency}-{sell_currency} - 快取未命中，嘗試從 API 獲取...")
        current_date = datetime.now()
        while current_date.weekday() >= 5: # 尋找最近的工作日
            current_date -= timedelta(days=1)

        rate_data = self.get_exchange_rate(current_date, buy_currency, sell_currency)

        if not rate_data or 'data' not in rate_data:
            current_app.logger.error(f"❌ API LATEST (FAIL): {buy_currency}-{sell_currency} - API 抓取失敗。")
            return None

        # 3. 解析成功後，將新數據存入快取
        try:
            conversion_rate = float(rate_data['data']['conversionRate'])
            latest_data = {
                'date': current_date.strftime('%Y-%m-%d'),
                'rate': conversion_rate,
                'trend': None, 'trend_value': 0,
                'updated_time': datetime.now().isoformat()
            }
            self.latest_rate_cache.put(cache_key, latest_data)
            current_app.logger.info(f"💾 API LATEST (STORE): {buy_currency}-{sell_currency} - 成功獲取並存入快取")
            
            # 計算過去各期間最低匯率，優先 7, 30, 90, 180
            lowest_rate = None
            lowest_period = None
            for p in [7, 30, 90, 180]:
                dates, rates = self.extract_local_rates(p)
                if rates:
                    lowest_rate = min(rates)
                    lowest_period = p
                    break
            if lowest_rate is None:
                dates30, rates30 = self.extract_local_rates(30)
                if rates30:
                    lowest_rate = min(rates30)
                    lowest_period = 30
            if lowest_rate is not None:
                latest_data['lowest_rate'] = lowest_rate
                latest_data['lowest_period'] = lowest_period
            # 加入貨幣代碼以供前端顯示
            latest_data['buy_currency'] = buy_currency
            latest_data['sell_currency'] = sell_currency
            return latest_data
        except (KeyError, ValueError, TypeError) as e:
            current_app.logger.error(f"❌ API LATEST (PARSE FAIL): 為 {buy_currency}-{sell_currency} 解析即時抓取數據時出錯: {e}")
            return None 

    def get_cached_pairs(self):
        """獲取所有快取中的貨幣對"""
        try:
            pairs = set()

            # 安全地清理和獲取圖表快取
            try:
                self.lru_cache.clear_expired()
                with self.lru_cache.lock:
                    for key in list(self.lru_cache.cache.keys()):
                        # 目前圖表快取鍵為字串: chart_{buy}_{sell}_{days}
                        if isinstance(key, str) and key.startswith('chart_'):
                            parts = key.split('_')
                            if len(parts) >= 4:
                                buy = parts[1]
                                sell = parts[2]
                                pairs.add((buy, sell))
                        # 兼容舊版 tuple 形式
                        elif isinstance(key, tuple) and len(key) == 3:
                            _, buy, sell = key
                            pairs.add((buy, sell))
            except Exception as e:
                print(f"⚠️ 獲取圖表快取時發生錯誤: {e}")

            # 安全地清理和獲取匯率快取
            try:
                self.latest_rate_cache.clear_expired()
                with self.latest_rate_cache.lock:
                    for key in list(self.latest_rate_cache.cache.keys()):
                        if isinstance(key, tuple) and len(key) == 2:
                            buy, sell = key
                            pairs.add((buy, sell))
            except Exception as e:
                print(f"⚠️ 獲取匯率快取時發生錯誤: {e}")
            
            # 轉換為列表並排序
            sorted_pairs = sorted(list(pairs))
            
            return [{'buy_currency': p[0], 'sell_currency': p[1]} for p in sorted_pairs]
            
        except Exception as e:
            print(f"❌ get_cached_pairs 發生錯誤: {e}")
            return [] 