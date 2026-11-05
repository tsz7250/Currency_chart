# 多幣種匯率走勢圖
正式預測現在使用集中發布包：相同發布版本在所有部署回傳相同價格、區間與評分，不依本地使用多久或圖表快取而改變。首次正式包尚未發布時，預測彈窗顯示等待版本，歷史圖仍可使用。

### 集中發布與同步

一般部署保留 `.env` 的 `FORECAST_ROLE=client`；`FORECAST_RELEASE_REPO` 預設 `tsz7250/Currency_chart`。啟動、09:45、18:15 與預測請求會安排背景同步；遠端版本檢查最多每五分鐘一次，不阻塞 HTTP。正式包從固定 `forecast-current` Release 的 `forecast-release.zip` 下載。各包內部版本不可覆寫；更新該遠端指標前，保留舊版本原包供回退。

下載後先驗證檔案允許清單、大小、SHA256、六組共同基準日、30 個目標日期與正值區間；整包驗證成功才原子切換。中斷或同步失敗保留完整舊版；超過七天停止提供預測。畫面顯示發布版本、資料基準日及落後日數。預測起點仍是資料基準日，不重標為今天。

集中發布端使用 `FORECAST_ROLE=publisher`，會在主站排程時補抓報價並建立本地包；不自動上傳 GitHub。首次匯入：

`python -X utf8 scripts/publish_forecasts.py --import-history instance/forecast-supervised/run-2026-11-05/history --as-of 2026-11-04 --preview`

報價版本保存在 `instance/forecast-publisher/quotes.sqlite3`，保存查詢日、來源 fxDate、取得時間、來源與雜湊；相同內容去重，同日修正另存。歷史匯入標記為 historical_import，無法證明歷史公布版本，不當成即時取得資料。圖表的 180 天範圍不再刪除原始本地報價。

`python -X utf8 scripts/publish_forecasts.py --refresh --activate`

正式包要求六組同一天、至少 270 筆資料，且發布日必須是當天；缺資料保留舊版。補抓會處理來源仍提供範圍內的缺漏及近七天修正，不把停機期間補成預測。每份包附資料快照、模型固定 revision、校準設定、程式與套件版本、全部預測及評分。重複輸入不建立新版本；每日多份修正僅以首份正式預測計入正式命中樣本，避免同日起點重複加權。Preview 包不能安裝成正式版本，預覽啟用會使用獨立目錄。

接收已審查的正式本地包：`python -X utf8 scripts/sync_forecasts.py --install <forecast-release.zip>`。明確回退：加上 `--rollback`。遠端同步：`python -X utf8 scripts/sync_forecasts.py`。公開發布前需確認原始報價的再散布授權；Mastercard 一般網站使用條款未授予再發布權，預覽包不應直接上傳公開 Release。

新發布引擎使用完整暖機歷史，再選最近 180 日評分起點；保留原先 91 個日曆點的輸入規格及缺值，不因新增未來資料改變舊起點。歷史回放與實際發出後評分分開：`backtest.score_source` 與 `backtest.issued` 清楚區別；正式結果不足時不以歷史回放代替。API 保留原參數及價格欄位，增加 release_version、published_at、data_age_days、sync_state。

Docker 已改為 Python 3.12，以符合 scikit-learn 1.8 的版本要求。映像排除 cookies、本地報價、instance 與 .env，執行資料由掛載提供。一般部署不使用本地模型推論正式結果；`app/forecast_service.py` 保留作舊流程及測試使用。

用簡單的方式查看多幣別匯率走勢。支援近 7／30／90／180 天圖表、幣別搜尋與交換，並在背景自動更新資料。

## 實際界面
![image](https://github.com/tszngaiyip/Currency_chart/blob/main/static/images/6mo.png?raw=true)

## 如何開始（Windows）
1) 安裝相依套件

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

2) 啟動服務

```powershell
python run.py
```

3) 開啟瀏覽器
- http://127.0.0.1:5000/

**✨ 智能自動化：**
- 首次啟動會自動檢查並更新匯率數據
- 如需獲取 Cookies，會自動顯示瀏覽器窗口約 10 秒
- 一切都是自動的，只需運行 `python run.py`！

