import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const handlers = new Map(), events = new Map(), timers = new Map();
const controls = {}, requests = [], charts = [];
let nextTimer = 0;
const document = { body: { style: { overflow: '' } }, getElementById: id => controls[id],
  addEventListener: (event, callback) => events.set(event, callback) };
for (const id of ['forecast-horizon', 'forecast-status', 'forecast-retry', 'forecast-popup-overlay', 'forecast-btn', 'forecast-close', 'forecastChart', 'forecast-metrics', 'forecast-target', 'forecast-alert-link', 'forecast-direction-main', 'forecast-direction-sub',
  'forecast-facts', 'forecast-warning', 'forecast-details', 'forecast-target-touch', 'forecast-target-end', 'forecast-target-diff', 'forecast-label-touch', 'forecast-label-diff', 'forecast-target-note']) {
  controls[id] = { value: id === 'forecast-horizon' ? '7' : '', hidden: id === 'forecast-status' || id === 'forecast-facts' || id === 'forecast-warning', style: {},
    classList: { add() {}, remove() {} }, setAttribute() {}, getContext() { return id; },
    focus() { document.activeElement = this; }, contains(el) { return Object.values(controls).includes(el); },
    querySelector: () => ({ focus() { document.activeElement = this; } }),
    addEventListener(event, callback) { handlers.set(`${id}:${event}`, callback); } };
}
const source = readFileSync(new URL('../static/js/forecast.js', import.meta.url), 'utf8')
  .replace('export function setupForecast', 'function setupForecast');
const context = vm.createContext({ URLSearchParams, AbortController, document,
  Chart: class { constructor(canvas, config) { this.canvas = canvas; this.config = config; charts.push(this); } destroy() { this.destroyed = true; } },
  setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, { callback, delay }); return id; },
  clearTimeout(id) { timers.delete(id); },
  fetch(url, options) { return new Promise(resolve => requests.push({ url, options, resolve })); }
});
vm.runInContext(source, context);
const manager = { currentFromCurrency: 'TWD', currentToCurrency: 'HKD' };
context.setupForecast(manager);
assert.equal(requests.length, 0);
handlers.get('forecast-btn:click')();
assert.equal(document.activeElement, controls['forecast-close']);
requests[0].resolve({ status: 202, json: async () => ({ status: 'pending', message: '等待資料' }) });
await new Promise(setImmediate);
assert.equal(controls['forecast-status'].hidden, false);
assert.match(controls['forecast-status'].textContent, /等待資料/);
[...timers.values()].find(timer => timer.delay === 2000).callback();
const ready = { buy_currency: 'TWD', sell_currency: 'HKD', status: 'ready', as_of: '2026-11-04', experimental: true,
  recent_history: Array.from({ length: 7 }, (_, i) => ({ date: `2026-10-${25+i}`, rate: i === 1 ? null : 1 })),
  points: [{ date: '2026-11-05', median: 1, low80: .9, high80: 1.1, calibration_samples: 20 }],
  backtest: { raw: { mae: .01 }, flat: { mae: .0105 }, calibrated: { samples: 7, coverage80: .7 },
    classification: { samples: 20, accuracy: .5, precision: .3, recall: .3, f1: .3 } } };
