# 替換為極輕量的 Debian Linux + Python 3.10 基礎
FROM python:3.10-slim-bookworm

WORKDIR /app

# 1. 補齊必需的系統套件（包含 xvfb 虛擬顯示器）
RUN apt-get update && apt-get install -y \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# 2. 安裝 Python 套件（此步驟會安裝 Playwright 的 Python 連接器）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 3. 關鍵瘦身：【僅下載】Chromium 瀏覽器，並且【僅安裝】Chromium 所需的 OS 相依庫
RUN playwright install chromium && playwright install-deps chromium

# 複製應用程式碼
COPY . .

# 確保圖表輸出目錄存在
RUN mkdir -p static/charts

# 設定預設環境變數
ENV HOST=0.0.0.0
ENV PORT=5000
ENV PYTHONUNBUFFERED=1

# 對外開放 5000 埠
EXPOSE 5000

# 啟動應用程式（使用 shell form 確保日誌正確輸出）
CMD xvfb-run -a python -u run.py
