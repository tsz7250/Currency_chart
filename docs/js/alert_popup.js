export function setupAlertPopup(onOpen = () => {}) {
  const trigger = document.getElementById('alert-btn');
  const overlay = document.getElementById('alert-popup-overlay');
  const closeButton = document.getElementById('alert-popup-close-btn');
  if (!trigger || !overlay || !closeButton) return;

  const close = () => {
    overlay.classList.remove('show');
    overlay.style.display = 'none';
    overlay.setAttribute('aria-hidden', 'true');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.focus();
  };
  trigger.addEventListener('click', () => {
    for (const id of ['history-popup-overlay', 'popup-overlay']) {
      const other = document.getElementById(id);
      if (other) {
        other.classList.remove('show');
        other.style.display = 'none';
      }
    }
    onOpen();
    overlay.style.display = 'flex';
    overlay.classList.add('show');
    overlay.setAttribute('aria-hidden', 'false');
    trigger.setAttribute('aria-expanded', 'true');
    (document.getElementById('alert-email') || closeButton).focus();
  });
  closeButton.addEventListener('click', close);
  overlay.addEventListener('click', (event) => {
    if (event.target === overlay) close();
  });
  document.addEventListener('keydown', (event) => {
    if (!overlay.classList.contains('show')) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === 'Tab') {
      const items = [...overlay.querySelectorAll('button, input, select, textarea, a[href], [tabindex]')]
        .filter((element) => !element.disabled && element.tabIndex >= 0);
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && (document.activeElement === first || !overlay.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !overlay.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    }
  });
}
