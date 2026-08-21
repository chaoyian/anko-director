async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {'Content-Type': 'application/json', ...(options.headers || {})},
    ...options,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { const body = await response.json(); message = body.detail || message; } catch (_) {}
    toast(message, true);
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}
function toast(message, error = false) {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.classList.toggle('error', error);
  element.hidden = false;
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => { element.hidden = true; }, 4200);
}
function loading(button, state) {
  if (!button) return;
  if (state) { button.dataset.label = button.textContent; button.textContent = '处理中…'; button.disabled = true; }
  else { button.textContent = button.dataset.label || button.textContent; button.disabled = false; }
}
