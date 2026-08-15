/* Bootstrap: view switching, connection status, and the API base dialog. */

import { health, base, setBase, DEFAULT_BASE, isMixedContent } from './api.js';
import * as chat from './chat.js';
import * as memory from './memory.js';

const views = document.querySelectorAll('.view');
const navItems = document.querySelectorAll('.nav-item');
const status = document.getElementById('status');
const statusText = status.querySelector('.status-text');

const dialog = document.getElementById('settings');
const apiInput = document.getElementById('api-base');
const apiHint = document.getElementById('api-hint');

const focusers = { chat: chat.focus, memory: memory.focus };

function show(name) {
  for (const view of views) view.classList.toggle('is-active', view.dataset.view === name);
  for (const item of navItems) {
    const active = item.dataset.view === name;
    item.classList.toggle('is-active', active);
    if (active) item.setAttribute('aria-current', 'page');
    else item.removeAttribute('aria-current');
  }
  location.hash = name;
  focusers[name]?.();
}

function setStatus(state, text) {
  status.dataset.state = state;
  statusText.textContent = text;
  status.title = text;
}

/** Poll ARC so the rail reflects reality rather than the last thing that happened. */
async function poll() {
  if (isMixedContent()) {
    setStatus('down', 'blocked: https → http');
    return;
  }
  try {
    const info = await health();
    setStatus('ok', `ARC ${info.version}`);
  } catch {
    setStatus('down', 'ARC offline');
  }
}

// ── connection dialog ───────────────────────────────────────────────────

function openSettings() {
  apiInput.value = base();
  updateHint();
  dialog.showModal();
}

function updateHint() {
  const value = apiInput.value.trim() || DEFAULT_BASE;
  if (isMixedContent(value)) {
    apiHint.textContent =
      'This page is served over https, so the browser will block a plain http API.';
  } else if (value === DEFAULT_BASE) {
    apiHint.textContent = 'Same-origin — the dev server proxies this to a local ARC.';
  } else {
    apiHint.textContent = '';
  }
}

document.getElementById('settings-open').addEventListener('click', openSettings);
apiInput.addEventListener('input', updateHint);

dialog.addEventListener('close', () => {
  if (dialog.returnValue === 'save') {
    setBase(apiInput.value);
    poll();
  }
});

// ── wiring ──────────────────────────────────────────────────────────────

for (const item of navItems) {
  item.addEventListener('click', () => show(item.dataset.view));
}

document.getElementById('new-chat').addEventListener('click', () => {
  chat.reset();
  show('chat');
});

window.addEventListener('hashchange', () => {
  const name = location.hash.slice(1);
  if (focusers[name]) show(name);
});

const initial = location.hash.slice(1);
show(focusers[initial] ? initial : 'chat');

poll();
setInterval(poll, 10_000);
