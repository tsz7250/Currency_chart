// static/js/dom.js

let chartInstance = null;

// 全域 Chart.js 字型設置為微軟正黑體
if (typeof Chart !== 'undefined') {
  Chart.defaults.font.family = "'Microsoft JhengHei', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
}

// 自訂 Plugin: 繪製 Matplotlib 經典四邊邊框與右上角圖例
const chartCustomDrawPlugin = {
  id: 'chartCustomDraw',
  afterDraw(chart) {
    const { ctx, chartArea } = chart;
    if (!chartArea) return;

    ctx.save();
    // 1. 繪製四周外框
    ctx.strokeStyle = '#2c3e50';
    ctx.lineWidth = 1;
    ctx.strokeRect(chartArea.left, chartArea.top, chartArea.right - chartArea.left, chartArea.bottom - chartArea.top);

    // 2. 繪製右上角平均值圖例框（含橙色虛線標記）
    const avgVal = chart.options?.plugins?._avgRate;
    if (avgVal !== undefined) {
      const text = `平均值: ${avgVal.toFixed(4)}`;
      ctx.font = 'bold 12px "Microsoft JhengHei", sans-serif';
      const textWidth = ctx.measureText(text).width;
      const lineLen = 18;
      const padX = 8;
      const boxWidth = padX * 2 + lineLen + 6 + textWidth;
      const boxHeight = 22;
      const boxRight = chartArea.right - 10;
      const boxTop = chartArea.top + 10;
      const boxLeft = boxRight - boxWidth;

      // 白底背景
      ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
      ctx.fillRect(boxLeft, boxTop, boxWidth, boxHeight);

      // 淺灰邊框
      ctx.strokeStyle = '#bbbbbb';
      ctx.lineWidth = 1;
      ctx.strokeRect(boxLeft, boxTop, boxWidth, boxHeight);

      // 橙色虛線 (---)
      const lineY = boxTop + boxHeight / 2;
      const lineStartX = boxLeft + padX;
      const lineEndX = lineStartX + lineLen;
      ctx.save();
      ctx.strokeStyle = '#f39c12';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(lineStartX, lineY);
      ctx.lineTo(lineEndX, lineY);
      ctx.stroke();
      ctx.restore();

      // 文字
      ctx.fillStyle = '#333333';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, lineEndX + 6, lineY);
    }
    ctx.restore();
  }
};

export function showError(message) {
  const errorEl = document.getElementById('error-message');
  if (errorEl) {
    errorEl.textContent = message;
    errorEl.style.display = 'block';
  }
}

// 顯示最新匯率數據（固定4位小數）
export function displayLatestRate(data) {
  const rateEl = document.getElementById('latest-rate-content');
  if (!rateEl) return;
  // 日期格式化
  const formatDate = dateStr => new Date(dateStr).toLocaleDateString('zh-TW', { year: 'numeric', month: 'long', day: 'numeric' });
  // 趨勢顯示
  const getTrendDisplay = (trend, trendValue) => {
    if (!trend || trend === 'stable') return { icon: '➡️', text: '不變', class: 'stable' };
    if (trend === 'up') return { icon: '📈', text: `漲價 ${trendValue.toFixed(4)}`, class: 'up' };
    return { icon: '📉', text: `降價 ${Math.abs(trendValue).toFixed(4)}`, class: 'down' };
  };
  const trendInfo = getTrendDisplay(data.trend, data.trend_value);
  const rateValue = data.rate;
  // TWD⇔HKD反算提示
  let hint = '';
  if (data.buy_currency === 'TWD' && data.sell_currency === 'HKD') {
    const inverted = 1 / data.rate;
    hint = `<span class="rate-hint">(${inverted.toFixed(4)})</span>`;
  }
  // 準備處理時間顯示
  const timingDisplay = data.processing_time ? 
    `<div class="rate-timing">⚡ 載入時間：${data.processing_time_ms}ms</div>` : '';

  // 如果是前一天的數據，顯示標注
  const previousDayNotice = data.is_previous_day 
    ? `<div class="rate-fallback-notice" style="background: #fff3cd; color: #856404; padding: 8px; border-radius: 4px; margin-top: 8px; font-size: 0.9rem; border: 1px solid #ffc107;">
        ⚠️ ${data.fallback_reason || '今日數據尚未更新'}，顯示前一日匯率
      </div>`
    : '';

  rateEl.innerHTML = `
    <div class="rate-display">
      <div class="rate-info">
        <div class="rate-date">📅 ${formatDate(data.date)}${data.is_previous_day ? ' <span style="color: #856404;">(前一日)</span>' : ''}</div>
        <div class="rate-trend ${trendInfo.class}">
          <span class="trend-icon">${trendInfo.icon}</span>
          <span>${trendInfo.text}</span>
        </div>
      </div>
      <div class="rate-main">
        <div class="rate-value">${rateValue.toFixed(4)}${hint}</div>
        <div class="rate-label">1 ${data.buy_currency} = ? ${data.sell_currency}</div>
      </div>
      <div class="rate-info">
        ${data.is_best
          ? `<div class="rate-best">目前匯率是近${data.best_period}天最低</div>`
          : `<div class="rate-lowest">近${data.lowest_period}天最低: ${data.lowest_rate.toFixed(4)}</div>`}
        ${timingDisplay}
      </div>
      ${previousDayNotice}
    </div>
  `;
}

