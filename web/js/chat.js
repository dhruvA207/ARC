/* The conversation view. */

import { streamChat } from './api.js';
import { render } from './markdown.js';
import * as store from './store.js';

const transcript = document.getElementById('transcript');
const form = document.getElementById('composer');
const input = document.getElementById('input');
const send = document.getElementById('send');
const stop = document.getElementById('stop');
const jump = document.getElementById('jump');

/** How many prior turns travel in the system prompt. Enough for a conversation to hold
 *  together, short enough not to crowd out recalled memories at a 4B context. */
const CONTEXT_TURNS = 12;

/**
 * The system prompt, sent because supplying `system` replaces ARC's own default.
 *
 * The identity line is deliberately plain — no claim about where ARC runs, and no pitch
 * about what makes it special. The deployment story is expected to change, and anything
 * more specific here is something that will need unwriting later.
 *
 * The memory handling below is not framing, it is mechanics: recalled memories really do
 * get injected into this prompt, so the model has to be told what they are.
 *
 * The provenance sentence is load-bearing, not decoration: memories arrive rendered with
 * markers like `[episodic, 2026-07-30]`, and without the instruction the model treats
 * them as a style to copy — it once answered "pong [episodic, 2026-07-30]", which was
 * then stored and recalled, compounding each turn. Dropping this line reintroduces that.
 */
const ARC_SYSTEM =
  'You are ARC, Dhruv\'s assistant. Use any memories below naturally; never copy their ' +
  'bracketed provenance markers into your reply, and cite a source URL when you rely ' +
  'on one. Be concise.';

let current = null;
let inFlight = null;
let listeners = [];

/** Notify the sidebar that the thread list changed. */
export function onChange(fn) {
  listeners.push(fn);
}
function changed() {
  for (const fn of listeners) fn();
}

/** Build the system prompt: ARC's guidance, then the conversation so far. */
function buildSystem(conversation) {
  const turns = conversation.turns.slice(-CONTEXT_TURNS);
  if (!turns.length) return ARC_SYSTEM;

  const lines = turns.map((t) => `${t.role === 'user' ? 'User' : 'ARC'}: ${t.content}`);
  return (
    `${ARC_SYSTEM}\n\n` +
    'Conversation so far, for continuity. Do not repeat it back or quote it verbatim; ' +
    'just continue naturally.\n\n' +
    lines.join('\n')
  );
}

// ── rendering ───────────────────────────────────────────────────────────

function actionButton(label, title, handler) {
  const button = document.createElement('button');
  button.className = 'act';
  button.type = 'button';
  button.title = title;
  button.textContent = label;
  button.addEventListener('click', handler);
  return button;
}

