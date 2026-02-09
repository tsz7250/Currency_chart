import { fetchChart, loadLatestRate, triggerPregeneration, fetchCachedPairs } from './api.js';
import { 
  displayLatestRate, 
  showRateError, 
  showPopup, 
  closePopup, 
  updateGridStats,
  showGlobalProgressBar,
  updateGlobalProgressBar,
  hideGlobalProgressBar,
  populateCurrencySelectors,
  renderChart,
  updateDateRange,
  updatePeriodButtons,
  handleChartError,
  openHistoryPopup,
  closeHistoryPopup,
  renderHistoryList,
  showAutoUpdateNotification
} from './dom.js';
import { CurrencyManager } from './currency_manager.js';
import { userHistoryManager } from './history_manager.js';

// 全域變數
let currentPeriod = '7'; // 預設圖表週期
let eventSource = null;
let chartCache = {}; // 前端圖表短期快取

// 創建全域 CurrencyManager 實例
const currencyManager = new CurrencyManager({
  currentPeriod: () => currentPeriod,
  chartCache,
  updateDisplay,
  showGlobalProgressBar,
  updateGlobalProgressBar,
  hideGlobalProgressBar,
  renderChart,
  updateDateRange,
  updatePeriodButtons,
  displayLatestRate,
  showRateError,
  updateCurrencyDisplay,
  loadLatestRate,
  handleChartError,
  triggerPregeneration
});

// 頁面載入時自動載入圖表和最新匯率
document.addEventListener('DOMContentLoaded', async function () {
  // 首先填充貨幣選擇器
  await populateCurrencySelectors('from-currency', 'to-currency');
  
  try {
    const response = await fetch('/api/server_status');
    if (!response.ok) {
      throw new Error(`Server status check failed: ${response.statusText}`);
    }
    const data = await response.json();
    const currentServerId = data.server_instance_id;
    const storedServerId = sessionStorage.getItem('serverInstanceId');

    if (currentServerId !== storedServerId) {
      // Server has restarted. Reset settings.
      
      sessionStorage.removeItem('fromCurrency');
      sessionStorage.removeItem('toCurrency');
      // Store the new server ID
      sessionStorage.setItem('serverInstanceId', currentServerId);
      
      // 重設 CurrencyManager
      currencyManager.currentFromCurrency = 'TWD';
      currencyManager.currentToCurrency = 'HKD';
      currencyManager.saveToStorage();
    }
  } catch (error) {
    console.error('檢查伺服器狀態失敗:', error);
    // If check fails, do not reset to preserve user selection in case of network issues
  }

  // CurrencyManager 已經在初始化時處理了 sessionStorage 載入
  // 更新 select 元素的值
  currencyManager.updateCurrencySelectors();

  // 【修正】必須先建立 SSE 連接，才能觸發任何可能發送 SSE 事件的行為
  // 建立SSE連接
  setupSSEConnection();

  // 【修正】初始載入圖表與匯率，使用直接呼叫，而不是有 bug 的 switchCurrencies
  currencyManager.loadChart();
  currencyManager.loadRate();

  // 綁定貨幣選擇器事件
  setupCurrencySelectors();

  // 手動更新初始顯示
  updateCurrencyDisplay('from-currency');
  updateCurrencyDisplay('to-currency');
  
  // 綁定確認按鈕事件
  setupConfirmButton();
  
  // 綁定其他按鈕事件
  setupEventListeners();
  // 綁定歷史記錄彈窗事件
  setupHistoryPopup();
});

// 設置貨幣選擇器事件（統一搜索下拉選單）
function setupCurrencySelectors() {
  setupCurrencyCombobox('from-currency');
  setupCurrencyCombobox('to-currency');
  setupCurrencySwapButton();
}

function setupCurrencySwapButton() {
  const swapButton = document.querySelector('.exchange-arrow');
  if (swapButton) {
    swapButton.addEventListener('click', () => {
      currencyManager.swapCurrencies();
    });
  }
}

