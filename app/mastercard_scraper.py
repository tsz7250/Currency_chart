"""
Mastercard 數據抓取器 - 使用真實瀏覽器會話
策略：從真實瀏覽器複製有效的 cookies，然後用 requests 請求
"""

import json
import requests
from datetime import datetime, timedelta
import time
import random


class MastercardScraper:
    """
    使用 requests + 有效 cookies 的方式抓取 Mastercard 數據
    """
    
    def __init__(self, cookies_file='mastercard_cookies.json'):
        self.cookies_file = cookies_file
        self.session = None
        self.cookies_dict = {}
        self.load_cookies()
    
    def load_cookies(self):
        """從文件載入 cookies"""
        try:
            with open(self.cookies_file, 'r', encoding='utf-8') as f:
                cookies_list = json.load(f)
            
            # 轉換為 requests 需要的格式
            self.cookies_dict = {
                cookie['name']: cookie['value'] 
                for cookie in cookies_list
            }
            
            print(f"[Scraper] 已載入 {len(self.cookies_dict)} 個 cookies")
            
            # 顯示關鍵 cookies
            key_cookies = ['_abck', 'bm_sz', 'bm_sv']
            for key in key_cookies:
                if key in self.cookies_dict:
                    print(f"  - {key}: {self.cookies_dict[key][:30]}...")
            
            return True
        except FileNotFoundError:
            print(f"[Scraper] 錯誤：找不到 {self.cookies_file}")
            print("[Scraper] 請先手動使用瀏覽器訪問 Mastercard 並導出 cookies")
            return False
        except Exception as e:
            print(f"[Scraper] 載入 cookies 時發生錯誤: {e}")
            return False
    
    def get_exchange_rate(self, date, buy_currency='TWD', sell_currency='HKD'):
        """
        獲取指定日期的匯率
        
        Args:
            date: datetime 對象
            buy_currency: 交易貨幣
            sell_currency: 帳單貨幣
        
        Returns:
            dict: {'data': {'conversionRate': '0.251241', ...}} 或 None
        """
        url = "https://www.mastercard.com/marketingservices/public/mccom-services/currency-conversions/conversion-rates"
        
        params = {
            'exchange_date': date.strftime('%Y-%m-%d'),
            'transaction_currency': buy_currency,
            'cardholder_billing_currency': sell_currency,
            'bank_fee': '0',
            'transaction_amount': '1'
        }
        
        # 從 HAR 提取的完整 headers
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
            'Priority': 'u=0, i',
            'TE': 'trailers'
        }
        
        try:
            # 添加隨機延遲
            delay = random.uniform(1.0, 3.0)
            time.sleep(delay)
            
            print(f"[Scraper] 正在請求 {date.strftime('%Y-%m-%d')} 的數據...")
            
            response = requests.get(
                url,
                params=params,
                headers=headers,
                cookies=self.cookies_dict,
                timeout=15
            )
            
            print(f"[Scraper] HTTP {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"[Scraper] 成功獲取數據")
                return data
            elif response.status_code == 403:
                print(f"[Scraper] 403 錯誤：cookies 可能已過期")
                print(f"[Scraper] 請重新從瀏覽器導出新的 cookies")
                return None
            else:
                print(f"[Scraper] 錯誤：HTTP {response.status_code}")
                print(f"[Scraper] 響應: {response.text[:200]}")
                return None
        
        except Exception as e:
            print(f"[Scraper] 發生錯誤: {e}")
            return None
    
    def update_local_data(self, data_file='TWD-HKD_180d.json', days=180):
        """
        更新本地數據文件
        
        Args:
            data_file: 數據文件路徑
            days: 要更新的天數
        
        Returns:
            int: 成功更新的數據數量
        """
        # 載入現有數據
        try:
            with open(data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, IOError) as e:
            print(f"[Scraper] 載入數據失敗: {e}")
            data = {}
        
        print(f"[Scraper] 本地數據中有 {len(data)} 條記錄")
        
        # 找出需要更新的日期
        end_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start_date = end_date - timedelta(days=days)
        
        # 找到最新的日期
        if data:
            latest_date_str = max(data.keys())
            latest_date = datetime.strptime(latest_date_str, '%Y-%m-%d')
            start_date = latest_date + timedelta(days=1)
            print(f"[Scraper] 最新數據: {latest_date_str}")
        
        print(f"[Scraper] 需要更新從 {start_date.strftime('%Y-%m-%d')} 到 {end_date.strftime('%Y-%m-%d')}")
        
        # 抓取數據
        updated_count = 0
        current_date = start_date
        
        while current_date <= end_date:
            # 跳過週末
            if current_date.weekday() < 5:
                date_str = current_date.strftime('%Y-%m-%d')
                
                # 如果已經有數據，跳過
                if date_str in data:
                    print(f"[Scraper] {date_str} 已存在，跳過")
                else:
                    result = self.get_exchange_rate(current_date, 'TWD', 'HKD')
                    
                    if result and 'data' in result:
                        try:
                            rate = float(result['data']['conversionRate'])
                            data[date_str] = {
                                'rate': rate,
                                'updated': datetime.now().isoformat()
                            }
                            updated_count += 1
                            print(f"[Scraper] ✓ {date_str}: {rate}")
                        except Exception as e:
                            print(f"[Scraper] ✗ {date_str}: 解析失敗 - {e}")
                    else:
                        print(f"[Scraper] ✗ {date_str}: 獲取失敗")
                        
                        # 如果是 403，停止更新
                        if result is None:
                            print(f"[Scraper] 停止更新（可能需要刷新 cookies）")
                            break
            
            current_date += timedelta(days=1)
        
        # 保存數據
        if updated_count > 0:
            with open(data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[Scraper] 已保存 {updated_count} 條新數據到 {data_file}")
        else:
            print(f"[Scraper] 沒有新數據需要保存")
        
        return updated_count