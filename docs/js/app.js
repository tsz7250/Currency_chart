/**
 * TWD-HKD 靜態匯率展示 — 原版視覺復刻與 Chart.js 渲染
 * ponytail: 純前端無後端依賴，復刻原版圖表與微軟正黑體排版
 */

let rateData = {};
let currentPeriod = 7;
let chartInstance = null;

// 全域 Chart.js 字型設置為微軟正黑體
Chart.defaults.font.family = "'Microsoft JhengHei', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";

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

// 頁面載入
document.addEventListener('DOMContentLoaded', async () => {
  await loadData();
  setupPeriodButtons();
  render();
});

async function loadData() {
  try {
    const resp = await fetch('data/TWD-HKD_180d.json');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    rateData = await resp.json();
  } catch (e) {
    console.error('載入數據失敗:', e);
    const spinner = document.getElementById('chartSpinner');
    if (spinner) spinner.innerHTML = '<p>❌ 載入數據失敗</p>';
  }
}

function setupPeriodButtons() {
  document.querySelectorAll('.period-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      currentPeriod = parseInt(btn.dataset.period);
      document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      render();
    });
  });
}

function getFilteredData(days) {
  const entries = Object.entries(rateData)
    .map(([date, info]) => ({ date, rate: info.rate }))
    .sort((a, b) => a.date.localeCompare(b.date));

  if (entries.length === 0) return [];

  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  const year = cutoff.getFullYear();
  const month = String(cutoff.getMonth() + 1).padStart(2, '0');
  const day = String(cutoff.getDate()).padStart(2, '0');
  const cutoffStr = `${year}-${month}-${day}`;

  return entries.filter(e => e.date >= cutoffStr);
}

function render() {
  const data = getFilteredData(currentPeriod);

  if (data.length === 0) {
    document.getElementById('chartSpinner').style.display = 'flex';
    document.getElementById('chartSpinner').innerHTML = '<p>📭 無數據</p>';
    document.getElementById('rateChart').style.display = 'none';
    return;
  }

  renderChart(data);
  renderLatestRate(data);
  renderStats(data);
}

function renderChart(data) {
  const canvas = document.getElementById('rateChart');
  const spinner = document.getElementById('chartSpinner');

  spinner.style.display = 'none';
  canvas.style.display = 'block';

  const labels = data.map(d => d.date);
  const rates = data.map(d => d.rate);

  if (chartInstance) {
    chartInstance.destroy();
  }

  const periodNames = { 7: '近1週', 30: '近1個月', 90: '近3個月', 180: '近6個月' };
  const periodLabel = periodNames[currentPeriod] || `近${currentPeriod}天`;

  // 計算統計值與極值索引
  const maxRate = Math.max(...rates);
  const minRate = Math.min(...rates);
  // ponytail: round avgRate to 4 decimal places to align visually with 4-decimal Y axis ticks
  const avgRate = Number((rates.reduce((s, r) => s + r, 0) / rates.length).toFixed(4));
  const maxIndex = rates.indexOf(maxRate);
  const minIndex = rates.indexOf(minRate);
  const maxDate = labels[maxIndex];
  const minDate = labels[minIndex];

  // ponytail: Y 軸緩衝
  const yRange = maxRate - minRate || 0.001;
  const yPaddingTop = yRange * 0.22;
  const yPaddingBottom = yRange * 0.15;

  // 極值點 X 軸位移防止左右邊界裁切
  const calcXAdjust = (idx, total) => {
    if (idx === 0 || (idx / total) < 0.08) return 20;
    if (idx === total - 1 || (idx / total) > 0.92) return -20;
    return 0;
  };

  // ponytail: 組合 annotations 配置（極值點標籤）
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
      xValue: maxDate,
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
      xValue: minDate,
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
      labels,
      datasets: [{
        label: '匯率',
        data: rates,
        borderColor: '#2E86AB',
        backgroundColor: 'rgba(46, 134, 171, 0.05)',
        borderWidth: 2,
        pointRadius: data.length > 90 ? 2 : 3.5,
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
          text: `TWD 到 HKD 匯率走勢圖 (${periodLabel})`,
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
            title: (items) => items[0].label,
            label: (item) => `匯率: ${item.raw.toFixed(7)}`,
          }
        }
      },
      scales: {
        x: {
          offset: true,
          title: {
            display: true,
            text: '日期',
            color: '#2c3e50',
            font: { size: 13, weight: 'bold' }
          },
          grid: { color: 'rgba(0,0,0,0.06)' },
          ticks: {
            maxTicksLimit: 12,
            color: '#333',
            font: { size: 12 },
            callback: function(val) {
              const dateStr = this.getLabelForValue(val);
              if (!dateStr) return '';
              const parts = dateStr.split('-');
              return parts.length === 3 ? `${parts[1]}/${parts[2]}` : dateStr;
            }
          }
        },
        y: {
          min: minRate - yPaddingBottom,
          max: maxRate + yPaddingTop,
          title: {
            display: true,
            text: '匯率',
            color: '#2c3e50',
            font: { size: 13, weight: 'bold' }
          },
          grid: { color: 'rgba(0,0,0,0.06)' },
          ticks: {
            color: '#333',
            font: { size: 12 },
            callback: (v) => v.toFixed(4),
          }
        }
      }
    }
  });
}

