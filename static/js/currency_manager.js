// CurrencyManager 類別 - 統一管理貨幣狀態和載入控制
class CurrencyManager {
    constructor(dependencies = {}) {
      this.currentFromCurrency = 'TWD';
      this.currentToCurrency = 'HKD';
      this.pendingFromCurrency = null;
      this.pendingToCurrency = null;
      this.isSwapping = false;
      
      // 載入狀態管理（只有圖表和匯率）
      this.loadingStates = {
        chart: false,
        rate: false
      };
      
      // 新增：圖表載入超時計時器
      this.chartLoadTimeout = null;
      
      // 儲存依賴項
      this.deps = dependencies;
      
      this.loadFromStorage();
    }
  
    // 從 sessionStorage 載入狀態
    loadFromStorage() {
      const savedFromCurrency = sessionStorage.getItem('fromCurrency');
      const savedToCurrency = sessionStorage.getItem('toCurrency');
      
      if (savedFromCurrency) this.currentFromCurrency = savedFromCurrency;
      if (savedToCurrency) this.currentToCurrency = savedToCurrency;
      
      this.saveToStorage();
    }
  
    // 儲存到 sessionStorage
    saveToStorage() {
      sessionStorage.setItem('fromCurrency', this.currentFromCurrency);
      sessionStorage.setItem('toCurrency', this.currentToCurrency);
    }
  
    // 檢查是否可以切換貨幣（圖表和匯率都載入完成）
    canSwitchCurrency() {
      return !this.loadingStates.chart && !this.loadingStates.rate;
    }
  
    // 設定載入狀態
    setLoading(type, status) {
      this.loadingStates[type] = status;
      this.updateUIStates();
    }
  
    // 新增：檢查圖表是否正在載入中
    isChartLoading() {
      return this.loadingStates.chart;
    }
  
    // 更新UI狀態（禁用/啟用按鈕）
    updateUIStates() {
      const isLoading = !this.canSwitchCurrency();
      
      // 禁用/啟用貨幣選擇器
      const currencyInputs = document.querySelectorAll('.currency-input');
      currencyInputs.forEach(input => {
        input.disabled = isLoading;
      });
      
      // 禁用/啟用交換按鈕
      const swapButton = document.querySelector('.exchange-arrow');
      if (swapButton) {
        swapButton.style.pointerEvents = isLoading ? 'none' : 'auto';
        swapButton.style.opacity = isLoading ? '0.5' : '1';
      }
      
      // 禁用/啟用狀態按鈕
      const statusButtons = document.querySelectorAll('.status-btn');
      statusButtons.forEach(btn => {
        btn.disabled = isLoading;
      });
  
      // 禁用/啟用確認按鈕
      const confirmBtn = document.getElementById('confirm-currency-btn');
      if (confirmBtn) {
        confirmBtn.disabled = isLoading;
      }
    }
  
    // 統一的貨幣切換入口
    async switchCurrencies(fromCurrency, toCurrency, source = 'manual') {
      
      
      // 檢查是否可以切換
      if (!this.canSwitchCurrency()) {
        
        return { success: false, reason: 'system_busy' };
      }
  
      // 如果沒有變化，直接返回
      if (fromCurrency === this.currentFromCurrency && toCurrency === this.currentToCurrency) {
        return { success: true, noChange: true };
      }
  
      // 立即設定載入狀態
      this.setLoading('chart', true);
      this.setLoading('rate', true);
  
      try {
        // 更新貨幣狀態
        this.currentFromCurrency = fromCurrency;
        this.currentToCurrency = toCurrency;
        this.saveToStorage();
  
        // 更新UI顯示
        this.updateCurrencySelectors();
        if (this.deps.updateDisplay) {
          this.deps.updateDisplay();
        }
  
        // 【重構】分派載入任務，但不在此處等待或處理它們的完成
        // 載入狀態將由 loadChart 和 loadRate 內部管理
        this.loadChart();
        this.loadRate();
  
        return {
          success: true,
          fromCurrency,
          toCurrency,
          source
        };
      } catch (error) {
        console.error('貨幣切換失敗:', error);
        // 確保載入狀態被重置
        this.setLoading('chart', false);
        this.setLoading('rate', false);
        throw error;
      }
    }
  