// 顯示匯率載入錯誤（原始設計）
export function showRateError(message) {
  const rateEl = document.getElementById('latest-rate-content');
  if (!rateEl) return;
  rateEl.innerHTML = `
    <div class="rate-error">
      <div style="font-size:2rem;margin-bottom:10px;">⚠️</div>
      <div>載入失敗</div>
      <div style="font-size:0.9rem;margin-top:5px;">${message}</div>
    </div>
  `;
}

export function showPopup(title, content) {
  const popupOverlay = document.getElementById('popup-overlay');
  const popupTitle = document.getElementById('popup-title');
  const popupBody = document.getElementById('popup-body');
  
  if (popupOverlay && popupTitle && popupBody) {
    popupTitle.textContent = title;
    popupBody.innerHTML = content;
    popupOverlay.style.display = 'flex';
    
    // 添加淡入動畫效果
    setTimeout(() => {
      popupOverlay.classList.add('show');
    }, 10);
  }
}

export function closePopup() {
  const popupOverlay = document.getElementById('popup-overlay');
  
  if (popupOverlay) {
    popupOverlay.classList.remove('show');
    
    // 等待動畫完成後隱藏元素
    setTimeout(() => {
      popupOverlay.style.display = 'none';
    }, 300);
  }
}

// 更新圖表統計網格
export function updateGridStats(stats, processingTimeMs = null) {
  if (!stats) return;
  const maxEl = document.getElementById('maxRate');
  const minEl = document.getElementById('minRate');
  const avgEl = document.getElementById('avgRate');
  const dpEl = document.getElementById('dataPoints');
  const drEl = document.getElementById('dateRange');
  if (maxEl) maxEl.textContent = `最高匯率: ${stats.max_rate.toFixed(4)}`;
  if (minEl) minEl.textContent = `最低匯率: ${stats.min_rate.toFixed(4)}`;
  if (avgEl) avgEl.textContent = `平均匯率: ${stats.avg_rate.toFixed(4)}`;
  if (dpEl) dpEl.textContent = `數據點: ${stats.data_points}`;
  
  // 更新日期範圍，並在有處理時間時添加時間信息
  if (drEl) {
    let dateRangeText = `數據範圍: ${stats.date_range}`;
    if (processingTimeMs) {
      dateRangeText += ` (⚡${processingTimeMs}ms)`;
    }
    drEl.textContent = dateRangeText;
  }
}

// --- 全局進度條管理 (Refactored) ---

/**
 * 顯示並重置全局進度條。
 * @param {string} message - 要顯示的載入訊息。
 */