function renderLatestRate(data) {
  const container = document.getElementById('latest-rate-content');
  
  const allEntries = Object.entries(rateData)
    .map(([date, info]) => ({ date, rate: info.rate }))
    .sort((a, b) => a.date.localeCompare(b.date));

  if (allEntries.length === 0) return;

  const latest = allEntries[allEntries.length - 1];
  const prev = allEntries.length >= 2 ? allEntries[allEntries.length - 2] : null;

  const formatDate = (dateStr) => {
    try {
      const d = new Date(dateStr + 'T00:00:00');
      return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
    } catch {
      return dateStr;
    }
  };

  let trendInfo = { icon: '➡️', text: '不變', class: 'stable' };
  if (prev) {
    const diff = latest.rate - prev.rate;
    if (diff > 0.00001) {
      trendInfo = { icon: '📈', text: `漲價 ${diff.toFixed(4)}`, class: 'up' };
    } else if (diff < -0.00001) {
      trendInfo = { icon: '📉', text: `降價 ${Math.abs(diff).toFixed(4)}`, class: 'down' };
    }
  }

  const inverted = 1 / latest.rate;

  const last7Entries = allEntries.slice(-7);
  const min7d = Math.min(...last7Entries.map(e => e.rate));
  const isBest = latest.rate <= min7d;
  const bestHtml = isBest
    ? `<div class="rate-best">目前匯率是近7天最低</div>`
    : `<div class="rate-lowest">近7天最低: ${min7d.toFixed(4)}</div>`;

  container.innerHTML = `
    <div class="rate-display">
      <div class="rate-info">
        <div class="rate-date">📅 ${formatDate(latest.date)}</div>
        <div class="rate-trend ${trendInfo.class}">
          <span class="trend-icon">${trendInfo.icon}</span>
          <span>${trendInfo.text}</span>
        </div>
      </div>
      <div class="rate-main">
        <div class="rate-value">${latest.rate.toFixed(4)}(${inverted.toFixed(4)})</div>
        <div class="rate-label">1 TWD = ? HKD</div>
      </div>
      <div class="rate-info">
        ${bestHtml}
      </div>
    </div>
  `;
}

function renderStats(data) {
  const rates = data.map(d => d.rate);
  const max = Math.max(...rates);
  const min = Math.min(...rates);
  const avg = rates.reduce((s, r) => s + r, 0) / rates.length;

  document.getElementById('maxRate').textContent = `最高匯率: ${max.toFixed(4)}`;
  document.getElementById('minRate').textContent = `最低匯率: ${min.toFixed(4)}`;
  document.getElementById('avgRate').textContent = `平均匯率: ${avg.toFixed(4)}`;
  document.getElementById('dataPoints').textContent = `數據點: ${data.length}`;
  document.getElementById('dateRange').textContent = `數據範圍: ${data[0].date} 至 ${data[data.length - 1].date}`;
}