    // 載入圖表 (事件驅動的 "檢視器" 模式)
    async loadChart() {
      const fromCurrency = this.currentFromCurrency;
      const toCurrency = this.currentToCurrency;
      const period = this.deps.currentPeriod ? this.deps.currentPeriod() : 7;
      const cacheKey = `${fromCurrency}_${toCurrency}_${period}`;


      // 步驟 1: 檢查前端短期快取
      if (this.deps.chartCache && this.deps.chartCache[cacheKey]) {
        
        const chartData = this.deps.chartCache[cacheKey];
        // 直接渲染，不發送任何請求
        if (this.deps.renderChart) {
          this.deps.renderChart(chartData, chartData.stats, fromCurrency, toCurrency, period);
        }
        if (this.deps.updateDateRange) {
          this.deps.updateDateRange(chartData.stats.date_range);
        }
        if (this.deps.updatePeriodButtons) {
          this.deps.updatePeriodButtons(period);
        }
  // 快取命中，視為完成（全域進度條已由外部負責）
        this.setLoading('chart', false);
        return;
      }

      // 清除可能存在的舊計時器
      if (this.chartLoadTimeout) {
        clearTimeout(this.chartLoadTimeout);
      }

      // 步驟 2: 如果前端快取未命中，觸發後端開始工作
      
      if (this.deps.showGlobalProgressBar) {
        this.deps.showGlobalProgressBar(`正在為您準備 ${fromCurrency}-${toCurrency} 的圖表...`);
      }
      this.setLoading('chart', true); // 顯示加載動畫

      // 先嘗試直接獲取圖表，如果失敗則使用預生成模式
      try {
        const chartResponse = await fetch(`/api/chart?period=${period}&buy_currency=${fromCurrency}&sell_currency=${toCurrency}`);
        if (chartResponse.ok) {
          const chartData = await chartResponse.json();
          if (chartData && chartData.dates) {
            // 直接渲染圖表
            if (this.deps.renderChart) {
              this.deps.renderChart(chartData, chartData.stats, fromCurrency, toCurrency, period);
            }
            if (this.deps.updateDateRange) {
              this.deps.updateDateRange(chartData.stats.date_range);
            }
            if (this.deps.updatePeriodButtons) {
              this.deps.updatePeriodButtons(period);
            }
            // 更新前端快取
            if (this.deps.chartCache) {
              this.deps.chartCache[cacheKey] = chartData;
            }
            // 直接取得圖表成功，視為完成（全域進度條已由外部負責）
            // 成功載入，隱藏進度條並設定載入狀態為 false
            if (this.deps.hideGlobalProgressBar) {
              this.deps.hideGlobalProgressBar();
            }
            this.setLoading('chart', false);
            return; // 直接返回，不需要預生成
          }
        }
      } catch (error) {
        console.warn('直接獲取圖表失敗，將使用預生成模式:', error);
      }

      // 如果直接獲取失敗，則使用預生成模式
      // 設定載入超時 (例如 30 秒)
      this.chartLoadTimeout = setTimeout(() => {
        console.error(`圖表請求超時: ${fromCurrency}-${toCurrency}`);
        if (this.isChartLoading()) { // 僅在仍在載入時觸發
          this.deps.handleChartError(`為 ${fromCurrency}-${toCurrency} 生成圖表時發生超時。`);
          this.deps.hideGlobalProgressBar();
          this.setLoading('chart', false);
        }
      }, 30000); // 30 秒超時

      // 只觸發，不等待，不處理回應。UI 更新將由 SSE 事件驅動
      this.deps.triggerPregeneration(fromCurrency, toCurrency);
      // 注意：這裡不直接渲染，而是等待 'chart_ready' 事件
    }
  