## 如何開始（Docker）
若環境已安裝 Docker 與 Docker Compose，可直接容器化啟動（免手動配置 Python 與 Playwright）：

1) 構建並於背景啟動

```bash
docker compose up -d --build
```

2) 開啟瀏覽器
- http://127.0.0.1:5000/

3) 停止服務

```bash
docker compose down
```

## 如何使用
- 上方選擇「近 1 週／1 個月／3 個月／6 個月」切換期間
- 右側選擇「買入/賣出」幣別（可搜尋、可交換），點「確認變更」
- 生成圖表時會看到進度條，完成後自動顯示
- 歷史記錄可查看你看過與伺服器快取過的幣別對

## 資料來源與版權
- 匯率資料取自 Mastercard 公開服務，請遵守對方條款
- 本專案僅供學習與個人使用，如需散布請自行加入 LICENSE

## 到價 Email 提醒（自架版）

訪客可為目前已確認的幣別對設定「大於等於」或「小於等於」目標匯率。
匯率方向為 **1 個來源幣別可兌換多少目標幣別**，沿用 Mastercard 匯率，銀行手續費設為 0。
先點驗證信內的連結，再按確認訂閱，才會啟用；驗證連結 24 小時內有效。
每個提醒於台北時間每日 09:00 檢查，09:30 重試，每日最多寄一次。
週末使用最近週五資料；平日沒有當日資料就略過。這是日匯率提醒，不是即時報價。

### 寄信設定

