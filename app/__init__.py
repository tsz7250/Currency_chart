from flask import Flask
import os
import atexit
import signal
import sys
import logging
import matplotlib
import matplotlib.font_manager as fm
from datetime import datetime, timedelta
from .exchange_rate_manager import ExchangeRateManager
from .scheduler import init_scheduler


def should_cleanup_today():
    """檢查今天是否需要清理日誌（檢查是否已有今天的日誌）"""
    if not os.path.exists('app.log'):
        return False
    
    try:
        today_str = datetime.now().strftime('%Y-%m-%d')
        with open('app.log', 'r', encoding='utf-8') as f:
            # 檢查前10行是否有今天的日誌（效率考量）
            for i, line in enumerate(f):
                if i >= 10:
                    break
                if line.startswith(f'[{today_str}'):
                    # 找到今天的日誌，說明今天已啟動過並開始記錄
                    return False
        
        # 沒有找到今天的日誌，需要清理昨天的
        return True
    except Exception:
        pass
    
    return False


def cleanup_daily_logs():
    """清理日誌，只保留錯誤級別的記錄"""
    if not os.path.exists('app.log'):
        return
    
    try:
        # 讀取現有日誌
        with open('app.log', 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # 只保留日誌級別為 ERROR 或 CRITICAL 的行
        # 檢查 "] ERROR " 或 "] CRITICAL " 模式以確保匹配的是日誌級別而非消息內容
        error_lines = [line for line in lines if '] ERROR ' in line or '] CRITICAL ' in line]
        
        if error_lines:
            # 寫回只有錯誤的日誌
            with open('app.log', 'w', encoding='utf-8') as f:
                f.writelines(error_lines)
            print(f"🧹 已清理日誌，保留 {len(error_lines)} 條錯誤記錄")
        else:
            # 沒有錯誤，刪除文件
            os.remove('app.log')
            print("🧹 已清理日誌，無錯誤記錄")
    except Exception as e:
        print(f"⚠️ 清理日誌時發生錯誤: {e}")


def setup_logging(app):
    """配置應用程式日誌"""
    log_level = 'INFO'  # 可在此處直接修改日誌級別
    
    today = datetime.now()
    
    # 每月1號完全清空
    if today.day == 1 and os.path.exists('app.log'):
        try:
            file_size = os.path.getsize('app.log') / 1024 / 1024
            os.remove('app.log')
            print(f"🗑️ 每月清空：已刪除舊日誌 (大小: {file_size:.2f} MB)")
        except Exception as e:
            print(f"⚠️ 清空日誌時發生錯誤: {e}")
    # 每天清理（只保留錯誤），通過讀取第一行判斷是否今天已清理
    elif should_cleanup_today():
        cleanup_daily_logs()
    
    # 格式化器
    formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # 文件處理器（單一檔案，持續追加）
    file_handler = logging.FileHandler('app.log', encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(getattr(logging, log_level))
    
    # 控制台處理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, log_level))
    
    # 配置 app logger
    app.logger.addHandler(file_handler)
    app.logger.addHandler(console_handler)
    app.logger.setLevel(getattr(logging, log_level))
    
    # 配置根 logger
    logging.basicConfig(level=getattr(logging, log_level), handlers=[])


def auto_update_data():
    """自動判斷並更新數據"""
    from .mastercard_scraper import MastercardScraper
    from .cookie_fetcher import CookieFetcher
    import json
    import time
    import random
    
    DATA_FILE = 'TWD-HKD_180d.json'
    COOKIES_FILE = 'mastercard_cookies.json'
    
    # 載入本地數據
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            local_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, IOError) as e:
        print(f"⚠️ 載入本地數據失敗: {e}")
        local_data = {}
    
    # 找出應該有數據的最新工作日
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    expected_date = today
    # 如果是週末，回退到上週五
    while expected_date.weekday() >= 5:
        expected_date -= timedelta(days=1)
    
    expected_date_str = expected_date.strftime('%Y-%m-%d')
    
    # 檢查是否需要更新
    if expected_date_str in local_data:
        print(f"✅ 本地數據已是最新（{expected_date_str}）")
        return
    
    print(f"⚠️ 需要更新數據（缺少 {expected_date_str}）")
    
    # 檢查 cookies 是否存在
    if not os.path.exists(COOKIES_FILE):
        print("🍪 Cookies 文件不存在，正在自動獲取...")
        print("   ⏰ 瀏覽器窗口將顯示約 10 秒...")
        try:
            fetcher = CookieFetcher(COOKIES_FILE)
            success = fetcher.fetch_and_save(headless=False, wait_time=10)
            if not success:
                print("❌ 無法自動獲取 cookies")
                print("   💡 提示：請手動運行以下命令：")
                print("      python app\\cookie_fetcher.py")
                return
        except Exception as e:
            print(f"❌ 獲取 cookies 時發生錯誤: {e}")
            print("   💡 提示：請手動運行以下命令：")
            print("      python app\\cookie_fetcher.py")
            return
    
    # 使用 scraper 更新數據
    print("🔄 正在更新匯率數據...")
    try:
        scraper = MastercardScraper(COOKIES_FILE)
        
        # 找出需要更新的日期範圍
        if local_data:
            latest_date_str = max(local_data.keys())
            latest_date = datetime.strptime(latest_date_str, '%Y-%m-%d')
            print(f"   本地最新數據：{latest_date_str}")
        else:
            # 如果沒有數據，從 180 天前開始
            latest_date = today - timedelta(days=181)
            print(f"   本地無數據，將獲取最近 180 天")
        
        start_fetch_date = latest_date + timedelta(days=1)
        current_date = start_fetch_date
        updated_count = 0
        failed_count = 0
        
        cookies_refreshed = False  # 標記是否已經嘗試過刷新 cookies
        
        while current_date <= today:
            if current_date.weekday() < 5:  # 只更新工作日
                date_str = current_date.strftime('%Y-%m-%d')
                if date_str not in local_data:
                    data = scraper.get_exchange_rate(current_date)
                    
                    if data and 'data' in data and 'conversionRate' in data['data']:
                        try:
                            rate = float(data['data']['conversionRate'])
                            local_data[date_str] = {
                                'rate': rate,
                                'updated': datetime.now().isoformat()
                            }
                            print(f"   ✅ {date_str}: {rate}")
                            updated_count += 1
                            failed_count = 0  # 成功後重置失敗計數
                            
                            # 隨機延遲 1-2 秒，避免請求過快
                            time.sleep(random.uniform(1, 2))
                        except (KeyError, ValueError) as e:
                            print(f"   ❌ 解析 {date_str} 失敗: {e}")
                            failed_count += 1
                    else:
                        print(f"   ⚠️ 無法獲取 {date_str}")
                        failed_count += 1
                        
                        # 如果失敗且還沒有嘗試過刷新 cookies，立即嘗試刷新
                        if not cookies_refreshed and failed_count >= 1:
                            print("   🍪 檢測到獲取失敗，可能是 Cookies 過期")
                            print("   ⏰ 正在自動重新獲取 Cookies（瀏覽器將顯示約 10 秒）...")
                            try:
                                fetcher = CookieFetcher(COOKIES_FILE)
                                success = fetcher.fetch_and_save(headless=False, wait_time=10)
                                if success:
                                    print("   ✅ Cookies 更新成功，重新嘗試獲取數據...")
                                    scraper = MastercardScraper(COOKIES_FILE)  # 重新載入 cookies
                                    failed_count = 0  # 重置失敗計數
                                    cookies_refreshed = True  # 標記已刷新
                                    
                                    # 重新嘗試獲取當前日期的數據
                                    data = scraper.get_exchange_rate(current_date)
                                    if data and 'data' in data and 'conversionRate' in data['data']:
                                        try:
                                            rate = float(data['data']['conversionRate'])
                                            local_data[date_str] = {
                                                'rate': rate,
                                                'updated': datetime.now().isoformat()
                                            }
                                            print(f"   ✅ {date_str}: {rate}")
                                            updated_count += 1
                                        except (KeyError, ValueError) as e:
                                            print(f"   ❌ 重試後解析 {date_str} 仍然失敗: {e}")
                                    else:
                                        print(f"   ⚠️ 即使更新 cookies 後仍無法獲取 {date_str}")
                                else:
                                    print("   ❌ 無法重新獲取 cookies，停止更新")
                                    print("   💡 請手動運行: python app/cookie_fetcher.py")
                                    break
                            except Exception as e:
                                print(f"   ❌ 重新獲取 cookies 時發生錯誤: {e}")
                                print("   💡 請手動運行: python app/cookie_fetcher.py")
                                break
                        elif cookies_refreshed and failed_count >= 2:
                            # 如果已經刷新過 cookies 但還是連續失敗，停止更新
                            print("   ❌ 即使更新 cookies 後仍連續失敗，停止更新")
                            print("   💡 可能是網站維護或其他問題，請稍後重試")
                            break
            
            current_date += timedelta(days=1)
        
        # 保存更新的數據
        if updated_count > 0:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(local_data, f, ensure_ascii=False, indent=2)
            print(f"💾 已保存 {updated_count} 筆新數據到 {DATA_FILE}")
            print(f"✅ 數據更新完成！")
        else:
            print("⚠️ 沒有獲取到新數據")
            print("   💡 提示：請檢查網路連接或稍後再試（系統會在下次啟動或排程時自動重試）")
            
    except Exception as e:
        print(f"❌ 更新數據時發生錯誤: {e}")
        print("   💡 提示：請檢查網路與 cookies 狀態，系統會在之後自動重試")

def create_app():
    # 設定非 GUI 後端
    matplotlib.use('Agg')

    app = Flask(__name__, static_folder='../static', template_folder='../templates')
    
    # 設置日誌系統
    setup_logging(app)

    with app.app_context():
        # 在創建 manager 之前先檢查並更新數據
        print("🔄 檢查並更新數據...")
        auto_update_data()
    
    # 建立服務實例並附加到 app（載入更新後的數據）
    app.manager = ExchangeRateManager()

    with app.app_context():
        # 設定中文字體
        font_path = os.path.join(os.path.dirname(__file__), '..', 'fonts', 'NotoSansTC-Regular.ttf')
        if os.path.exists(font_path):
            fm.fontManager.addfont(font_path)
            font_prop = fm.FontProperties(fname=font_path)
            matplotlib.rcParams['font.sans-serif'] = [font_prop.get_name()]
        else:
            try:
                matplotlib.rcParams['font.sans-serif'] = ['Noto Sans CJK TC']
                print("使用系統字體: Noto Sans CJK TC")
            except Exception as e:
                matplotlib.rcParams['font.sans-serif'] = ['DejaVu Sans']
                print(f"警告: 未找到中文字體: {e}")
                print("請將 NotoSansTC-Regular.ttf 放入 fonts/ 資料夾")
        matplotlib.rcParams['axes.unicode_minus'] = False
        
        # 引入並註冊藍圖
        from . import routes
        app.register_blueprint(routes.bp)

        # 在應用程式啟動時執行一次性任務
        print("🧹 清理舊的圖表文件...")
        app.manager._cleanup_charts_directory(app.manager.charts_dir, max_age_days=0)
        
        # 清理舊數據
        app.manager.update_data(180)
        
        print("📊 預生成圖表...")
        app.manager.warm_up_chart_cache()

        # 啟動定時任務
        init_scheduler(app)
    
    # 註冊清理函數
    @app.teardown_appcontext
    def cleanup_browser(exception=None):
        """在 app context 結束時清理瀏覽器資源"""
        pass  # Context 級別的清理（如果需要）
    
    # 註冊程式退出時的清理函數
    def cleanup_on_exit():
        """在程式退出時清理所有資源"""
        print("\n🛑 正在關閉應用程式...")
        if hasattr(app, 'manager'):
            try:
                app.manager.shutdown()
            except Exception as e:
                print(f"⚠️ 清理資源時發生錯誤: {e}")
        print("✅ 清理完成")
    
    # 註冊 atexit 清理函數
    atexit.register(cleanup_on_exit)
    
    # 註冊信號處理器（處理 Ctrl+C 等）
    def signal_handler(sig, frame):
        print("\n🛑 收到終止信號...")
        cleanup_on_exit()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    return app 