function addTurn(role, text = '') {
  const turn = document.createElement('div');
  turn.className = `turn ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'user' ? 'You' : 'A';
  avatar.setAttribute('aria-hidden', 'true');

  const column = document.createElement('div');
  column.className = 'column';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  render(bubble, text);
  column.append(bubble);

  turn.append(avatar, column);
  transcript.append(turn);
  scroll();
  return { turn, bubble, column };
}

/** Copy / regenerate, added once a reply is complete. */
function addActions(column, bubble, { regenerate = false } = {}) {
  column.querySelector('.actions')?.remove();

  const actions = document.createElement('div');
  actions.className = 'actions';

  actions.append(
    actionButton('Copy', 'Copy this reply', async (event) => {
      await navigator.clipboard.writeText(bubble.dataset.raw || bubble.textContent);
      const button = event.currentTarget;
      button.textContent = 'Copied';
      setTimeout(() => (button.textContent = 'Copy'), 1200);
    }),
  );
  if (regenerate) {
    actions.append(actionButton('Retry', 'Generate this reply again', () => regenerateLast()));
  }
  column.append(actions);
}

function showEmpty() {
  transcript.replaceChildren();

  const blank = document.createElement('div');
  blank.className = 'empty';

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 32 32');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', 'empty-mark');
  const ring = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  for (const [key, value] of Object.entries({
    cx: '16', cy: '16', r: '11', fill: 'none', stroke: 'currentColor',
    'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-dasharray': '52 70',
    transform: 'rotate(-45 16 16)',
  })) ring.setAttribute(key, value);
  svg.append(ring);

  const heading = document.createElement('h1');
  heading.textContent = 'ARC';
  const line = document.createElement('p');
  line.textContent = 'How can I help?';

  blank.append(svg, heading, line);
  transcript.append(blank);
}

/** Swap a user message for a textarea, and fork the conversation when saved. */
function startEditing(column, bubble, index) {
  if (inFlight) return;
  const original = bubble.dataset.raw ?? bubble.textContent;

  const editor = document.createElement('div');
  editor.className = 'editor';

  const area = document.createElement('textarea');
  area.value = original;
  area.rows = Math.min(12, original.split('\n').length + 1);

  const row = document.createElement('div');
  row.className = 'editor-row';

  const save = document.createElement('button');
  save.className = 'primary small';
  save.type = 'button';
  save.textContent = 'Save & submit';

  const cancel = document.createElement('button');
  cancel.className = 'act';
  cancel.type = 'button';
  cancel.textContent = 'Cancel';

  row.append(cancel, save);
  editor.append(area, row);

  bubble.hidden = true;
  column.querySelector('.actions')?.setAttribute('hidden', '');
  column.append(editor);
  area.focus();
  area.setSelectionRange(area.value.length, area.value.length);

  const close = () => {
    editor.remove();
    bubble.hidden = false;
    column.querySelector('.actions')?.removeAttribute('hidden');
  };

  cancel.addEventListener('click', close);
  save.addEventListener('click', async () => {
    const text = area.value.trim();
    if (!text || text === original) {
      close();
      return;
    }
    current = store.editTurn(current.id, index, text);
    paint();
    changed();

    const { bubble: reply, column: replyColumn } = addTurn('arc');
    await generate(replyColumn, reply);
  });

  area.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') close();
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) save.click();
  });
}

/** ‹ 2/3 › for a turn that has been edited, letting you flip between the paths. */
function addVersionSwitcher(column, index) {
  const info = store.versionInfo(current.id, index);
  if (!info) return;

  const nav = document.createElement('div');
  nav.className = 'versions';

  const step = (delta, label) => {
    const button = document.createElement('button');
    button.className = 'version-step';
    button.type = 'button';
    button.textContent = label;
    button.disabled = info.active + delta < 0 || info.active + delta >= info.count;
    button.addEventListener('click', () => {
      current = store.useVersion(current.id, index, info.active + delta);
      paint();
      changed();
    });
    return button;
  };

  const count = document.createElement('span');
  count.className = 'version-count';
  count.textContent = `${info.active + 1}/${info.count}`;

  nav.append(step(-1, '‹'), count, step(1, '›'));
  column.append(nav);
}

/** Draw a whole conversation from the store. */
function paint() {
  if (!current || !current.turns.length) {
    showEmpty();
    return;
  }
  transcript.replaceChildren();
  current.turns.forEach((turn, index) => {
    const role = turn.role === 'user' ? 'user' : 'arc';
    const { bubble, column } = addTurn(role, turn.content);
    bubble.dataset.raw = turn.content;

    if (role === 'user') {
      const actions = document.createElement('div');
      actions.className = 'actions';
      actions.append(
        actionButton('Edit', 'Edit this message and branch from here', () =>
          startEditing(column, bubble, index),
        ),
      );
      column.append(actions);
      addVersionSwitcher(column, index);
    } else {
      addActions(column, bubble, { regenerate: index === current.turns.length - 1 });
    }
  });
  scroll();
}

// ── scrolling ───────────────────────────────────────────────────────────

function atBottom() {
  return transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
}

let pinned = true;

function scroll(force = false) {
  if (force || pinned) transcript.scrollTop = transcript.scrollHeight;
}

transcript.addEventListener('scroll', () => {
  pinned = atBottom();
  jump.hidden = pinned;
});

jump.addEventListener('click', () => {
  pinned = true;
  jump.hidden = true;
  scroll(true);
});

// ── generating ──────────────────────────────────────────────────────────

function setBusy(busy) {
  send.hidden = busy;
  stop.hidden = !busy;
  input.disabled = false; // you can type the next message while one is generating
}

async function generate(column, bubble) {
  const controller = new AbortController();
  inFlight = controller;
  setBusy(true);

  const caret = document.createElement('span');
  caret.className = 'caret';
  bubble.append(caret);

  let text = '';
  let stopped = false;

  try {
    await streamChat({
      message: current.turns[current.turns.length - 1].content,
      sessionId: current.id,
      system: buildSystem({ turns: current.turns.slice(0, -1) }),
      signal: controller.signal,
      onToken(chunk) {
        text += chunk;
        render(bubble, text);
        bubble.append(caret);
        scroll();
      },
    });
  } catch (error) {
    if (error.name === 'AbortError') {
      stopped = true;
    } else {
      caret.remove();
      column.closest('.turn').classList.add('error');
      render(bubble, String(error.message || error));
      store.addTurn(current.id, 'assistant', `⚠ ${error.message || error}`);
      setBusy(false);
      inFlight = null;
      changed();
      return;
    }
  }

  caret.remove();
  const final = text || (stopped ? '(stopped)' : '(no reply)');
  render(bubble, final);
  bubble.dataset.raw = final;

  store.addTurn(current.id, 'assistant', final);
  addActions(column, bubble, { regenerate: true });

  setBusy(false);
  inFlight = null;
  input.focus();
  scroll();
  changed();
}

async function submit(event) {
  event?.preventDefault();
  const message = input.value.trim();
  if (!message || inFlight) return;

  if (!current) current = store.create();

  store.addTurn(current.id, 'user', message);
  input.value = '';
  autosize();
  // Repaint rather than append, so the new message arrives with its Edit control and any
  // version switcher already attached — one code path for drawing a turn, not two.
  paint();
  changed();

  const { bubble, column } = addTurn('arc');
  await generate(column, bubble);
}

/** Re-run the last user message, discarding the reply it produced. */
async function regenerateLast() {
  if (!current || inFlight) return;
  const turns = current.turns;
  if (!turns.length || turns[turns.length - 1].role !== 'assistant') return;

  store.dropLastReply(current.id);
  transcript.lastElementChild?.remove();

  const { bubble, column } = addTurn('arc');
  await generate(column, bubble);
}

function autosize() {
  input.style.height = 'auto';
  input.style.height = `${input.scrollHeight}px`;
}

// ── public ──────────────────────────────────────────────────────────────

export function open(cid) {
  inFlight?.abort();
  current = store.get(cid);
  pinned = true;
  jump.hidden = true;
  paint();
  input.focus();
  return current;
}

export function startNew() {
  inFlight?.abort();
  current = store.create();
  pinned = true;
  jump.hidden = true;
  showEmpty();
  input.focus();
  changed();
  return current;
}

export function currentId() {
  return current?.id ?? null;
}

export function focus() {
  input.focus();
}

/** Restore the most recent thread, or start a fresh one. */
export function restore() {
  const [latest] = store.list();
  current = latest || store.create();
  paint();
}

form.addEventListener('submit', submit);
input.addEventListener('input', autosize);
input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) submit(event);
});

stop.addEventListener('click', () => {
  inFlight?.abort();
});