// 設置單個貨幣組合框（統一搜索下拉選單）
function setupCurrencyCombobox(selectId) {
  const wrapper = document.querySelector(`#${selectId}`).parentElement;
  const input = wrapper.querySelector('.currency-input');
  const dropdown = wrapper.querySelector('.currency-dropdown');
  const select = wrapper.querySelector('select');

  let allOptions = [];
  let filteredOptions = [];
  let highlightedIndex = -1;
  let isSearchMode = false;

  const getAllOptions = () => {
    return Array.from(select.options).map(option => ({
      value: option.value,
      text: option.textContent
    }));
  };

  const filterOptions = (searchTerm) => {
    return allOptions.filter(option =>
      option.text.toLowerCase().includes(searchTerm.toLowerCase())
    );
  };

  const createDropdownItems = (options) => {
    dropdown.innerHTML = '';
    const fragment = document.createDocumentFragment();

    // 使用當前實際值或待定值來決定哪個項目被選中
    const currentValue = (selectId === 'from-currency' && currencyManager.pendingFromCurrency) ? currencyManager.pendingFromCurrency :
      (selectId === 'to-currency' && currencyManager.pendingToCurrency) ? currencyManager.pendingToCurrency :
        select.value;

    options.forEach((option) => {
      const item = document.createElement('div');
      item.className = 'currency-dropdown-item';
      item.dataset.value = option.value;
      item.textContent = option.text;
      if (option.value === currentValue) {
        item.classList.add('selected');
      }
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        selectOption(option.value);
      });
      fragment.appendChild(item);
    });

    dropdown.appendChild(fragment);
  };

  const showDropdown = () => {
    allOptions = getAllOptions();
    filteredOptions = [...allOptions];
    createDropdownItems(filteredOptions);

    const selectedValue = (selectId === 'from-currency' && currencyManager.pendingFromCurrency) ? currencyManager.pendingFromCurrency :
      (selectId === 'to-currency' && currencyManager.pendingToCurrency) ? currencyManager.pendingToCurrency :
        select.value;

    const selectedItem = dropdown.querySelector(`[data-value="${selectedValue}"]`);
    if (selectedItem) {
      setTimeout(() => {
        selectedItem.scrollIntoView({ block: 'nearest' });
      }, 0);
    }

    dropdown.classList.add('open');
    highlightedIndex = filteredOptions.findIndex(o => o.value === selectedValue);
  };

  const hideDropdown = () => {
    dropdown.classList.remove('open');
    exitSearchMode();
  };

  const highlightItem = (index) => {
    const items = dropdown.querySelectorAll('.currency-dropdown-item');
    const currentHighlighted = dropdown.querySelector('.highlighted');
    if (currentHighlighted) {
      currentHighlighted.classList.remove('highlighted');
    }
    if (items[index]) {
      items[index].classList.add('highlighted');
      items[index].scrollIntoView({ block: 'nearest' });
    }
    highlightedIndex = index;
  };

  const selectOption = (value) => {
    const selectedOption = allOptions.find(o => o.value === value);
    if (!selectedOption) return;

    // 更新 pending 值
    const type = selectId === 'from-currency' ? 'from' : 'to';
    currencyManager.setPendingCurrency(type, value);

    // 更新輸入框顯示為待定選項
    input.value = selectedOption.text;

    hideDropdown();
  };

  const updateInputDisplay = () => {
    const pendingValue = selectId === 'from-currency' ? currencyManager.pendingFromCurrency : currencyManager.pendingToCurrency;
    const finalValue = pendingValue || select.value;
    const selectedOption = allOptions.length > 0 ? allOptions.find(o => o.value === finalValue) : Array.from(select.options).find(o => o.value === finalValue);

    if (selectedOption) {
      input.value = selectedOption.text;
    }
  };

  const enterSearchMode = () => {
    if (isSearchMode) return;
    isSearchMode = true;
    input.value = '';
    input.removeAttribute('readonly');
    input.focus();
    showDropdown();
    filteredOptions = filterOptions(''); // reset filter
    createDropdownItems(filteredOptions);
  };

  const exitSearchMode = () => {
    if (!isSearchMode) return;
    isSearchMode = false;
    input.setAttribute('readonly', true);
    updateInputDisplay();
  };

  input.addEventListener('input', () => {
    if (isSearchMode) {
      filteredOptions = filterOptions(input.value);
      createDropdownItems(filteredOptions);
      highlightItem(0);
    }
  });

  document.addEventListener('click', (e) => {
    if (!wrapper.contains(e.target)) {
      hideDropdown();
    }
  });

  wrapper.addEventListener('click', (e) => {
    if (e.target.classList.contains('currency-input')) {
      enterSearchMode();
    }
  });

  input.addEventListener('keydown', (e) => {
    if (!isSearchMode) {
        if (e.key === ' ' || e.key === 'Enter') {
            e.preventDefault();
            enterSearchMode();
        }
        return;
    }
    
    switch(e.key) {
      case 'ArrowDown':
        e.preventDefault();
        highlightItem(Math.min(highlightedIndex + 1, filteredOptions.length - 1));
        break;
      case 'ArrowUp':
        e.preventDefault();
        highlightItem(Math.max(highlightedIndex - 1, 0));
        break;
      case 'Enter':
        e.preventDefault();
        const highlightedItemEl = dropdown.querySelector('.highlighted');
        if (highlightedItemEl) {
          selectOption(highlightedItemEl.dataset.value);
        } else if (filteredOptions.length > 0) {
          selectOption(filteredOptions[0].value);
        }
        break;
      case 'Escape':
        hideDropdown();
        break;
    }
  });
}