    // 載入最新匯率
    async loadRate() {
      const startTime = performance.now();
      
      try {
        if (!this.deps.loadLatestRate) {
          throw new Error('loadLatestRate 函數未提供');
        }
        
        const rateData = await this.deps.loadLatestRate(this.currentFromCurrency, this.currentToCurrency);
        const finalTime = (performance.now() - startTime) / 1000;
        
        if (this.deps.displayLatestRate) {
          this.deps.displayLatestRate(rateData);
        }
        
        // 顯示處理時間信息
        if (rateData.processing_time) {
          
        }
      } catch (error) {
        console.error('匯率載入失敗:', error);
        if (this.deps.showRateError) {
          this.deps.showRateError('匯率載入失敗，請稍後再試');
        }
      } finally {
        // 【修正】無論成功或失敗，都要解除匯率載入狀態
        this.setLoading('rate', false);
      }
    }
  
    // 交換貨幣
    async swapCurrencies() {
      if (this.isSwapping) return;
      
      this.isSwapping = true;
      
      try {
        // 清除待確認狀態
        this.clearPendingChanges();
        
        // 添加視覺效果
        const swapButton = document.querySelector('.exchange-arrow');
        if (swapButton) {
          swapButton.style.transform = 'rotate(180deg)';
          setTimeout(() => swapButton.style.transform = '', 300);
        }
  
        // 關閉開啟的下拉選單
        const openDropdown = document.querySelector('.currency-dropdown.open');
        if (openDropdown) {
          document.body.click();
        }
  
        // 執行交換
        const result = await this.switchCurrencies(
          this.currentToCurrency, 
          this.currentFromCurrency, 
          'swap'
        );
        
        return result;
      } finally {
        setTimeout(() => this.isSwapping = false, 100);
      }
    }
  
    // 確認貨幣變更
    async confirmCurrencyChanges() {
      if (!this.pendingFromCurrency && !this.pendingToCurrency) {
        return { success: false, reason: 'no_pending_changes' };
      }
  
      const newFromCurrency = this.pendingFromCurrency || this.currentFromCurrency;
      const newToCurrency = this.pendingToCurrency || this.currentToCurrency;
  
      // 清除待確認狀態
      this.clearPendingChanges();
  
      // 執行切換
      return await this.switchCurrencies(newFromCurrency, newToCurrency, 'confirm');
    }
  
    // 設置待確認的貨幣
    setPendingCurrency(type, currency) {
      if (type === 'from') {
        this.pendingFromCurrency = currency;
      } else if (type === 'to') {
        this.pendingToCurrency = currency;
      }
      
      this.showConfirmButton();
    }
  
    // 清除待確認狀態
    clearPendingChanges() {
      this.pendingFromCurrency = null;
      this.pendingToCurrency = null;
      this.hideConfirmButton();
      this.updateCurrencySelectors();
    }
  
    // 顯示確認按鈕
    showConfirmButton() {
      const confirmBtn = document.getElementById('confirm-currency-btn');
      if (confirmBtn) {
        confirmBtn.style.display = 'block';
      }
    }
  
    // 隱藏確認按鈕
    hideConfirmButton() {
      const confirmBtn = document.getElementById('confirm-currency-btn');
      if (confirmBtn) {
        confirmBtn.style.display = 'none';
      }
    }
  
    // 更新貨幣選擇器顯示
    updateCurrencySelectors() {
      const fromCurrencySelect = document.getElementById('from-currency');
      const toCurrencySelect = document.getElementById('to-currency');
      
      if (fromCurrencySelect) {
        fromCurrencySelect.value = this.currentFromCurrency;
      }
      if (toCurrencySelect) {
        toCurrencySelect.value = this.currentToCurrency;
      }
      
      if (this.deps.updateCurrencyDisplay) {
        this.deps.updateCurrencyDisplay('from-currency');
        this.deps.updateCurrencyDisplay('to-currency');
      }
    }
  }

// 導出 CurrencyManager 類別
export { CurrencyManager };