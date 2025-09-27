# 多幣種匯率走勢圖

用簡單的方式查看多幣別匯率走勢。支援近 7／30／90／180 天圖表、幣別搜尋與交換，並在背景自動更新資料。

## 如何開始（Windows）
1) 安裝相依套件

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2) 啟動服務

```powershell
python run.py
```

3) 開啟瀏覽器
- http://127.0.0.1:5000/

提示：第一次啟動會下載/整理資料並預先產生圖表，可能需要一些時間。

## 如何使用
- 上方選擇「近 1 週／1 個月／3 個月／6 個月」切換期間
- 右側選擇「買入/賣出」幣別（可搜尋、可交換），點「確認變更」
- 生成圖表時會看到進度條，完成後自動顯示
- 歷史記錄可查看你看過與伺服器快取過的幣別對

## 實際界面
![實際界面](https://github.com/tszngaiyip/Currency_chart/tree/main/static/images/6mo.png)

## 資料來源與版權
- 匯率資料取自 Mastercard 公開服務，請遵守對方條款
- 本專案僅供學習與個人使用，如需散布請自行加入 LICENSE