export function showGlobalProgressBar(message = '正在請求後端生成圖表...') {
  const spinner = document.getElementById('chartSpinner');
  if (!spinner) return;

  const chartCanvas = document.getElementById('rateChart');
  const chartImage = document.getElementById('chartImage');
  const errorDisplay = document.getElementById('chartErrorDisplay');
  const loadingMessageEl = document.getElementById('loadingMessage');
  const progressBarContainer = spinner.querySelector('.progress-bar-container');
  const progressBar = document.getElementById('progressBar');
  const progressPercentage = document.getElementById('progressPercentage');

  // 顯示 spinner，隱藏圖表和錯誤
  spinner.style.display = 'flex';
  if (chartCanvas) chartCanvas.style.display = 'none';
  if (chartImage) chartImage.style.display = 'none';
  if (errorDisplay) errorDisplay.style.display = 'none';
  
  // 設定載入訊息
  if (loadingMessageEl) loadingMessageEl.textContent = message;

  // 重置並顯示進度條
  if (progressBarContainer && progressBar && progressPercentage) {
    progressBarContainer.style.display = 'block';
    progressPercentage.style.display = 'block';
    progressBar.style.transition = 'width 0.2s linear'; // 平滑過渡
    progressBar.style.width = '0%';
    progressPercentage.textContent = '0%';
  }
}

/**
 * 更新全局進度條的進度。
 * @param {number} progress - 進度百分比 (0-100)。
 * @param {string|null} message - (可選) 要更新的載入訊息。
 */
export function updateGlobalProgressBar(progress, message = null) {
  const progressBar = document.getElementById('progressBar');
  const progressPercentage = document.getElementById('progressPercentage');
  const loadingMessageEl = document.getElementById('loadingMessage');

  if (progressBar && progressPercentage) {
    const p = Math.max(0, Math.min(100, progress)); // 確保進度在 0-100 之間
    progressBar.style.width = `${p}%`;
    progressPercentage.textContent = `${Math.round(p)}%`;
  }
  
  if (message && loadingMessageEl) {
      loadingMessageEl.textContent = message;
  }
}

/**
 * 以動畫效果完成並隱藏全局進度條。
 * @param {Function} [callback] - (可選) 在進度條完全隱藏後執行的回呼函式。
 */
export function hideGlobalProgressBar(callback) {
  const spinner = document.getElementById('chartSpinner');
  if (!spinner || spinner.style.display === 'none') {
    if (callback) callback();
    return;
  }

  const progressBar = document.getElementById('progressBar');
  
  // 讓完成動畫更明顯
  updateGlobalProgressBar(100, '圖表載入完成！');

  setTimeout(() => {
      if (spinner) spinner.style.display = 'none';
      
      // 在隱藏後執行回呼
      if (callback) callback();

      // 重置進度條以備下次使用
      if (progressBar) {
          progressBar.style.transition = '';
          progressBar.style.width = '0%';
      }
  }, 500); // 延遲 500ms 隱藏
}

/**
 * 從 JSON 檔案載入貨幣並填充到指定的 <select> 元素中。
 * @param {string} fromCurrencyId - 'from' 貨幣選擇器的 ID。
 * @param {string} toCurrencyId - 'to' 貨幣選擇器的 ID。
 */
export async function populateCurrencySelectors(fromCurrencyId, toCurrencyId) {
  try {
    const response = await fetch('/static/currency_list.json');
    if (!response.ok) {
      throw new Error(`無法載入貨幣列表：${response.statusText}`);
    }
    const currencies = await response.json();

    const fromSelect = document.getElementById(fromCurrencyId);
    const toSelect = document.getElementById(toCurrencyId);

    if (!fromSelect || !toSelect) {
      console.error('找不到指定的貨幣選擇器元素');
      return;
    }

    // 清空現有選項
    fromSelect.innerHTML = '';
    toSelect.innerHTML = '';

    // 填充選項
    currencies.forEach(currency => {
      const optionHtml = `<option value="${currency.code}">${currency.name} - ${currency.code}</option>`;
      fromSelect.insertAdjacentHTML('beforeend', optionHtml);
      toSelect.insertAdjacentHTML('beforeend', optionHtml);
    });

    // 設定預設值
    fromSelect.value = 'TWD';
    toSelect.value = 'HKD';

  } catch (error) {
    console.error('填充貨幣選擇器時出錯:', error);
    showError('無法載入貨幣選項，請稍後重試。');
  }
}

