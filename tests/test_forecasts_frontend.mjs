import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const handlers = new Map(), events = new Map(), timers = new Map();
const controls = {}, requests = [], charts = [];
let nextTimer = 0;
const document = { body: { style: { overflow: '' } }, getElementById: id => controls[id],
  addEventListener: (event, callback) => events.set(event, callback) };
for (const id of ['forecast-horizon', 'forecast-status', 'forecast-retry', 'forecast-popup-overlay', 'forecast-btn', 'forecast-close', 'forecastChart', 'forecast-metrics']) {
  controls[id] = { value: id === 'forecast-horizon' ? '7' : '', hidden: true, style: {},
    classList: { add() {}, remove() {} }, setAttribute() {}, getContext() { return id; },
    focus() { document.activeElement = this; }, contains(el) { return Object.values(controls).includes(el); },
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
assert.equal(events.has('forecast-chart-data'), false);
handlers.get('forecast-btn:click')();
assert.equal(document.activeElement, controls['forecast-close']);
requests[0].resolve({ status: 202, json: async () => ({ status: 'pending', message: '等待資料' }) });
await new Promise(setImmediate);
[...timers.values()].find(timer => timer.delay === 2000).callback();
const ready = { buy_currency: 'TWD', sell_currency: 'HKD', status: 'ready', as_of: '2026-11-04', experimental: true,
  recent_history: Array.from({ length: 7 }, (_, i) => ({ date: `2026-10-${25+i}`, rate: i === 1 ? null : 1 })),
  points: [{ date: '2026-11-05', median: 1, low80: .9, high80: 1.1, calibration_samples: 20 }],
  backtest: { raw: { mae: .01 }, calibrated: { samples: 7, coverage80: .7 },
    classification: { samples: 20, accuracy: .5, precision: .3, recall: .3, f1: .3 } } };
requests[1].resolve({ status: 200, ok: true, json: async () => ready });
await new Promise(setImmediate);
assert.equal(charts.at(-1).canvas, 'forecastChart');
assert.equal(charts.at(-1).config.data.datasets[0].data[1], null);
assert.equal(charts.at(-1).config.data.datasets[2].fill, 1);
assert.match(controls['forecast-metrics'].textContent, /Accuracy 50.0%/);
assert.match(controls['forecast-status'].textContent, /實驗性/);
assert.match(controls['forecast-status'].textContent, /資料基準日 2026-11-04/);
controls['forecast-horizon'].value = '14';
handlers.get('forecast-horizon:change')();
assert.equal(charts[0].destroyed, true);
assert.match(requests.at(-1).url, /horizon=14/);
manager.currentFromCurrency = 'USD';
events.get('alert-currency-changed')();
requests[2].resolve({ status: 200, ok: true, json: async () => ready });
await new Promise(setImmediate);
assert.equal(charts.length, 1, '忽略舊幣對回應');
const before = requests.length;
events.get('forecast-history-ready')({ detail: { buy_currency: 'USD', sell_currency: 'HKD' } });
assert.equal(requests.length, before + 1);
events.get('keydown')({ key: 'Escape', preventDefault() {} });
assert.equal(requests.at(-1).options.signal.aborted, true);
assert.equal(document.activeElement, controls['forecast-btn']);
assert.equal(document.body.style.overflow, '');
requests.at(-1).resolve({ status: 200, ok: true, json: async () => ({ ...ready, buy_currency: 'USD' }) });
await new Promise(setImmediate);
assert.equal(charts.length, 1, '關閉後不繪圖');
handlers.get('forecast-btn:click')();
assert.equal(requests.length, before + 2);
console.log('獨立預測彈窗測試通過：開關、缺值、區間、評分、切換、過期回應、更新。');
