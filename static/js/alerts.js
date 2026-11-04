import { setupAlertPopup } from './alert_popup.js';

export function setupAlerts(currencyManager) {
  const form = document.getElementById('alert-form');
  if (!form) {
    setupAlertPopup();
    return;
  }
  const pair = document.getElementById('alert-pair');
  const message = document.getElementById('alert-message');
  const button = form.querySelector('button[type="submit"]');
  const updatePair = () => {
    pair.textContent = `1 ${currencyManager.currentFromCurrency} 可兌換多少 ${currencyManager.currentToCurrency}`;
  };
  setupAlertPopup(updatePair);
  updatePair();
  form.addEventListener('focusin', updatePair);
  document.addEventListener('alert-currency-changed', updatePair);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    updatePair();
    if (!currencyManager.canSwitchCurrency()) {
      message.textContent = '幣別正在更新，請完成後再申請。';
      return;
    }
    button.disabled = true;
    message.textContent = '正在申請，請稍候…';
    try {
      const response = await fetch('/api/alerts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: form.elements.email.value.trim(),
          buy_currency: currencyManager.currentFromCurrency,
          sell_currency: currencyManager.currentToCurrency,
          direction: form.elements.direction.value,
          target_rate: form.elements.target_rate.value
        })
      });
      const data = await response.json();
      message.textContent = response.ok ? data.message : (data.error || '申請失敗，請稍後再試。');
    } catch {
      message.textContent = '無法確認申請結果，請先檢查信箱，再於稍後重試。';
    } finally {
      button.disabled = false;
      if (document.getElementById('alert-popup-overlay')?.classList.contains('show') &&
          document.activeElement === document.body) button.focus();
    }
  });
}