// 處理圖表載入錯誤
export function handleChartError(message) {
  const chartContainer = document.getElementById('chart-container');
  if (chartContainer) {
    chartContainer.innerHTML = `<p class="error">${message}</p>`;
  }
}

/**
 * 使用 Chart.js 向量渲染匯率走勢圖並更新相關統計資訊。
 * 支援傳入純數列資料物件或向下相容參數。
 */
export function renderChart(chartData, stats, fromCurrency, toCurrency, period) {
  const canvas = document.getElementById('rateChart');
  const chartImage = document.getElementById('chartImage');
  const chartErrorDisplay = document.getElementById('chartErrorDisplay');

  // 參數歸一化處理
  const data = (chartData && typeof chartData === 'object' && chartData.dates) 
    ? chartData 
    : { dates: chartData?.dates, rates: chartData?.rates, stats: stats || chartData?.stats };

  const fromCurr = fromCurrency || data.buy_currency || 'TWD';
  const toCurr = toCurrency || data.sell_currency || 'HKD';
  const activePeriod = period || data.period || 7;
  const currentStats = data.stats || stats;

  hideGlobalProgressBar(() => {
    if (!canvas || !data || !Array.isArray(data.dates) || data.dates.length === 0) {
      if (chartErrorDisplay) {
        chartErrorDisplay.textContent = '❌ 無可用的圖表匯率數據';
        chartErrorDisplay.style.display = 'block';
      }
      return;
    }

    if (chartImage) chartImage.style.display = 'none';
    if (chartErrorDisplay) chartErrorDisplay.style.display = 'none';
    canvas.style.display = 'block';

    const labels = data.dates;
    const rates = data.rates;

    if (chartInstance) {
      chartInstance.destroy();
    }

    const periodNames = { 7: '近1週', 30: '近1個月', 90: '近3個月', 180: '近6個月' };
    const periodLabel = periodNames[activePeriod] || `近${activePeriod}天`;

    // 計算統計值與極值索引
    const maxRate = Math.max(...rates);
    const minRate = Math.min(...rates);
    const avgRate = Number((rates.reduce((s, r) => s + r, 0) / rates.length).toFixed(4));
    const maxIndex = rates.indexOf(maxRate);
    const minIndex = rates.indexOf(minRate);

    // Y 軸緩衝與整數步長網格對齊
    const yRange = maxRate - minRate || 0.001;
    const yPaddingTop = yRange * 0.22;
    const yPaddingBottom = yRange * 0.15;
    const _yCandidates = [0.0001, 0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01];
    const yStep = _yCandidates.find(s => yRange / s >= 4 && yRange / s <= 8) ?? _yCandidates[_yCandidates.length - 1];
    const yMin = Number((Math.floor((minRate - yPaddingBottom) / yStep) * yStep).toFixed(4));
    const yMax = Number((Math.ceil((maxRate + yPaddingTop) / yStep) * yStep).toFixed(4));

    // X 軸等距抽樣刻度索引（保證首尾兩點必定呈現且間距均勻）
    const tickIndices = [];
    if (labels.length <= 10) {
      for (let i = 0; i < labels.length; i++) tickIndices.push(i);
    } else {
      const targetTicks = 11;
      const lastIdx = labels.length - 1;
      const step = Math.ceil(lastIdx / (targetTicks - 1));
      for (let i = 0; i < lastIdx; i += step) {
        tickIndices.push(i);
      }
      if (lastIdx - tickIndices[tickIndices.length - 1] < step * 0.6) {
        tickIndices[tickIndices.length - 1] = lastIdx;
      } else {
        tickIndices.push(lastIdx);
      }
    }

    // X 軸固定留白（保留約 2.5% 寬度，首尾點距邊框永遠固定約 20px）
    const xSpan = labels.length - 1 || 1;
    const xPadding = xSpan * 0.025;
    const xMin = -xPadding;
    const xMax = xSpan + xPadding;

    // 極值點 X 軸位移防止左右邊界裁切
    const calcXAdjust = (idx, total) => {
      if (idx === 0 || (idx / total) < 0.08) return 20;
      if (idx === total - 1 || (idx / total) > 0.92) return -20;
      return 0;
    };

    // 極值標籤與平均線配置
    const annotations = {
      avgLine: {
        type: 'line',
        yMin: avgRate,
        yMax: avgRate,
        borderColor: '#f39c12',
        borderWidth: 1.5,
        borderDash: [6, 6],
        label: { display: false }
      },
      maxLabel: {
        type: 'label',
        xValue: maxIndex,
        yValue: maxRate,
        backgroundColor: 'transparent',
        content: maxRate.toFixed(4),
        color: '#e74c3c',
        font: { size: 12, weight: 'bold' },
        yAdjust: -12,
        xAdjust: calcXAdjust(maxIndex, labels.length),
        padding: 2,
      },
      minLabel: {
        type: 'label',
        xValue: minIndex,
        yValue: minRate,
        backgroundColor: 'transparent',
        content: minRate.toFixed(4),
        color: '#27ae60',
        font: { size: 12, weight: 'bold' },
        yAdjust: 12,
        xAdjust: calcXAdjust(minIndex, labels.length),
        padding: 2,
      }
    };

    const ctx = canvas.getContext('2d');

    chartInstance = new Chart(ctx, {
      type: 'line',
      data: {
        datasets: [{
          label: '匯率',
          data: rates.map((r, i) => ({ x: i, y: r })),
          borderColor: '#2E86AB',
          backgroundColor: 'rgba(46, 134, 171, 0.05)',
          borderWidth: 2,
          pointRadius: data.dates.length > 90 ? 2 : 3.5,
          pointHoverRadius: 5,
          pointBackgroundColor: '#2E86AB',
          pointBorderColor: '#2E86AB',
          fill: false,
          tension: 0,
        }]
      },
      plugins: [chartCustomDrawPlugin],
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: {
          padding: { left: 10, right: 20, top: 10, bottom: 5 }
        },
        interaction: {
          intersect: false,
          mode: 'index',
        },
        plugins: {
          _avgRate: avgRate,
          legend: { display: false },
          title: {
            display: true,
            text: `${fromCurr} 到 ${toCurr} 匯率走勢圖 (${periodLabel})`,
            font: { size: 16, weight: 'bold' },
            padding: { top: 8, bottom: 16 },
            color: '#2c3e50',
          },
          annotation: {
            annotations
          },
          tooltip: {
            backgroundColor: 'rgba(0, 0, 0, 0.85)',
            titleColor: '#fff',
            bodyColor: '#fff',
            titleFont: { size: 13 },
            bodyFont: { size: 13 },
            padding: 10,
            cornerRadius: 6,
            displayColors: false,
            callbacks: {
              title: (items) => labels[items[0].raw.x] || '',
              label: (item) => `匯率: ${item.raw.y.toFixed(7)}`,
            }
          }
        },
        scales: {
          x: {
            type: 'linear',
            min: xMin,
            max: xMax,
            afterBuildTicks: (scale) => {
              scale.ticks = tickIndices.map(i => ({ value: i }));
            },
            title: {
              display: true,
              text: '日期',
              color: '#2c3e50',
              font: { size: 13, weight: 'bold' }
            },
            grid: {
              color: 'rgba(0,0,0,0.06)',
              tickColor: 'rgba(0,0,0,0.2)',
            },
            ticks: {
              color: '#333',
              font: { size: 12 },
              callback: function(val) {
                const d = labels[val];
                if (!d) return '';
                const parts = d.split('-');
                return parts.length === 3 ? `${parts[1]}/${parts[2]}` : d;
              }
            }
          },
          y: {
            min: yMin,
            max: yMax,
            title: {
              display: true,
              text: '匯率',
              color: '#2c3e50',
              font: { size: 13, weight: 'bold' }
            },
            grid: { color: 'rgba(0,0,0,0.06)' },
            ticks: {
              stepSize: yStep,
              color: '#333',
              font: { size: 12 },
              callback: (v) => v.toFixed(4),
            }
          }
        }
      }
    });

    // 更新統計數據
    if (currentStats) {
      updateGridStats(currentStats, data.processing_time_ms);
      const dateRangeEl = document.getElementById('dateRange');
      if (dateRangeEl && currentStats.date_range) {
        let text = `數據範圍: ${currentStats.date_range}`;
        if (data.processing_time_ms) {
          text += ` (⚡${data.processing_time_ms}ms)`;
        }
        dateRangeEl.textContent = text;
      }
    }
  });
}

