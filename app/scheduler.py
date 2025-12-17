import schedule
import time
import logging
from datetime import datetime
from threading import Thread
from .sse import send_sse_event

logger = logging.getLogger(__name__)
_app = None

def scheduled_update():
    """定時重新載入本地數據
    
    數據由應用啟動邏輯與排程自動更新，本函數負責重新載入並通知前端。
    """
    
    if not _app:
        return
        
    with _app.app_context():
        manager = _app.manager
        try:
            logger.info("開始重新載入本地數據...")
            today = datetime.now()
            today_str = today.strftime('%Y-%m-%d')

            # 重新載入數據（可能已被外部腳本更新）
            old_count = len(manager.data)
            manager.data = manager.load_data()
            new_count = len(manager.data)
            
            # 檢查今天的資料是否存在
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
                logger.warning(f"⚠️ 本地數據中沒有今天的資料")
                logger.info("系統將在之後的啟動或排程中繼續嘗試更新（請檢查網路與 cookies 狀態）")
            
            if new_count != old_count:
                logger.info(f"數據量變化：{old_count} → {new_count}")

        except Exception as e:
            logger.error(f"重新載入數據失敗: {str(e)}", exc_info=True)

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
    schedule.every().hour.do(clear_cache_with_context)
    
    scheduler_thread = Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("✅ 定時任務已啟動") 