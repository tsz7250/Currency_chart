// static/js/dom.js

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

  const chartImage = document.getElementById('chartImage');
  const errorDisplay = document.getElementById('chartErrorDisplay');
  const loadingMessageEl = document.getElementById('loadingMessage');
  const progressBarContainer = spinner.querySelector('.progress-bar-container');
  const progressBar = document.getElementById('progressBar');
  const progressPercentage = document.getElementById('progressPercentage');

  // 顯示 spinner，隱藏圖表和錯誤
  spinner.style.display = 'flex';
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
 * Renders the chart image and updates the associated statistics.
 * @param {string} chartUrl - The URL of the chart image.
 * @param {object} stats - The object containing statistics.
 * @param {string} fromCurrency - The starting currency code.
 * @param {string} toCurrency - The target currency code.
 * @param {string|number} period - The data period for the chart.
 */
export function renderChart(chartUrl, stats, fromCurrency, toCurrency, period) {
  const chartImage = document.getElementById('chartImage');
  const chartErrorDisplay = document.getElementById('chartErrorDisplay');
  const chartTitle = document.getElementById('chart-title');

  // 隱藏載入動畫，並在完成後執行回呼
  hideGlobalProgressBar(() => {
    if (chartImage && chartUrl) {
      chartImage.src = chartUrl;
      chartImage.style.display = 'block';
      if (chartErrorDisplay) chartErrorDisplay.style.display = 'none';

      // 更新統計數據和標題
      updateGridStats(stats);
      if (chartTitle) {
        chartTitle.textContent = `${fromCurrency} → ${toCurrency} (${period} 天走勢)`;
      }
      // 確保日期範圍也被更新
      const dateRangeEl = document.getElementById('dateRange');
      if(dateRangeEl && stats && stats.date_range) {
          dateRangeEl.textContent = `數據範圍: ${stats.date_range}`;
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