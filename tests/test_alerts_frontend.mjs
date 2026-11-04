import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { setupAlerts } from '../static/js/alerts.js';

const handlers = {};
const fields = {
  email: { value: ' user@example.com ' },
  direction: { value: 'gte' },
  target_rate: { value: '0.25' }
};
const button = { disabled: false };
const pair = { textContent: '' };
const message = { textContent: '' };
const form = {
  elements: fields,
  querySelector: () => button,
  addEventListener: (name, callback) => { handlers[name] = callback; }
};
globalThis.document = {
  getElementById: (id) => ({ 'alert-form': form, 'alert-pair': pair, 'alert-message': message }[id]),
  addEventListener: (name, callback) => { handlers[name] = callback; }
};
const manager = { currentFromCurrency: 'TWD', currentToCurrency: 'HKD', canSwitchCurrency: () => true };
setupAlerts(manager);
assert.equal(pair.textContent, '1 TWD 可兌換多少 HKD');
manager.currentFromCurrency = 'USD';
handlers['alert-currency-changed']();
assert.equal(pair.textContent, '1 USD 可兌換多少 HKD');
let calls = 0;
globalThis.fetch = async (url, options) => {
  calls++;
  assert.equal(url, '/api/alerts');
  assert.equal(button.disabled, true);
  assert.deepEqual(JSON.parse(options.body), {
    email: 'user@example.com', buy_currency: 'USD', sell_currency: 'HKD',
    direction: 'gte', target_rate: '0.25'
  });
  return { ok: true, json: async () => ({ message: '申請已受理' }) };
};
await handlers.submit({ preventDefault() {} });
assert.equal(message.textContent, '申請已受理');
assert.equal(button.disabled, false);
manager.canSwitchCurrency = () => false;
await handlers.submit({ preventDefault() {} });
assert.equal(calls, 1);
assert.match(message.textContent, /幣別正在更新/);
manager.canSwitchCurrency = () => true;
globalThis.fetch = async () => { throw new Error('offline'); };
await handlers.submit({ preventDefault() {} });
assert.match(message.textContent, /先檢查信箱/);
assert.equal(button.disabled, false);
globalThis.fetch = async () => ({ ok: false, json: async () => ({ error: '申請次數過多' }) });
await handlers.submit({ preventDefault() {} });
assert.equal(message.textContent, '申請次數過多');
const css = await readFile(new URL('../static/css/style.css', import.meta.url), 'utf8');
assert.match(css, /#alert-form button\s*\{[^}]*display:\s*block/);
function element(id, initial = {}) {
  const classes = new Set();
  return Object.assign({
    id, disabled: false, tabIndex: 0, style: {}, attributes: {}, handlers: {},
    classList: { add: (name) => classes.add(name), remove: (name) => classes.delete(name), contains: (name) => classes.has(name) },
    addEventListener(name, callback) { this.handlers[name] = callback; },
    setAttribute(name, value) { this.attributes[name] = value; },
    focus() { document.activeElement = this; }
  }, initial);
}
const trigger = element('alert-btn');
const close = element('alert-popup-close-btn');
const email = Object.assign(fields.email, element('alert-email'));
const direction = Object.assign(fields.direction, element('alert-direction'));
const target = Object.assign(fields.target_rate, element('alert-target'));
Object.assign(button, element('submit'));
const overlay = element('alert-popup-overlay', { style: { display: 'none' } });
const history = element('history-popup-overlay');
history.classList.add('show');
overlay.querySelectorAll = () => [close, email, direction, target, button];
overlay.contains = (item) => overlay.querySelectorAll().includes(item);
const elements = {
  'alert-btn': trigger, 'alert-popup-overlay': overlay, 'alert-popup-close-btn': close,
  'alert-email': email, 'alert-pair': pair, 'alert-message': message, 'alert-form': form,
  'history-popup-overlay': history
};
document.getElementById = (id) => elements[id];
setupAlerts(manager);
trigger.handlers.click();
assert.equal(overlay.style.display, 'flex');
assert.equal(overlay.attributes['aria-hidden'], 'false');
assert.equal(trigger.attributes['aria-expanded'], 'true');
assert.equal(document.activeElement, email);
assert.equal(history.style.display, 'none');
assert.equal(history.classList.contains('show'), false);
let prevented = false;
const key = (name, shiftKey = false) => ({ key: name, shiftKey,
  preventDefault() { prevented = true; }, stopPropagation() {} });
close.focus();
handlers.keydown(key('Tab', true));
assert.equal(document.activeElement, button);
assert.equal(prevented, true);
handlers.keydown(key('Tab'));
assert.equal(document.activeElement, close);
button.disabled = true;
close.focus();
handlers.keydown(key('Tab', true));
assert.equal(document.activeElement, target);
button.disabled = false;
overlay.handlers.click({ target: email });
assert.equal(overlay.style.display, 'flex');
handlers.keydown(key('Escape'));
assert.equal(overlay.style.display, 'none');
assert.equal(document.activeElement, trigger);
assert.equal(trigger.attributes['aria-expanded'], 'false');
const savedEmail = email.value;
const savedMessage = message.textContent;
manager.currentToCurrency = 'JPY';
trigger.handlers.click();
assert.equal(email.value, savedEmail);
assert.equal(message.textContent, savedMessage);
assert.equal(pair.textContent, '1 USD 可兌換多少 JPY');
overlay.handlers.click({ target: overlay });
assert.equal(overlay.style.display, 'none');

let finishRequest;
let pendingCalls = 0;
globalThis.fetch = async () => {
  pendingCalls++;
  return new Promise((resolve) => { finishRequest = resolve; });
};
trigger.handlers.click();
const pending = handlers.submit({ preventDefault() {} });
assert.equal(button.disabled, true);
document.body = {};
document.activeElement = document.body;
handlers.keydown(key('Tab'));
assert.equal(document.activeElement, close);
close.handlers.click();
trigger.handlers.click();
await handlers.submit({ preventDefault() {} });
assert.equal(pendingCalls, 1);
assert.equal(button.disabled, true);
document.activeElement = document.body;
finishRequest({ ok: true, json: async () => ({ message: '請至信箱確認' }) });
await pending;
assert.equal(message.textContent, '請至信箱確認');
assert.equal(button.disabled, false);
assert.equal(document.activeElement, button);

delete elements['alert-form'];
delete elements['alert-email'];
overlay.querySelectorAll = () => [close];
setupAlerts(null);
trigger.handlers.click();
assert.equal(document.activeElement, close);
handlers.keydown(key('Tab'));
assert.equal(document.activeElement, close);
assert.equal(pendingCalls, 1);
const popupModule = await readFile(new URL('../static/js/alert_popup.js', import.meta.url), 'utf8');
assert.equal(popupModule, await readFile(new URL('../docs/js/alert_popup.js', import.meta.url), 'utf8'));
console.log('前端互動自測通過：申請、彈窗、關閉、焦點、表單保留、寄送中重開、停用說明。');