/**
 * 更新期間按鈕的啟用狀態和當前選中項。
 * @param {string|number} activePeriod - 當前活躍的週期。
 */
export function updatePeriodButtons(activePeriod) {
  const periodButtons = document.querySelectorAll('.period-btn');
  periodButtons.forEach(btn => {
    btn.classList.remove('active');
    if (btn.dataset.period == activePeriod) {
      btn.classList.add('active');
    }
  });
}

/**
 * 更新圖表下方的日期範圍顯示。
 * @param {string} dateRangeText - 要顯示的日期範圍文字。
 */
export function updateDateRange(dateRangeText) {
    const drEl = document.getElementById('dateRange');
    if (drEl && dateRangeText) {
        drEl.textContent = `數據範圍: ${dateRangeText}`;
    }
}

// --- History Popup ---

export function openHistoryPopup() {
  const popup = document.getElementById('history-popup-overlay');
  if (popup) {
    popup.style.display = 'flex';
    setTimeout(() => popup.classList.add('show'), 10);
  }
}

export function closeHistoryPopup() {
  const popup = document.getElementById('history-popup-overlay');
  if (popup) {
    popup.classList.remove('show');
    setTimeout(() => {
      popup.style.display = 'none';
    }, 300);
  }
}

export function renderHistoryList(pairs, type) {
  const listEl = document.getElementById('history-list');
  if (!listEl) return;

  if (!pairs || pairs.length === 0) {
    const message = type === 'user' ? '你還沒有任何瀏覽記錄' : '伺服器尚無任何快取記錄';
    listEl.innerHTML = `<div class="history-empty">${message}</div>`;
    return;
  }

  listEl.innerHTML = pairs.map(pair => `
    <div class="history-item" data-buy-currency="${pair.buy_currency}" data-sell-currency="${pair.sell_currency}">
      <span>${pair.buy_currency}</span>
      <span class="history-arrow">→</span>
      <span>${pair.sell_currency}</span>
    </div>
  `).join('');
}

/**
 * 在圖表上方顯示自動更新通知，3秒後自動消失
 */
export function showAutoUpdateNotification() {
  const chartContainer = document.getElementById('chart-container');
  if (!chartContainer) return;
  
  // 檢查是否已有通知，避免重複
  const existingNotification = chartContainer.querySelector('.auto-update-notification');
  if (existingNotification) {
    existingNotification.remove();
  }
  
  // 創建通知元素
  const notification = document.createElement('div');
  notification.className = 'auto-update-notification';
  notification.innerHTML = '💹 匯率已自動更新';
  
  // 插入到圖表容器頂部
  chartContainer.insertBefore(notification, chartContainer.firstChild);
  
  // 觸發淡入動畫
  setTimeout(() => {
    notification.classList.add('show');
  }, 10);
  
  // 3秒後淡出並移除
  setTimeout(() => {
    notification.classList.remove('show');
    notification.classList.add('hide');
    // 動畫結束後從DOM中移除
    setTimeout(() => {
      notification.remove();
    }, 300);
  }, 3000);
}