// 更新單個貨幣選擇器的顯示（當 CurrencyManager 狀態改變時）
function updateCurrencyDisplay(selectId) {
    const wrapper = document.querySelector(`#${selectId}`).parentElement;
    const input = wrapper.querySelector('.currency-input');
    const select = wrapper.querySelector('select');
    
    const value = selectId === 'from-currency' ? currencyManager.currentFromCurrency : currencyManager.currentToCurrency;
    const option = Array.from(select.options).find(opt => opt.value === value);
    
    if (option) {
        input.value = option.textContent;
        select.value = value;
    }
}


// 更新顯示內容
function updateDisplay() {
  // 更新最新匯率區塊標題
  const rateHeader = document.querySelector('.latest-rate-header h3');
  if (rateHeader) {
    rateHeader.textContent = `💰 最新匯率 (${currencyManager.currentFromCurrency} ⇒ ${currencyManager.currentToCurrency})`;
  }

  // 更新頁面標題
  document.title = `${currencyManager.currentFromCurrency} to ${currencyManager.currentToCurrency} Exchange Rate`;

  // 更新主要標題
  const mainTitle = document.getElementById('main-title');
  if (mainTitle) {
    mainTitle.textContent = `${currencyManager.currentFromCurrency} → ${currencyManager.currentToCurrency} 匯率走勢`;
  }
}

