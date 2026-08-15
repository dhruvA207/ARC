/* Bootstrap: view switching, the conversation list, connection status, settings. */

import { health, base, setBase, DEFAULT_BASE, isMixedContent } from './api.js';
import * as chat from './chat.js';
import * as memory from './memory.js';
import * as store from './store.js';

const views = document.querySelectorAll('.view');
const navItems = document.querySelectorAll('.nav-item');
const status = document.getElementById('status');
const statusText = status.querySelector('.status-text');
const threads = document.getElementById('threads');
const threadSearch = document.getElementById('thread-search');

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

// ── conversation list ───────────────────────────────────────────────────

function iconButton(label, title, handler) {
  const button = document.createElement('button');
  button.className = 'thread-act';
  button.type = 'button';
  button.title = title;
  button.setAttribute('aria-label', title);
  button.textContent = label;
  button.addEventListener('click', (event) => {
    event.stopPropagation(); // do not also open the thread
    handler();
  });
  return button;
}

/** Offer `text` as a downloaded file. */
function download(filename, text, type = 'text/plain') {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  // Revoked on the next tick — immediately would race the download starting.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function slug(title) {
  return title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48);
}

function paintThreads() {
  const active = chat.currentId();
  threads.replaceChildren();

  const results = store.search(threadSearch.value);

  for (const { conversation, snippet } of results) {
    const item = document.createElement('li');
    item.className = 'thread' + (conversation.id === active ? ' is-active' : '');

    const openButton = document.createElement('button');
    openButton.className = 'thread-open';
    openButton.type = 'button';
    openButton.title = conversation.title;
    openButton.addEventListener('click', () => {
      chat.open(conversation.id);
      paintThreads();
      show('chat');
    });

    const name = document.createElement('span');
    name.className = 'thread-name';
    name.textContent = conversation.title;
    openButton.append(name);

    // Only when the match came from the body — otherwise the title already shows why.
    if (snippet) {
      const hint = document.createElement('span');
      hint.className = 'thread-snippet';
      hint.textContent = snippet;
      openButton.append(hint);
    }

    const tools = document.createElement('span');
    tools.className = 'thread-tools';
    tools.append(
      iconButton('⭳', 'Export as Markdown', () => {
        download(`${slug(conversation.title) || 'conversation'}.md`, store.toMarkdown(conversation.id), 'text/markdown');
      }),
      iconButton('✎', 'Rename conversation', () => {
        const title = prompt('Rename conversation', conversation.title);
        if (title !== null) {
          store.rename(conversation.id, title);
          paintThreads();
        }
      }),
      iconButton('×', 'Delete conversation', () => {
        if (!confirm(`Delete “${conversation.title}”?`)) return;
        store.remove(conversation.id);
        // Deleting the open thread leaves nothing shown, so fall back to the newest
        // remaining one, or a fresh empty conversation.
        if (conversation.id === chat.currentId()) {
          const [next] = store.list();
          if (next) chat.open(next.id);
          else chat.startNew();
        }
        paintThreads();
      }),
    );

    item.append(openButton, tools);
    threads.append(item);
  }

  if (!threads.childElementCount) {
    const empty = document.createElement('li');
    empty.className = 'thread-empty';
    empty.textContent = threadSearch.value.trim()
      ? 'No conversations match.'
      : 'No conversations yet.';
    threads.append(empty);
  }
}

// ── status ──────────────────────────────────────────────────────────────

function setStatus(state, text) {
  status.dataset.state = state;
  statusText.textContent = text;
  status.title = text;
}

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

document.getElementById('settings-open').addEventListener('click', () => {
  apiInput.value = base();
  updateHint();
  dialog.showModal();
});
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
  chat.startNew();
  threadSearch.value = ''; // a filtered list would hide the thread just created
  paintThreads();
  show('chat');
});

threadSearch.addEventListener('input', paintThreads);

document.getElementById('export-all').addEventListener('click', () => {
  const stamp = new Date().toISOString().slice(0, 10);
  download(`arc-conversations-${stamp}.json`, store.toJSON(), 'application/json');
});

window.addEventListener('hashchange', () => {
  const name = location.hash.slice(1);
  if (focusers[name]) show(name);
});

chat.onChange(paintThreads);
chat.restore();
paintThreads();

const initial = location.hash.slice(1);
show(focusers[initial] ? initial : 'chat');

poll();
setInterval(poll, 10_000);
