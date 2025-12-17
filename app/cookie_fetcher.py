"""
自動獲取 Mastercard 有效 Cookies
使用有頭模式的 Playwright + Stealth
"""

import asyncio
import json
import os
from playwright.async_api import async_playwright
from playwright_stealth.stealth import Stealth


class CookieFetcher:
    """
    使用有頭瀏覽器自動訪問 Mastercard 並獲取有效 cookies
    """
    
    def __init__(self, cookies_file='mastercard_cookies.json'):
        self.cookies_file = cookies_file
    
    async def _wait_for_key_cookies(self, context, timeout=5):
        """
        等待必須的 cookies 生成
        必須的 cookies: _abck, bm_sz, bm_sv（Akamai Bot Manager 反機器人系統）
        
        Args:
            context: Playwright context
            timeout: 最長等待時間（秒）
        
        Returns:
            tuple: (是否成功, 缺失的 cookies 列表)
        """
        required_cookies = ['_abck', 'bm_sz', 'bm_sv']
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            cookies = await context.cookies()
            cookie_names = {c['name'] for c in cookies}
            
            # 檢查哪些必須的 cookies 已經生成
            missing = [key for key in required_cookies if key not in cookie_names]
            
            if not missing:  # 所有必須的 cookies 都已生成
                return True, []
            
            await asyncio.sleep(0.5)  # 每0.5秒檢查一次
        
        # 超時後返回缺失的 cookies
        cookies = await context.cookies()
        cookie_names = {c['name'] for c in cookies}
        missing = [key for key in required_cookies if key not in cookie_names]
        return False, missing
    
    async def fetch_cookies_async(self, headless=False, wait_time=10):
        """
        使用 Playwright 訪問頁面並獲取 cookies
        
        Args:
            headless: 是否使用無頭模式（False = 顯示瀏覽器窗口）
            wait_time: 最大等待時間（秒），用於關鍵 cookies 生成的超時時間
        
        Returns:
            list: cookies 列表
        """
        print(f"[CookieFetcher] 啟動瀏覽器...")
        print(f"[CookieFetcher] 模式: {'無頭' if headless else '有頭（顯示窗口）'}")
        
        async with async_playwright() as p:
            browser = None
            try:
                # 啟動瀏覽器（有頭模式更容易通過檢測）
                browser = await p.chromium.launch(
                    headless=headless,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                    ]
                )
                
                # 創建 context
                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0',
                    viewport={'width': 1920, 'height': 1080},
                    locale='zh-TW',
                )
                
                # 創建頁面
                page = await context.new_page()
                
                # 應用 stealth
                stealth = Stealth()
                await stealth.apply_stealth_async(page)
                
                print(f"[CookieFetcher] 正在訪問 Mastercard...")
                
                # 先訪問主頁
                try:
                    main_url = "https://www.mastercard.com/us/en/personal/get-support/currency-exchange-rate-converter.html"
                    print(f"[CookieFetcher] 步驟 1: 訪問主頁...")
                    await page.goto(main_url, wait_until='load', timeout=30000)
                    print(f"[CookieFetcher] ✓ 主頁載入完成")
                    
                    # 等待必須的 cookies 生成
                    print(f"[CookieFetcher] 等待必須的 cookies 生成...")
                    cookies_ready, missing = await self._wait_for_key_cookies(context, timeout=wait_time)
                    
                    if cookies_ready:
                        print(f"[CookieFetcher] ✓ 所有必須的 cookies 已生成 (_abck, bm_sz, bm_sv)")
                    else:
                        print(f"[CookieFetcher] ⚠️ 超時：缺少 cookies: {', '.join(missing)}")
                        print(f"[CookieFetcher] 繼續嘗試，可能仍然有效...")
                    
                except Exception as e:
                    print(f"[CookieFetcher] 警告：訪問主頁時發生錯誤: {e}")
            
                # 訪問 API 端點獲取更完整的 cookies
                api_success = False
                try:
                    from datetime import datetime
                    today = datetime.now().strftime('%Y-%m-%d')
                    api_url = f"https://www.mastercard.com/marketingservices/public/mccom-services/currency-conversions/conversion-rates?exchange_date={today}&transaction_currency=TWD&cardholder_billing_currency=HKD&bank_fee=0&transaction_amount=1"
                    print(f"[CookieFetcher] 步驟 2: 訪問 API 端點...")
                    
                    response = await page.goto(api_url, wait_until='load', timeout=30000)
                    status = response.status if response else 'None'
                    print(f"[CookieFetcher] HTTP {status}")
                    
                    if status == 200:
                        print(f"[CookieFetcher] ✅ 成功！API 返回 200")
                        
                        # 嘗試解析 JSON
                        try:
                            content = await page.content()
                            if 'conversionRate' in content:
                                print(f"[CookieFetcher] ✓ 確認：頁面包含匯率數據")
                                api_success = True
                        except Exception as e:
                            print(f"[CookieFetcher] 解析頁面內容時發生錯誤: {e}")
                    else:
                        print(f"[CookieFetcher] ⚠️ API 返回 {status}")
                    
                    # 如果 API 成功，再等待一小段時間確保所有 cookies 都設置完成
                    if api_success:
                        await asyncio.sleep(1)
                    else:
                        # 如果 API 失敗，等待稍長時間讓反機器人系統完成
                        await asyncio.sleep(3)
                    
                except Exception as e:
                    print(f"[CookieFetcher] 警告：訪問 API 時發生錯誤: {e}")
                
                # 獲取所有 cookies
                cookies = await context.cookies()
                print(f"[CookieFetcher] 獲取到 {len(cookies)} 個 cookies")
                
                # 驗證必須的 cookies 和可選的 cookies
                required_cookies = ['_abck', 'bm_sz', 'bm_sv']  # 必須
                optional_cookies = ['ak_bmsc']  # 可選但有幫助
                
                cookie_names = {c['name']: c['value'] for c in cookies}
                
                # 檢查必須的 cookies
                missing_required = []
                for name in required_cookies:
                    if name in cookie_names:
                        print(f"  ✓ {name}: {cookie_names[name][:30]}... [必須]")
                    else:
                        missing_required.append(name)
                        print(f"  ✗ {name}: 缺失 [必須]")
                
                # 檢查可選的 cookies
                for name in optional_cookies:
                    if name in cookie_names:
                        print(f"  ✓ {name}: {cookie_names[name][:30]}... [可選]")
                
                # 最終驗證
                if missing_required:
                    print(f"[CookieFetcher] ⚠️ 警告：缺少必須的 cookies: {', '.join(missing_required)}")
                    print(f"[CookieFetcher] 這些 cookies 可能無效，請重試")
                else:
                    print(f"[CookieFetcher] ✅ 所有必須的 cookies 都已獲取")
                
                return cookies
            
            finally:
                if browser:
                    await browser.close()
                    print("[CookieFetcher] 瀏覽器已關閉")
    
    def fetch_cookies(self, headless=False, wait_time=10):
        """同步版本的 fetch_cookies"""
        return asyncio.run(self.fetch_cookies_async(headless, wait_time))
    
    def save_cookies(self, cookies):
        """
        保存 cookies 到文件
        
        Args:
            cookies: Playwright 格式的 cookies
        """
        # 轉換為標準格式
        cookies_list = [
            {
                'name': c['name'],
                'value': c['value'],
                'domain': c.get('domain', '.mastercard.com'),
                'path': c.get('path', '/')
            }
            for c in cookies
        ]
        
        with open(self.cookies_file, 'w', encoding='utf-8') as f:
            json.dump(cookies_list, f, indent=2, ensure_ascii=False)
        
        print(f"[CookieFetcher] ✓ Cookies 已保存到: {self.cookies_file}")
        return cookies_list
    
    def fetch_and_save(self, headless=False, wait_time=10):
        """
        獲取並保存 cookies（一步完成）
        
        Args:
            headless: 是否使用無頭模式
            wait_time: 等待時間
        
        Returns:
            bool: 是否成功
        """
        try:
            print("=" * 70)
            print("  Mastercard Cookies 自動獲取工具")
            print("=" * 70)
            print()
            
            # 獲取 cookies
            cookies = self.fetch_cookies(headless=headless, wait_time=wait_time)
            
            if not cookies:
                print("\n❌ 失敗：未能獲取任何 cookies")
                return False
            
            # 保存 cookies
            self.save_cookies(cookies)
            
            print("\n" + "=" * 70)
            print("✅ 成功！Cookies 已保存")
            print("=" * 70)
            print()
            print("下一步：重新啟動網站或等待排程，系統會自動使用新的 Cookies 更新數據")
            print()
            
            return True
            
        except Exception as e:
            print(f"\n❌ 發生錯誤: {e}")
            import traceback
            traceback.print_exc()
            return False