// SSE 連接
function setupSSEConnection() {
  if (eventSource) {
    eventSource.close();
  }

  eventSource = new EventSource('/api/events');

  // SSE 連線建立時更新指示器
  eventSource.onopen = function() {
    const indicator = document.getElementById('sse-status-indicator');
    if (indicator) {
      indicator.classList.remove('disconnected');
      indicator.classList.add('connected');
      indicator.title = 'SSE 已連接';
    }
    console.log('✅ SSE 連線已建立');
  };

  // 監聽伺服器發送的連接確認事件
  eventSource.addEventListener('connected', function(event) {
    const data = JSON.parse(event.data);
    console.log('✅ SSE 連接確認:', data.message);
  });

  // 新增：正確監聽 'progress_update' 命名事件
  eventSource.addEventListener('progress_update', function(event) {
    const data = JSON.parse(event.data);
    // 確保進度條只為當前查看的貨幣對更新
    if (data.buy_currency === currencyManager.currentFromCurrency && data.sell_currency === currencyManager.currentToCurrency) {
        // 只更新目前選定 period 的全域進度
        const periodKey = (currentPeriod || '7').toString();
        const percent = data.period_progress && data.period_progress[periodKey] != null
          ? data.period_progress[periodKey]
          : data.progress;
        // 組合符合當前 period 的訊息（X/需要值）
        let msg = data.message;
        if (data.period_needed && data.current_points != null && data.period_needed[periodKey] != null) {
          const needed = data.period_needed[periodKey];
          const have = Math.min(data.current_points, needed);
          msg = `已獲取 ${have}/${needed} 天數據...`;
        }
        updateGlobalProgressBar(percent, msg);
    }
  });
  
  // 監聽 'chart_ready' 事件
  eventSource.addEventListener('chart_ready', (event) => {
    const chartData = JSON.parse(event.data);

    if (
      chartData.buy_currency === currencyManager.currentFromCurrency &&
      chartData.sell_currency === currencyManager.currentToCurrency
    ) {
      // 僅在該事件的 period 等於當前選擇的 period 時才更新 UI
      const isCurrentPeriod = String(chartData.period) === String(currentPeriod);
      // 清除超時計時器
      if (currencyManager.chartLoadTimeout) {
        clearTimeout(currencyManager.chartLoadTimeout);
        currencyManager.chartLoadTimeout = null;
      }

      // 更新前端快取（無論是否為當前 period）
      const cacheKey = `${chartData.buy_currency}_${chartData.sell_currency}_${chartData.period}`;
      chartCache[cacheKey] = chartData;

      if (isCurrentPeriod) {
        // 隱藏全域進度條並渲染
        hideGlobalProgressBar(() => {
          renderChart(chartData.chart_url, chartData.stats, chartData.buy_currency, chartData.sell_currency, chartData.period);
          updateDateRange(chartData.stats.date_range);
          // 一旦圖表準備就緒，設定載入狀態為 false
          currencyManager.setLoading('chart', false);
        });
      }
    }
  });
  
  // 監聽 'chart_error' 事件
  eventSource.addEventListener('chart_error', function(event) {
    const data = JSON.parse(event.data);
    if (data.buy_currency === currencyManager.currentFromCurrency && data.sell_currency === currencyManager.currentToCurrency) {
        const isCurrentPeriod = data.period == null || String(data.period) === String(currentPeriod);
        // 清除超時計時器
        if (currencyManager.chartLoadTimeout) {
            clearTimeout(currencyManager.chartLoadTimeout);
            currencyManager.chartLoadTimeout = null;
        }
        if (isCurrentPeriod) {
          hideGlobalProgressBar(() => {
              handleChartError(data.message);
              currencyManager.setLoading('chart', false);
          });
        }
    }
  });

  eventSource.onerror = function () {
    const indicator = document.getElementById('sse-status-indicator');
    if (indicator) {
      indicator.classList.remove('connected');
      indicator.classList.add('disconnected');
      indicator.title = 'SSE 連線中斷，正在重連...';
    }
    console.log('⚠️ SSE 連線中斷，瀏覽器將自動重連');
    // 不呼叫 eventSource.close()，讓 EventSource 原生自動重連機制接管
  };

  // 監聽 rate_updated 事件：伺服器排程更新匯率時觸發
  eventSource.addEventListener('rate_updated', async function(event) {
    const data = JSON.parse(event.data);
    console.log('🔄 收到匯率更新通知:', data);
    
    // 只有當更新的貨幣對與當前顯示的相符時才執行
    if (data.buy_currency === currencyManager.currentFromCurrency && 
        data.sell_currency === currencyManager.currentToCurrency) {
      
      // 顯示更新通知
      showAutoUpdateNotification();
      
      // 清除圖表快取
      const period = currentPeriod || '7';
      const cacheKey = `${data.buy_currency}_${data.sell_currency}_${period}`;
      delete chartCache[cacheKey];
      
      // 重新載入最新匯率
      await currencyManager.loadRate();
      
      // 重新載入圖表
      await currencyManager.loadChart();
    }
  });
}

