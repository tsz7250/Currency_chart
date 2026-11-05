export function setupForecast(manager) {
  const select = document.getElementById('forecast-horizon');
  const status = document.getElementById('forecast-status');
  const retry = document.getElementById('forecast-retry');
  const overlay = document.getElementById('forecast-popup-overlay');
  const trigger = document.getElementById('forecast-btn');
  const closeButton = document.getElementById('forecast-close');
  const canvas = document.getElementById('forecastChart');
  const metrics = document.getElementById('forecast-metrics');
  let chart = null;
  let opened = false;
  let previousOverflow = '';
  let result = null;
  let sequence = 0;
  let timer = null;
  let controller = null;
  const matches = data => data.buy_currency === manager.currentFromCurrency && data.sell_currency === manager.currentToCurrency;
  function redraw() {
    chart?.destroy();
    chart = null;
    if (!result) return;
    const history = result.recent_history;
    const labels = [...history.map(p => p.date), ...result.points.map(p => p.date)];
    const anchor = history.at(-1).rate;
    const prefix = Array(6).fill(null);
    const datasets = [{ label: '實際匯率', data: [...history.map(p => p.rate), ...result.points.map(() => null)],
      borderColor: '#2E86AB', pointRadius: 3, spanGaps: false }];
    for (const [key, label] of [['low80', '區間下界'], ['high80', '校準價格範圍'], ['median', '預測中位數']]) {
      datasets.push({ label, data: [...prefix, anchor, ...result.points.map(p => p[key])],
        borderColor: '#7954a1', backgroundColor: 'rgba(121,84,161,0.16)',
        borderWidth: key === 'median' ? 2 : 0, borderDash: [5, 4], pointRadius: 0,
        fill: key === 'high80' ? 1 : false, spanGaps: false, tension: 0 });
    }
    canvas.setAttribute('aria-label', `${result.buy_currency} 對 ${result.sell_currency}，近 7 天實際走勢與未來 ${select.value} 天預測`);
    chart = new Chart(canvas.getContext('2d'), { type: 'line', data: { labels, datasets },
      options: { responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { filter: item => item.datasetIndex !== 1 } },
          title: { display: true, text: `1 ${result.buy_currency} = ? ${result.sell_currency}` },
          annotation: { annotations: { start: { type: 'line', xMin: 6, xMax: 6,
            borderColor: '#7954a1', borderDash: [3, 3], borderWidth: 1,
            label: { display: true, content: '預測起點', position: 'start' } } } } },
        scales: { x: { ticks: { maxTicksLimit: 6, maxRotation: 0, callback: value => labels[value]?.slice(5) } }, y: { title: { display: true, text: '匯率' } } } } });
  }
  function clear() {
    sequence++;
    clearTimeout(timer);
    controller?.abort();
    result = null;
    retry.hidden = true;
    metrics.textContent = '';
    redraw();
  }
  async function load(attempt = 0, token = sequence) {
    if (!opened) return;
    controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const params = new URLSearchParams({ buy_currency: manager.currentFromCurrency,
        sell_currency: manager.currentToCurrency, horizon: select.value });
      const response = await fetch(`/api/forecast?${params}`, { signal: controller.signal });
      const data = await response.json();
      if (token !== sequence || (data.buy_currency && !matches(data))) return;
      if (response.status === 202) {
        status.textContent = data.message;
        if (attempt < 299) timer = setTimeout(() => load(attempt + 1, token), 2000);
        else { status.textContent = '預測準備時間較長，請稍後重試。'; retry.hidden = false; }
        return;
      }
      if (!response.ok || data.status !== 'ready') {
        status.textContent = data.message || '預測暫時無法使用。';
        retry.hidden = true;
        if (!response.ok) retry.hidden = false;
        return;
      }
      result = data;
      const end = data.points.at(-1);
      const stats = data.backtest.calibrated;
      const coverage = stats.coverage80 == null ? '尚無驗證結果' : `實測涵蓋率 ${(stats.coverage80 * 100).toFixed(1)}%（${stats.samples} 次）`;
      const range = end.low80 == null ? `校準樣本不足（${end.calibration_samples} 筆，至少需要 20 筆）` : `期末校準價格範圍 ${end.low80.toPrecision(5)}～${end.high80.toPrecision(5)}`;
      const baseline = data.backtest.flat?.mae;
      const change = baseline ? (data.backtest.raw.mae / baseline - 1) * 100 : null;
      const comparison = change == null ? '' : `｜中心值回測誤差較維持最新匯率${change <= 0 ? '低' : '高'} ${Math.abs(change).toFixed(1)}%`;
      const coverageWarning = stats.coverage80 != null && stats.coverage80 < 0.8 ? '｜注意：實測涵蓋率低於 80% 目標' : '';
      status.textContent = `資料基準日 ${data.as_of}｜自基準日起 ${select.value} 天，目標 ${end.date}｜期末中位數 ${end.median.toPrecision(5)}｜${range}｜歷史回放：${coverage}${comparison}${coverageWarning}${data.experimental ? '｜實驗性：驗證樣本不足' : ''}。80% 為各目標日的涵蓋目標，不保證整條路徑均在區間內。`;
      const score = data.backtest.classification;
      const percent = value => value == null ? '尚無結果' : `${(value * 100).toFixed(1)}%`;
      metrics.textContent = score ? `方向回測 ${score.samples} 次｜Accuracy ${percent(score.accuracy)}｜Precision ${percent(score.precision)}｜Recall ${percent(score.recall)}｜F1 ${percent(score.f1)}｜價格 MAE ${data.backtest.raw.mae?.toPrecision(5) ?? '尚無結果'}` : '方向評分尚無結果。';
      redraw();
    } catch (error) {
      if (token !== sequence) return;
      status.textContent = '預測載入失敗或逾時，請重試。';
      retry.hidden = false;
    } finally { clearTimeout(timeout); }
  }
  function refresh() { clear(); if (opened) { status.textContent = '正在準備預測…'; load(); } }
  function close() {
    opened = false;
    clear();
    overlay.classList.remove('show');
    overlay.style.display = 'none';
    overlay.setAttribute('aria-hidden', 'true');
    trigger.setAttribute('aria-expanded', 'false');
    document.body.style.overflow = previousOverflow;
    trigger.focus();
  }
  trigger.addEventListener('click', () => {
    if (opened) return;
    opened = true;
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    overlay.style.display = 'flex';
    overlay.classList.add('show');
    overlay.setAttribute('aria-hidden', 'false');
    trigger.setAttribute('aria-expanded', 'true');
    closeButton.focus();
    refresh();
  });
  closeButton.addEventListener('click', close);
  overlay.addEventListener('click', event => { if (event.target === overlay) close(); });
  document.addEventListener('keydown', event => {
    if (!opened) return;
    if (event.key === 'Escape') { event.preventDefault(); close(); }
    if (event.key === 'Tab') {
      const items = [closeButton, select, ...(!retry.hidden ? [retry] : [])];
      const first = items[0], last = items.at(-1);
      if (event.shiftKey && (document.activeElement === first || !overlay.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !overlay.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    }
  });
  select.addEventListener('change', refresh);
  retry.addEventListener('click', refresh);
  document.addEventListener('forecast-history-ready', event => { if (opened && matches(event.detail)) refresh(); });
  document.addEventListener('alert-currency-changed', refresh);
}
