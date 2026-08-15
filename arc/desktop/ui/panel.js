/* Wires the panel: the orb, the native bridge, and one turn of conversation.
 *
 * The native side drives state — it knows when the window is moving and where to — so
 * this file never decides its own geometry. It reacts, and it asks the native side to
 * move when the conversation implies it should.
 */

import { Orb } from './orb.js';

const canvas = document.getElementById('orb');
const form = document.getElementById('composer');
const input = document.getElementById('input');
const reply = document.getElementById('reply');
const status = document.getElementById('status');

const orb = new Orb(canvas);
orb.start();

/** Conversations are shared with every other front end, so this is a real id in ARC's
 *  store rather than a private one. `origin` is what the web UI reads to show which
 *  threads came from the desktop. */
let conversationId = `d-${Date.now().toString(36)}`;
let turns = [];
let inFlight = null;

// ── native bridge ───────────────────────────────────────────────────────
//
// `window.arcDesktop` is called from Python via evaluateJavaScript. Assigned before
// anything can await, so a state change arriving during load is never dropped.

window.arcDesktop = {
  setState(state) {
    // The panel reports its destination; the orb plays the transition into it.
    document.body.dataset.state = state;
    orb.setState(state === 'centre' ? 'arriving' : 'leaving');
    if (state === 'centre') setTimeout(() => input.focus(), 60);
  },
  setActivity(activity) {
    orb.setActivity(activity);
    if (activity === 'MUTED') status.textContent = 'muted';
    else if (activity === 'IDLE') status.textContent = '';
  },
};

document.body.dataset.state = 'corner';

// ── talking to ARC ──────────────────────────────────────────────────────

async function persist() {
  // Fire-and-forget: a failed sync must not cost you the reply on screen.
  try {
    await fetch('/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: conversationId,
        title: turns.find((t) => t.role === 'user')?.content.slice(0, 60) || 'Desktop',
        turns,
        origin: 'desktop',
        updated: Date.now(),
      }),
    });
  } catch {
    status.textContent = 'not synced';
  }
}

async function ask(message) {
  turns.push({ role: 'user', content: message });
  reply.textContent = '';
  status.textContent = 'thinking…';
  orb.setActivity('THINKING');

  const controller = new AbortController();
  inFlight = controller;

  let text = '';
  try {
    const response = await fetch('/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      // speak:false — this build has no voice, and ARC synthesises by default.
      body: JSON.stringify({ message, session_id: conversationId, speak: false }),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const readerStream = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let done = false;

    while (!done) {
      const chunk = await readerStream.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });

      let split;
      while ((split = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);

        let event = 'message';
        const data = [];
        for (const line of frame.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          else if (line.startsWith('data:')) data.push(line.slice(5).trim());
        }
        if (!data.length) continue;

        let payload;
        try {
          payload = JSON.parse(data.join('\n'));
        } catch {
          continue;
        }

        if (event === 'token' && payload.text) {
          text += payload.text;
          reply.textContent = text;
          reply.scrollTop = reply.scrollHeight;
        } else if (event === 'done') {
          if (payload.reply) text = payload.reply;
          done = true;
          // ARC holds the connection open after `done`, so waiting for EOF waits forever.
          break;
        } else if (event === 'error') {
          throw new Error(payload.error || 'stream failed');
        }
      }
    }
    await readerStream.cancel().catch(() => {});
  } catch (error) {
    if (error.name !== 'AbortError') {
      reply.textContent = String(error.message || error);
      status.textContent = 'error';
      orb.setActivity('IDLE');
      inFlight = null;
      return;
    }
  }

  reply.textContent = text || '(no reply)';
  turns.push({ role: 'assistant', content: text });
  status.textContent = '';
  orb.setActivity('IDLE');
  inFlight = null;
  persist();
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || inFlight) return;
  input.value = '';
  ask(message);
});

input.addEventListener('keydown', (event) => {
  // Escape parks the panel without touching the conversation — the same thing a second
  // double-tap of ⌘ does, for when your hands are already on the keyboard.
  if (event.key === 'Escape') {
    inFlight?.abort();
    window.webkit?.messageHandlers?.arc?.postMessage?.({ action: 'park' });
  }
});
