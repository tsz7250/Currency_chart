from flask import Blueprint, render_template, request, jsonify, Response, current_app
from datetime import datetime
import time
import queue
import schedule
import uuid

from .sse import sse_clients, sse_lock, sse_stream
from .scheduler import scheduled_update
from .utils import validate_currency_code, validate_period

bp = Blueprint('main', __name__)

SERVER_INSTANCE_ID = str(uuid.uuid4())

@bp.route('/')
def index():
    """主頁面"""
    return render_template('index.html')

@bp.route('/api/chart')
def get_chart():
    """獲取圖表API - 支援多幣種並統一使用伺服器快取"""
    start_time = time.time()
    
    period = request.args.get('period', '7')
    buy_currency = request.args.get('buy_currency', 'TWD')
    sell_currency = request.args.get('sell_currency', 'HKD')

    # 驗證 period 參數
    try:
        days = int(period)
    except ValueError:
        return jsonify({'error': '無效的 period 參數，必須是整數'}), 400
    
    if not validate_period(days):
        return jsonify({'error': f'無效的時間範圍，僅支援 7, 30, 90, 180 天'}), 400
    
    # 驗證貨幣代碼格式
    if not validate_currency_code(buy_currency):
        return jsonify({'error': f'無效的買入貨幣代碼格式：{buy_currency}'}), 400
    
    if not validate_currency_code(sell_currency):
        return jsonify({'error': f'無效的賣出貨幣代碼格式：{sell_currency}'}), 400

    try:
        chart_data = current_app.manager.create_chart(days, buy_currency, sell_currency)
        processing_time = time.time() - start_time
        
        if chart_data and chart_data.get('dates'):
            chart_data['processing_time'] = round(processing_time, 3)
            chart_data['processing_time_ms'] = round(processing_time * 1000, 1)
            return jsonify(chart_data)
        else:
            # 提供更詳細的錯誤信息
            data_count = len(current_app.manager.data) if hasattr(current_app.manager, 'data') else 0
            error_details = {
                'error': '無法生成圖表', 
                'no_data': True, 
                'processing_time': round(processing_time, 3),
                'data_available': data_count,
                'period_requested': days,
                'currency_pair': f"{buy_currency}-{sell_currency}"
            }
            return jsonify(error_details), 500
            
    except Exception as e:
        processing_time = time.time() - start_time
        current_app.logger.error(f"處理圖表請求時發生未預期的錯誤: {e}", exc_info=True)
        error_details = {
            'error': '伺服器內部錯誤', 
            'processing_time': round(processing_time, 3),
            'error_type': type(e).__name__,
            'currency_pair': f"{buy_currency}-{sell_currency}"
        }
        return jsonify(error_details), 500

@bp.route('/api/latest_rate')
def get_latest_rate():
    """獲取最新匯率的API端點，完全依賴 ExchangeRateManager 處理"""
    start_time = time.time()
    
    buy_currency = request.args.get('buy_currency', 'TWD')
    sell_currency = request.args.get('sell_currency', 'HKD')
    
    # 驗證貨幣代碼格式
    if not validate_currency_code(buy_currency):
        return jsonify({'error': f'無效的買入貨幣代碼格式：{buy_currency}'}), 400
    
    if not validate_currency_code(sell_currency):
        return jsonify({'error': f'無效的賣出貨幣代碼格式：{sell_currency}'}), 400
    
    try:
        latest_data = current_app.manager.get_current_rate(buy_currency, sell_currency)
        processing_time = time.time() - start_time
        
        if latest_data:
            current_rate = latest_data['rate']
            is_best = False
            for p in [7, 30, 90, 180]:
                dates, rates = current_app.manager.extract_local_rates(p)
                if rates and current_rate <= min(rates):
                    latest_data['best_period'] = p
                    latest_data['is_best'] = True
                    is_best = True
                    break
            if not is_best:
                dates30, rates30 = current_app.manager.extract_local_rates(30)
                if rates30:
                    latest_data['lowest_rate'] = min(rates30)
                    latest_data['lowest_period'] = 30
                latest_data['is_best'] = False

            latest_data['buy_currency'] = buy_currency
            latest_data['sell_currency'] = sell_currency
            latest_data['processing_time'] = round(processing_time, 3)
            latest_data['processing_time_ms'] = round(processing_time * 1000, 1)
            return jsonify(latest_data)
        else:
            return jsonify({ 
                'error': '無法獲取最新匯率，請稍後再試。', 
                'buy_currency': buy_currency, 
                'sell_currency': sell_currency,
                'processing_time': round(processing_time, 3)
            }), 500
    except Exception as e:
        processing_time = time.time() - start_time
        current_app.logger.error(f"💥 API LATEST (ERROR): 在獲取 {buy_currency}-{sell_currency} 時發生嚴重錯誤: {e}", exc_info=True)
        return jsonify({
            "error": f"伺服器在處理請求時發生內部錯誤: {e}",
            "buy_currency": buy_currency,
            "sell_currency": sell_currency,
            "processing_time": round(processing_time, 3)
        }), 500