使用 Gmail SMTP，預設以 SSL 連線至 `smtp.gmail.com:465`，並驗證伺服器憑證。
先開啟 Google 帳號的兩步驟驗證，再建立本程式專用的應用程式密碼；不要使用一般 Gmail 登入密碼。
部分工作、學校或受安全政策限制的帳號可能無法建立應用程式密碼；此版本不支援 OAuth 或 STARTTLS。
參考：[Google 應用程式密碼](https://support.google.com/mail/answer/185833?hl=zh-Hant)、[Gmail SMTP 設定](https://developers.google.com/workspace/gmail/imap/imap-smtp)。
驗證信與通知共用本程式每日 250 封上限；Gmail 另有寄送限制，SMTP 接受郵件不保證進入收件匣。

尚無 `.env` 時，將根目錄 `.env.example` 複製為 `.env`，填入 Gmail 帳號、應用程式密碼與網站網址。
已有 `.env` 時請手動合併下列設定，保留既有內容；舊的 `BREVO_API_KEY` 已不再讀取，可自行移除。
本機啟動時會自動讀取根目錄 `.env`，不受啟動目錄影響；已設定的環境變數優先，不會被覆寫。

```powershell
Copy-Item .env.example .env
```

編輯 `.env`（範例值須替換）：

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USERNAME=your-account@gmail.com
SMTP_PASSWORD=你的應用程式密碼（移除顯示用空格）
ALERT_SENDER_EMAIL=
ALERT_BASE_URL=http://192.168.1.10:5000
HOST=0.0.0.0
```

設定後執行；修改 `.env` 後須重啟服務：

```powershell
python run.py
```

`ALERT_BASE_URL` 必須是訪客可連線的固定網址，不含路徑；其他裝置不可使用 `localhost`。
區域網路網址只能在該網路內開啟；對外開放前須另建 HTTPS 入口。
主機日期與時區請設為台北時間，確保既有資料更新使用正確日期。
伺服器須在排程時間運作；停機期間不補寄。

`ALERT_SENDER_EMAIL` 留空時使用 `SMTP_USERNAME`；Gmail 建議使用相同寄件信箱。
Docker Compose 可從主機環境變數或根目錄 `.env` 讀取相同 SMTP 與提醒設定。
訂閱與寄送紀錄保存在 `instance/alerts.sqlite3`，容器透過 `./instance:/app/instance` 持久化。
本機可用 `ALERT_DATABASE` 更改位置；自訂位置須放在非公開目錄，並自行備份。
應用程式密碼、`.env` 與資料庫不可提交至 Git；備份檔同樣含個人資料，須限制存取。
未設定寄信服務時，提醒表單停用，圖表仍可使用。

### 限制與失敗處理

- 每個信箱最多 5 個未取消提醒；每 10 分鐘最多一封驗證信。
- 每個來源 IP 每小時最多 10 次有效格式申請。不要未經設定就信任代理轉送的 IP。
- 同日排程重跑、手動更新或重啟不重複寄送已受理的提醒。
- 登入失敗或 SMTP 明確拒絕的郵件可在下次排程重試；寄送途中斷線、逾時或程序中斷後的 `sending` 狀態不自動重寄。
- 郵件已獲 SMTP 接受後，關閉連線失敗不會造成重寄；寄送狀態保存於資料庫。
- 維護者可用寄信紀錄中的 key 與已保存的 Message-ID 核對；查清前不要刪除資料庫寄送紀錄。
- 達本程式每日上限便停止寄送；SMTP 回報限流或額度不足時視為明確拒絕，下次排程可重試。
- 從任一通知信取消該提醒；舊通知中的取消連結持續有效。取消時移除該提醒保存的 email。
- 第一版沿用單程序服務，不提供帳號、提醒清單或管理後台；修改門檻請取消後重新訂閱。
- GitHub Pages 僅顯示功能說明，不接收訂閱，也不存放 email。

### 驗證

```powershell
python -m unittest discover -s tests -v
node tests/test_alerts_frontend.mjs
```

自動測試以假寄信服務執行，不寄出真實郵件。部署後另用維護者自己的信箱驗證完整流程。

---

## Chronos 未來匯率預測（主站）

主頁「未來預測」按鈕開啟獨立彈窗，顯示近 7 個日曆天實際報價，以及未來 7／14／30 天中位數與校準價格範圍。原歷史圖與期間設定不受影響。
彈窗支援手機、Esc 關閉與焦點返回。方向回測顯示 accuracy、macro precision／recall／F1、樣本數與價格 MAE；漲／平／跌門檻為 ±0.1%，缺少類別的分數記為 0。此為目前模型滾動回測，並非一年資料的封存模型評選。
發布端使用固定版本 Chronos-Bolt Small 在 CPU 推論；一般部署只同步正式發布包，不自行推論或重新評分。
先執行 `python -m pip install -r requirements.txt`，再依原方式執行 `python run.py`。
模型保存在 `instance/models/`，Docker 沿用 `instance` 持久化掛載；Docker 安裝 CPU 版 PyTorch。

新發布引擎先準備完整暖機歷史，再評分最近 180 日起點；每個起點只取當時最近 90 天及起點當天、至少 40 筆報價。
缺報價保留為缺值。每個預測步長分開計算對數尺度的區間外誤差；至少 20 筆已實現誤差才提供校準區間。
驗證只使用當時已知結果校準；畫面揭露實測涵蓋率與樣本數，少於 20 次驗證標示實驗性。
80% 是目標而非命中保證；各日區間也不是整段路徑同時落入的機率。預測不計入歷史統計與到價提醒。
最後報價超過 7 天時停止預測。模型或下載失敗只影響預測區域，可稍後重試。

`GET /api/forecast?buy_currency=TWD&sell_currency=HKD&horizon=7` 回傳狀態、基準日、模型版本、每日預測、含缺值的近 7 天 `recent_history` 與回測摘要（含 `classification`）。
等待正式包時回傳 HTTP 202；主站最多等待約兩分鐘後提供手動重試。本地歷史資料不影響正式結果。

研究方法、實驗結果、淘汰原因與重跑入口請見 [FORECAST_RESEARCH.md](FORECAST_RESEARCH.md)。

## GitHub Pages 靜態展示（TWD-HKD）

本專案另提供免費靜態展示頁面，僅追蹤 TWD ⇒ HKD 匯率，由 GitHub Actions 每日自動更新。

**線上頁面**：https://tsz7250.github.io/Currency_chart/

### 自動更新
- GitHub Actions 每週一至週五 UTC 01:00（台北 09:00）自動抓取最新匯率
