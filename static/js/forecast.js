const QUANTILE_Z = 1.2815515655446004;

// Abramowitz-Stegun 7.1.26，|誤差| < 7.5e-8。
function phi(z) {
  const t = 1 / (1 + .2316419 * Math.abs(z));
  const poly = t * (.31938153 + t * (-.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  const cdf = 1 - .3989422804014327 * Math.exp(-z * z / 2) * poly;
  return z >= 0 ? cdf : 1 - cdf;
}

export function setupForecast(manager) {
  const select = document.getElementById('forecast-horizon');
  const status = document.getElementById('forecast-status');
  const retry = document.getElementById('forecast-retry');
  const overlay = document.getElementById('forecast-popup-overlay');
  const trigger = document.getElementById('forecast-btn');
  const closeButton = document.getElementById('forecast-close');
  const canvas = document.getElementById('forecastChart');
  const metrics = document.getElementById('forecast-metrics');
  const target = document.getElementById('forecast-target');
  const alertLink = document.getElementById('forecast-alert-link');
  const facts = document.getElementById('forecast-facts');
  const warning = document.getElementById('forecast-warning');
  const details = document.getElementById('forecast-details');
  const touchLabel = document.getElementById('forecast-label-touch');
  const diffLabel = document.getElementById('forecast-label-diff');
  const touchValue = document.getElementById('forecast-target-touch');
  const endValue = document.getElementById('forecast-target-end');
  const diffValue = document.getElementById('forecast-target-diff');
  const targetNote = document.getElementById('forecast-target-note');
  const directionMain = document.getElementById('forecast-direction-main');
  const directionSub = document.getElementById('forecast-direction-sub');
  let chart = null;
  let opened = false;
  let previousOverflow = '';
  let result = null;
  let sequence = 0;
  let timer = null;
  let controller = null;
  const matches = data => data.buy_currency === manager.currentFromCurrency && data.sell_currency === manager.currentToCurrency;
  // 帶隱含期末標準差（log 尺度）；缺校準時 null。
  function bandSigma(point) {
    return point.low80 == null || point.high80 == null ? null : Math.log(point.high80 / point.low80) / (2 * QUANTILE_Z);
  }
  function anchorRate() {
    return result ? [...result.recent_history].reverse().find(p => p.rate != null)?.rate ?? null : null;
  }
  const percent = value => value == null ? '尚無結果' : `${(value * 100).toFixed(1)}%`;
  const signed = value => `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;
  // 傾斜方向 d = P(up)−P(down)，在 7/14/30 節點間線性內插；無融合資料回傳 null。
  function drift(h) {
    const knots = [7, 14, 30].map(k => [k, result.directions?.[String(k)]])
      .filter(([, v]) => v != null).map(([k, v]) => [k, v[2] - v[0]]);
    if (!knots.length) return null;
    if (h <= knots[0][0]) return knots[0][1];
    for (let i = 1; i < knots.length; i++) {
      const [h0, v0] = knots[i - 1], [h1, v1] = knots[i];
      if (h <= h1) return v0 + (v1 - v0) * (h - h0) / (h1 - h0);
    }
    return knots.at(-1)[1];
  }
  function tiltFactors() {
    if (!result.directions) return null;
    return result.points.map((point, i) => {
      const sigma = bandSigma(point), dv = drift(i + 1);
      return sigma == null || dv == null ? 1 : Math.exp(dv * sigma);
    });
  }
  // 方向卡：無融合模型時顯示帶推論的誠實狀態（多為無方向優勢）；有模型時顯示模型方向。
  function renderDirection() {
    const end = result?.points.at(-1);
    const anchor = anchorRate();
    if (!end || anchor == null) {
      directionMain.textContent = '方向資料不足。';
      directionSub.textContent = '';
      return;
    }
    const sigma = bandSigma(end);
    if (sigma == null) {
      directionMain.textContent = '方向資料不足。';
      directionSub.textContent = `校準樣本不足（${end.calibration_samples} 筆，至少需要 20 筆）。`;
      return;
    }
    const span = `期末 80% 區間相對現值 ${signed((end.low80 / anchor - 1) * 100)}～${signed((end.high80 / anchor - 1) * 100)}`;
    const dirs = result.directions?.[select.value];
    if (dirs) {
      const up = dirs[2], down = dirs[0], gap = up - down;
      if (Math.abs(gap) < .05) directionMain.textContent = `→ 模型無明顯方向（升 ${percent(up)}／跌 ${percent(down)}，實驗性）`;
      else directionMain.textContent = `${gap > 0 ? '↑' : '↓'} 模型傾向${gap > 0 ? '升' : '跌'}（升 ${percent(up)}／跌 ${percent(down)}，實驗性）`;
    } else {
      const pUp = Math.min(1, Math.max(0, phi(Math.log(end.median / anchor) / sigma)));
      if (Math.abs(pUp - .5) <= .05) directionMain.textContent = `→ 目前無方向優勢（升 ${(pUp * 100).toFixed(1)}%／跌 ${((1 - pUp) * 100).toFixed(1)}%）`;
      else directionMain.textContent = `${pUp > .5 ? '↑' : '↓'} 帶輕微傾向${pUp > .5 ? '升' : '跌'}（升 ${(pUp * 100).toFixed(1)}%／跌 ${((1 - pUp) * 100).toFixed(1)}%，區間推論）`;
    }
    directionSub.textContent = span;
  }
  function redraw() {
    chart?.destroy();
    chart = null;
    if (!result) return;
    const history = result.recent_history;
    const labels = [...history.map(p => p.date), ...result.points.map(p => p.date)];
    const anchor = history.at(-1).rate;
    const percentTicks = anchor != null && anchor > 0;
    const prefix = Array(6).fill(null);
    const factors = tiltFactors();
    const shifted = (key, i) => {
      const value = result.points[i][key];
      return value == null ? null : value * (factors ? factors[i] : 1);
    };
    const datasets = [{ label: '實際匯率', data: [...history.map(p => p.rate), ...result.points.map(() => null)],
      borderColor: '#2E86AB', pointRadius: 3, spanGaps: true }];
    for (const [key, get] of [['low80', i => shifted('low80', i)], ['high80', i => shifted('high80', i)]]) {
      datasets.push({ label: key === 'low80' ? '區間下界' : '校準價格範圍', data: [...prefix, anchor, ...result.points.map((_, i) => get(i))],
        borderColor: '#7954a1', backgroundColor: 'rgba(121,84,161,0.16)',
        borderWidth: 0, borderDash: [5, 4], pointRadius: 0,
        fill: key === 'high80' ? 1 : false, spanGaps: false, tension: 0 });
    }
    if (factors) {
      datasets.push({ label: '傾斜預測（實驗性）', data: [...prefix, anchor, ...result.points.map((p, i) => p.median * factors[i])],
        borderColor: '#7954a1', borderWidth: 2, borderDash: [5, 4], pointRadius: 0, fill: false, spanGaps: false, tension: 0 });
    }
    const offset = value => percentTicks ? signed((value / anchor - 1) * 100) : `${value}`;
    canvas.setAttribute('aria-label', `${result.buy_currency} 對 ${result.sell_currency}，近 7 天實際走勢與未來 ${select.value} 天預測`);
    chart = new Chart(canvas.getContext('2d'), { type: 'line', data: { labels, datasets },
      options: { responsive: true, maintainAspectRatio: false, animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { filter: item => item.datasetIndex !== 1 } },
          title: { display: true, text: `1 ${result.buy_currency} = ? ${result.sell_currency}` },
          tooltip: { callbacks: percentTicks ? { label: ctx => ctx.parsed.y == null ? '' :
              `${ctx.dataset.label} ${ctx.parsed.y.toPrecision(5)}（${offset(ctx.parsed.y)}）` } : {} },
          annotation: { annotations: { start: { type: 'line', xMin: 6, xMax: 6,
              borderColor: '#7954a1', borderDash: [3, 3], borderWidth: 1,
              label: { display: true, content: '預測起點', position: 'start', font: { size: 10 } } },
            ...(anchor != null ? { now: { type: 'line', yMin: anchor, yMax: anchor,
              borderColor: '#2E86AB', borderDash: [2, 3], borderWidth: 1 } } : {}) } } },
        scales: { x: { ticks: { maxTicksLimit: 6, maxRotation: 0, callback: value => labels[value]?.slice(5) } },
          y: { title: { display: true, text: percentTicks ? '相對現值' : '匯率' },
            ticks: percentTicks ? { callback: offset } : {} } } } });
    renderDirection();
    updateCalculator();
  }
  // 換匯決策計算：觸及與期末機率用 log 常態近似，含傾斜漂移；夾限 [0,1]。
  function updateCalculator() {
    if (!result) return;
    const end = result.points.at(-1);
    const wanted = parseFloat(target.value);
    const anchor = anchorRate();
    touchLabel.textContent = `天內觸及`;
    diffLabel.textContent = `每 1 萬 ${result.buy_currency} 差`;
    if (!end || !isFinite(wanted) || wanted <= 0 || anchor == null) {
      touchValue.textContent = endValue.textContent = diffValue.textContent = '—';
      targetNote.textContent = '';
      return;
    }
    const sigma = bandSigma(end);
    if (sigma == null) {
      touchValue.textContent = endValue.textContent = diffValue.textContent = '—';
      targetNote.textContent = `校準樣本不足（${end.calibration_samples} 筆，至少需要 20 筆），無法估計到價機率。`;
      return;
    }
    targetNote.textContent = '';
    const horizon = parseInt(select.value, 10);
    touchLabel.textContent = `${horizon} 天內觸及`;
    const dirs = result.directions?.[String(horizon)];
    const nu = dirs ? (dirs[2] - dirs[0]) * sigma : 0;
    const m = Math.log(wanted / anchor);
    let touch;
    if (Math.abs(m) < 1e-12) touch = 1;
    else if (m < 0) touch = phi((m - nu) / sigma) + Math.exp(2 * nu * m / (sigma * sigma)) * phi((m + nu) / sigma);
    else touch = phi((nu - m) / sigma) + Math.exp(2 * nu * m / (sigma * sigma)) * phi(-(m + nu) / sigma);
    touch = Math.min(1, Math.max(0, touch));
    // 「更好」＝相對現值往 X 的方向：m>0 等期末 ≥ X；m<0 等期末 ≤ X。
    const endBetter = m >= 0 ? phi((nu - m) / sigma) : phi((m - nu) / sigma);
    const diff = (wanted - anchor) * 10000;
    touchValue.textContent = percent(touch);
    endValue.textContent = percent(Math.min(1, Math.max(0, endBetter)));
    diffValue.textContent = `${diff >= 0 ? '+' : ''}${diff.toFixed(0)} ${result.sell_currency}（${signed((wanted / anchor - 1) * 100)}）`;
  }
  function clear() {
    sequence++;
    clearTimeout(timer);
    controller?.abort();
    result = null;
    retry.hidden = true;
    metrics.textContent = '';
    facts.hidden = true;
    warning.hidden = true;
    touchValue.textContent = endValue.textContent = diffValue.textContent = '—';
    targetNote.textContent = '';
    directionMain.textContent = '—';
    directionSub.textContent = '';
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
        status.hidden = false;
        status.textContent = data.message;
        if (attempt < 299) timer = setTimeout(() => load(attempt + 1, token), 2000);
        else { status.textContent = '預測準備時間較長，請稍後重試。'; retry.hidden = false; }
        return;
      }
      if (!response.ok || data.status !== 'ready') {
        status.hidden = false;
        status.textContent = data.message || '預測暫時無法使用。';
        retry.hidden = true;
        if (!response.ok) retry.hidden = false;
        return;
      }
      result = data;
      status.hidden = true;
      const end = data.points.at(-1);
      const stats = data.backtest.calibrated;
      const factsList = [`基準 ${data.as_of} → 目標 ${end.date}`];
      factsList.push(end.low80 != null ? `期末 80% 區間 ${end.low80.toPrecision(5)}~${end.high80.toPrecision(5)}`
        : `校準樣本不足（${end.calibration_samples} 筆，至少需要 20 筆）`);
      factsList.push(stats.coverage80 == null ? '尚無驗證結果'
        : `實測涵蓋率 ${(stats.coverage80 * 100).toFixed(1)}%（${stats.samples} 次）`);
      facts.hidden = false;
      facts.textContent = factsList.join('　·　');
      const warnings = [];
      if (stats.coverage80 != null && stats.coverage80 < 0.8) warnings.push('實測涵蓋率低於 80% 目標');
      if (data.experimental) warnings.push('驗證樣本不足（實驗性）');
      if (data.directions) warnings.push('傾斜路徑為實驗性融合模型：滾動回測起點含模型訓練期，分數會偏高數月');
      warning.hidden = warnings.length === 0;
      warning.textContent = warnings.join('｜');
      const score = data.backtest.classification;
      let text = score ? `方向回測 ${score.samples} 次｜Accuracy ${percent(score.accuracy)}｜Precision ${percent(score.precision)}｜Recall ${percent(score.recall)}｜F1 ${percent(score.f1)}｜價格 MAE ${data.backtest.raw.mae?.toPrecision(5) ?? '尚無結果'}` : '方向評分尚無結果。';
      const baseline = data.backtest.flat?.mae;
      const change = baseline ? (data.backtest.raw.mae / baseline - 1) * 100 : null;
      if (change != null) text += `｜中心值回測誤差較維持最新匯率${change <= 0 ? '低' : '高'} ${Math.abs(change).toFixed(1)}%`;
      const blend = data.backtest.blend;
      if (blend?.samples) text += `｜傾斜路徑回測（實驗性）${blend.samples} 次｜Accuracy ${percent(blend.classification.accuracy)}｜F1 ${percent(blend.classification.f1)}`;
      metrics.textContent = text;
      const anchor = anchorRate();
      target.value = anchor != null ? (anchor * 1.005).toPrecision(6) : '';
      redraw();
    } catch (error) {
      if (token !== sequence) return;
      status.hidden = false;
      status.textContent = '預測載入失敗或逾時，請重試。';
      retry.hidden = false;
    } finally { clearTimeout(timeout); }
  }
  function refresh() {
    clear();
    if (opened) { status.hidden = false; status.textContent = '正在準備預測…'; load(); }
  }
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
      const summary = details?.querySelector?.('summary');
      const items = [closeButton, select, target, summary ?? details, ...(!retry.hidden ? [retry] : [])];
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
  target.addEventListener('input', updateCalculator);
  alertLink.addEventListener('click', event => {
    event.preventDefault();
    const wanted = parseFloat(target.value);
    if (isFinite(wanted) && wanted > 0) {
      const anchor = result ? anchorRate() : null;
      const alertTarget = document.getElementById('alert-target');
      if (alertTarget) alertTarget.value = wanted;
      const direction = document.getElementById('alert-direction');
      if (direction && anchor != null) direction.value = wanted > anchor ? 'gte' : 'lte';
    }
    close();
    document.getElementById('alert-btn')?.click();
  });
  document.addEventListener('forecast-history-ready', event => { if (opened && matches(event.detail)) refresh(); });
  document.addEventListener('alert-currency-changed', refresh);
}