requests[1].resolve({ status: 200, ok: true, json: async () => ready });
await new Promise(setImmediate);
assert.equal(charts.at(-1).canvas, 'forecastChart');
assert.equal(charts.at(-1).config.data.datasets[0].data[1], null);
assert.equal(charts.at(-1).config.data.datasets.length, 3, '中位數線已移除，只留實際＋帶');
assert.equal(charts.at(-1).config.data.datasets[2].fill, 1);
assert.equal(charts.at(-1).config.data.datasets[0].spanGaps, true, '實際線跨接週末缺值');
// 事實列與警示行取代文字牆；ready 時狀態列隱藏。
assert.equal(controls['forecast-status'].hidden, true);
assert.equal(controls['forecast-facts'].hidden, false);
assert.match(controls['forecast-facts'].textContent, /基準 2026-11-04 → 目標 2026-11-05/);
assert.match(controls['forecast-facts'].textContent, /期末 80% 區間 0\.90000~1\.1000/);
assert.match(controls['forecast-facts'].textContent, /實測涵蓋率 70\.0%（7 次）/);
assert.equal(controls['forecast-warning'].hidden, false);
assert.match(controls['forecast-warning'].textContent, /實測涵蓋率低於 80% 目標/);
assert.match(controls['forecast-warning'].textContent, /驗證樣本不足（實驗性）/);
// y 軸相對現值百分比，0% ＝現在匯率。
const y = charts.at(-1).config.options.scales.y;
assert.equal(y.title.text, '相對現值');
assert.equal(y.ticks.callback(1), '+0.0%');
assert.equal(y.ticks.callback(1.1), '+10.0%');
assert.equal(y.ticks.callback(.9), '-10.0%');
const annotations = charts.at(-1).config.options.plugins.annotation.annotations;
assert.equal(annotations.now.yMin, 1);
// 詳情折疊：回測指標與誤差對比移入 metrics。
assert.match(controls['forecast-metrics'].textContent, /Accuracy 50.0%/);
assert.match(controls['forecast-metrics'].textContent, /中心值回測誤差較維持最新匯率低 4\.8%/);
assert.doesNotMatch(controls['forecast-status'].textContent, /期末中位數/);
// 方向卡：無融合模型 → 帶推論，中位數等於現值 → 誠實顯示無方向優勢。
assert.match(controls['forecast-direction-main'].textContent, /目前無方向優勢（升 50\.0%／跌 50\.0%）/);
assert.match(controls['forecast-direction-sub'].textContent, /期末 80% 區間相對現值 -10\.0%～\+10\.0%/);
// 換匯決策計算：現值 1，帶 0.9～1.1，目標 1.005（無融合訊號 → 無漂移反射近似）。
function phi(z) {
  const t = 1 / (1 + .2316419 * Math.abs(z));
  const poly = t * (.31938153 + t * (-.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  const cdf = 1 - .3989422804014327 * Math.exp(-z * z / 2) * poly;
  return z >= 0 ? cdf : 1 - cdf;
}
const sigma = Math.log(1.1 / .9) / (2 * 1.2815515655446004);
const m = Math.log(1.005);
controls['forecast-target'].value = '1.005';
handlers.get('forecast-target:input')({});
assert.match(controls['forecast-label-touch'].textContent, /7 天內觸及/);
assert.equal(controls['forecast-target-touch'].textContent, `${(2 * phi(-m / sigma) * 100).toFixed(1)}%`);
assert.equal(controls['forecast-target-end'].textContent, `${(phi(-m / sigma) * 100).toFixed(1)}%`);
assert.equal(controls['forecast-target-diff'].textContent, '+50 HKD（+0.5%）');
controls['forecast-target'].value = '';
handlers.get('forecast-target:input')({});
assert.equal(controls['forecast-target-touch'].textContent, '—');
assert.equal(controls['forecast-target-note'].textContent, '');
// 校準樣本不足 → 計算器與事實列都說明原因。
const thin = { ...ready, points: [{ date: '2026-11-05', median: 1, low80: null, high80: null, calibration_samples: 5 }] };
handlers.get('forecast-horizon:change')();
requests.at(-1).resolve({ status: 200, ok: true, json: async () => thin });
await new Promise(setImmediate);
controls['forecast-target'].value = '1.005';
handlers.get('forecast-target:input')({});
assert.match(controls['forecast-target-note'].textContent, /校準樣本不足（5 筆/);
assert.equal(controls['forecast-target-touch'].textContent, '—');
assert.match(controls['forecast-facts'].textContent, /校準樣本不足（5 筆/);
assert.equal(controls['forecast-direction-main'].textContent, '方向資料不足。');
// 融合傾斜路徑：d=0.3 → 帶與中位數同步上移，計算器含漂移，警示行加訓練期汙染。
const blend = { ...ready, directions: { '7': [.2, .3, .5], '14': [.25, .3, .45], '30': [.3, .3, .4] },
  backtest: { ...ready.backtest, blend: { samples: 20, coverage80: .8, mae: .009,
    classification: { samples: 20, accuracy: .6, precision: .5, recall: .5, f1: .5 } } } };
handlers.get('forecast-horizon:change')();
requests.at(-1).resolve({ status: 200, ok: true, json: async () => blend });
await new Promise(setImmediate);
assert.equal(charts.at(-1).config.data.datasets.length, 4, '融合模式加傾斜中心線');
assert.match(charts.at(-1).config.data.datasets[3].label, /傾斜預測（實驗性）/);
const factor = Math.exp((.5 - .2) * sigma);
assert.ok(Math.abs(charts.at(-1).config.data.datasets[3].data[7] - factor) < 1e-9);
assert.ok(Math.abs(charts.at(-1).config.data.datasets[2].data[7] - 1.1 * factor) < 1e-9);
assert.match(controls['forecast-metrics'].textContent, /傾斜路徑回測（實驗性）20 次/);
assert.match(controls['forecast-warning'].textContent, /實驗性融合模型/);
assert.match(controls['forecast-direction-main'].textContent, /↑ 模型傾向升（升 50\.0%／跌 20\.0%，實驗性）/);
const nu = (.5 - .2) * sigma, mb = Math.log(1.005);
const driftedTouch = Math.min(1, Math.max(0, phi((nu - mb) / sigma) + Math.exp(2 * nu * mb / (sigma * sigma)) * phi(-(mb + nu) / sigma)));
handlers.get('forecast-target:input')({});
assert.equal(controls['forecast-target-touch'].textContent, `${(driftedTouch * 100).toFixed(1)}%`);
// Tab 陷阱聚焦 details 的 summary（可聚焦元素）。
const summary = controls['forecast-details'].querySelector();
document.activeElement = summary;
events.get('keydown')({ key: 'Tab', preventDefault() {} });
assert.equal(document.activeElement, controls['forecast-close']);
controls['forecast-horizon'].value = '14';
handlers.get('forecast-horizon:change')();
assert.equal(charts[0].destroyed, true);
assert.match(requests.at(-1).url, /horizon=14/);
manager.currentFromCurrency = 'USD';
events.get('alert-currency-changed')();
requests.at(-2).resolve({ status: 200, ok: true, json: async () => ({ ...blend, buy_currency: 'TWD' }) });
await new Promise(setImmediate);
assert.equal(charts.length, 3, '忽略舊幣對回應');
const before = requests.length;
events.get('forecast-history-ready')({ detail: { buy_currency: 'USD', sell_currency: 'HKD' } });
assert.equal(requests.length, before + 1);
events.get('keydown')({ key: 'Escape', preventDefault() {} });
assert.equal(requests.at(-1).options.signal.aborted, true);
assert.equal(document.activeElement, controls['forecast-btn']);
assert.equal(document.body.style.overflow, '');
assert.equal(controls['forecast-direction-main'].textContent, '—');
assert.equal(controls['forecast-facts'].hidden, true);
requests.at(-1).resolve({ status: 200, ok: true, json: async () => ({ ...ready, buy_currency: 'USD' }) });
await new Promise(setImmediate);
assert.equal(charts.length, 3, '關閉後不繪圖');
handlers.get('forecast-btn:click')();
assert.equal(requests.length, before + 2);
console.log('獨立預測彈窗測試通過：開關、缺值、事實列、警示、百分比軸、計算器、傾斜路徑、詳情折疊、切換、過期回應。');
