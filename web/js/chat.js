/* The conversation view. */

import { streamChat } from './api.js';

const transcript = document.getElementById('transcript');
const empty = document.getElementById('chat-empty');
const form = document.getElementById('composer');
const input = document.getElementById('input');
const send = document.getElementById('send');

/** One session per page load, so a reload starts a fresh thread in ARC's memory. */
let sessionId = newSession();
let inFlight = null;

function newSession() {
  return `web-${Date.now().toString(36)}`;
}

/** Minimal markdown: fenced code, inline code, paragraphs. Deliberately not a parser.
 *
 * Everything is inserted as text nodes rather than innerHTML — the model's output is
 * untrusted input like any other, and this page also renders stored memories. */
function render(target, text) {
  target.textContent = '';
  const blocks = text.split(/\n{2,}/);

  for (const block of blocks) {
    const fence = block.match(/^```(\w*)\n([\s\S]*?)```$/);
    if (fence) {
      const pre = document.createElement('pre');
      const code = document.createElement('code');
      code.textContent = fence[2];
      pre.append(code);
      target.append(pre);
      continue;
    }

    const p = document.createElement('p');
    // Inline code is the one span-level thing worth having: ARC answers a lot of
    // questions about paths and commands.
    const parts = block.split(/(`[^`\n]+`)/);
    for (const part of parts) {
      if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
        const code = document.createElement('code');
        code.textContent = part.slice(1, -1);
        p.append(code);
      } else if (part) {
        p.append(document.createTextNode(part));
      }
    }
    target.append(p);
  }
}

function addTurn(who, text = '') {
  empty?.remove();

  const turn = document.createElement('div');
  turn.className = `turn ${who}`;

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = who === 'user' ? 'You' : 'A';
  avatar.setAttribute('aria-hidden', 'true');

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  render(bubble, text);

  turn.append(avatar, bubble);
  transcript.append(turn);
  scroll();
  return bubble;
}

function scroll() {
  transcript.scrollTop = transcript.scrollHeight;
}

/** Grow the textarea with its content, up to the CSS max-height. */
function autosize() {
  input.style.height = 'auto';
  input.style.height = `${input.scrollHeight}px`;
}

async function submit(event) {
  event?.preventDefault();
  const message = input.value.trim();
  if (!message || inFlight) return;

  addTurn('user', message);
  input.value = '';
  autosize();
  send.disabled = true;

  const bubble = addTurn('arc');
  const caret = document.createElement('span');
  caret.className = 'caret';
  bubble.append(caret);

  let text = '';
  const controller = new AbortController();
  inFlight = controller;

  try {
    await streamChat({
      message,
      sessionId,
      signal: controller.signal,
      onToken(chunk) {
        text += chunk;
        render(bubble, text);
        bubble.append(caret);
        scroll();
      },
    });
    render(bubble, text || '(no reply)');
  } catch (error) {
    caret.remove();
    bubble.closest('.turn').classList.add('error');
    const failed = error.name === 'AbortError' ? 'Stopped.' : String(error.message || error);
    render(bubble, failed);
  } finally {
    caret.remove();
    inFlight = null;
    send.disabled = false;
    input.focus();
    scroll();
  }
}

export function reset() {
  inFlight?.abort();
  sessionId = newSession();
  transcript.replaceChildren();

  const blank = document.createElement('div');
  blank.className = 'empty';
  const heading = document.createElement('h1');
  heading.textContent = 'ARC';
  const line = document.createElement('p');
  line.textContent = 'Everything stays on your machine. Ask anything.';
  blank.append(heading, line);

  transcript.append(blank);
  input.focus();
}

export function focus() {
  input.focus();
}

form.addEventListener('submit', submit);
input.addEventListener('input', autosize);
input.addEventListener('keydown', (event) => {
  // Enter sends; Shift+Enter is a newline. IME composition must not be interrupted.
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    submit(event);
  }
});