@bp.route('/api/server_status')
def server_status_api():
    """提供伺服器實例ID，用於客戶端檢測伺服器重啟"""
    return jsonify({'server_instance_id': SERVER_INSTANCE_ID})

@bp.route('/api/schedule_status')
def get_schedule_status():
    """獲取定時任務狀態API"""
    try:
        jobs = schedule.jobs
        next_run_time = None

        if jobs:
            next_run_time = min(job.next_run for job in jobs).strftime('%Y-%m-%d %H:%M:%S')

        return jsonify({
            'success': True,
            'data': {
                'is_active': len(jobs) > 0,
                'next_run_time': next_run_time,
                'scheduled_time': '每天 09:00',
                'current_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'獲取定時任務狀態失敗: {str(e)}'
        }), 500

@bp.route('/api/trigger_scheduled_update')
def trigger_scheduled_update():
    """手動觸發定時更新API"""
    try:
        scheduled_update()
        return jsonify({
            'success': True,
            'message': '定時更新已手動觸發完成'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'手動觸發定時更新失敗: {str(e)}'
        }), 500

@bp.route('/api/force_cleanup_data')
def force_cleanup_data():
    """強制清理並更新近180天資料API"""
    try:
        print("🔄 強制執行180天資料清理...")
        old_count = len(current_app.manager.data)
        updated_count = current_app.manager.update_data(180)
        new_count = len(current_app.manager.data)
        removed_count = old_count - new_count + updated_count

        message = f"清理完成！原有 {old_count} 筆資料，現有 {new_count} 筆資料"
        if removed_count > 0:
            message += f"，已移除 {removed_count} 筆超過180天的舊資料"
        if updated_count > 0:
            message += f"，更新了 {updated_count} 筆新資料"

        print(f"✅ {message}")

        return jsonify({
            'success': True,
            'message': message,
            'old_count': old_count,
            'new_count': new_count,
            'removed_count': max(0, removed_count),
            'updated_count': updated_count,
            'cleaned_at': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'強制清理資料失敗: {str(e)}'
        }), 500

@bp.route('/api/pregenerate_charts')
def pregenerate_charts_api():
    """智能預生成圖表API"""
    buy_currency = request.args.get('buy_currency', 'TWD')
    sell_currency = request.args.get('sell_currency', 'HKD')
    
    # 驗證貨幣代碼格式
    if not validate_currency_code(buy_currency):
        return jsonify({'error': f'無效的買入貨幣代碼格式：{buy_currency}'}), 400
    
    if not validate_currency_code(sell_currency):
        return jsonify({'error': f'無效的賣出貨幣代碼格式：{sell_currency}'}), 400
    
    try:
        print(f"🚀 API觸發：請求為 {buy_currency}-{sell_currency} 啟動生成/通知流程...")
        current_app.manager.warm_up_chart_cache(buy_currency, sell_currency)
        
        return jsonify({
            'success': True, 
            'message': f'已觸發 {buy_currency}-{sell_currency} 圖表預生成/通知流程。'
        })
        
    except Exception as e:
        current_app.logger.error(f"💥 API /api/pregenerate_charts 發生錯誤: {e}", exc_info=True)
        return jsonify({
            'success': False, 
            'message': f'預生成圖表失敗: {str(e)}'
        }), 500

@bp.route('/api/cached_pairs')
def get_cached_pairs():
    """獲取伺服器快取中的所有貨幣對"""
    try:
        cached_pairs = current_app.manager.get_cached_pairs()
        return jsonify(cached_pairs)
    except Exception as e:
        current_app.logger.error(f"獲取快取貨幣對列表時發生錯誤: {e}", exc_info=True)
        return jsonify({'error': '無法獲取快取列表'}), 500

@bp.route('/api/events')
def sse_events():
    """SSE事件端點"""
    client_queue = queue.Queue()

    with sse_lock:
        sse_clients.append(client_queue)

    print(f"[SSE] 新客戶端連接，目前連接數: {len(sse_clients)}")

    try:
        client_queue.put("event: connected\ndata: {\"message\": \"SSE連接已建立\"}\n\n", timeout=1)
    except queue.Full:
        pass

    response = Response(sse_stream(client_queue), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Connection'] = 'keep-alive'
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response 