// showAutoUpdateNotification 函式已移至 dom.js


function setupConfirmButton() {
  const confirmBtn = document.getElementById('confirm-currency-btn');
  if (confirmBtn) {
    confirmBtn.addEventListener('click', () => {
      currencyManager.confirmCurrencyChanges();
    });
  }
}

/**
 * 統一設置事件監聽器
 */
function setupEventListeners() {
  // 切換圖表週期的按鈕
  const periodButtons = document.querySelectorAll('.period-btn');
  periodButtons.forEach(button => {
    button.addEventListener('click', () => {
      // 獲取被點擊的按鈕的週期
      const newPeriod = button.dataset.period;

      // 如果點擊的是當前的週期，則不執行任何操作
      if (currentPeriod === newPeriod) {
        return;
      }
      
      // 更新當前週期
      currentPeriod = newPeriod;

  // 立即更新按鈕的 UI 狀態
      updatePeriodButtons(currentPeriod);
  // 切換 period 時，重置並顯示全域進度條
  showGlobalProgressBar(`正在為您準備 ${currencyManager.currentFromCurrency}-${currencyManager.currentToCurrency} 的圖表 (${currentPeriod} 天)...`);
      
      // 使用 currencyManager 的方法來載入圖表
      currencyManager.loadChart();
    });
  });

  // Popup 關閉按鈕
  const popupCloseBtn = document.getElementById('popup-close-btn');
  if (popupCloseBtn) {
    popupCloseBtn.addEventListener('click', closePopup);
  }

  const popupOverlay = document.getElementById('popup-overlay');
  if (popupOverlay) {
    popupOverlay.addEventListener('click', (e) => {
      if (e.target === popupOverlay) {
        closePopup();
      }
    });
  }
}

function setupHistoryPopup() {
  const historyBtn = document.getElementById('history-btn');
  const historyPopupCloseBtn = document.getElementById('history-popup-close-btn');
  const userHistoryBtn = document.getElementById('user-history-btn');
  const serverHistoryBtn = document.getElementById('server-history-btn');
  const historyList = document.getElementById('history-list');

  if (!historyBtn || !historyPopupCloseBtn || !userHistoryBtn || !serverHistoryBtn || !historyList) {
    console.error('History popup elements not found');
    return;
  }

  const loadUserHistory = () => {
    const history = userHistoryManager.getHistory();
    renderHistoryList(history, 'user');
  };

  const loadServerHistory = async () => {
    try {
      const pairs = await fetchCachedPairs();
      renderHistoryList(pairs, 'server');
    } catch (error) {
      console.error('Failed to load server history:', error);
      renderHistoryList([], 'server'); // Show empty state on error
    }
  };

  historyBtn.addEventListener('click', () => {
    openHistoryPopup();
    // Default to server history view
    serverHistoryBtn.classList.add('active');
    userHistoryBtn.classList.remove('active');
    loadServerHistory();
  });

  historyPopupCloseBtn.addEventListener('click', closeHistoryPopup);

  userHistoryBtn.addEventListener('click', () => {
    userHistoryBtn.classList.add('active');
    serverHistoryBtn.classList.remove('active');
    loadUserHistory();
  });

  serverHistoryBtn.addEventListener('click', () => {
    serverHistoryBtn.classList.add('active');
    userHistoryBtn.classList.remove('active');
    loadServerHistory();
  });

  historyList.addEventListener('click', (e) => {
    const item = e.target.closest('.history-item');
    if (item) {
      const buyCurrency = item.dataset.buyCurrency;
      const sellCurrency = item.dataset.sellCurrency;
      
      if (buyCurrency && sellCurrency) {
        closeHistoryPopup();
        currencyManager.switchCurrencies(buyCurrency, sellCurrency);
      }
    }
  });
}