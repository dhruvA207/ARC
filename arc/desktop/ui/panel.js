/* Wires the panel: the orb, the native bridge, and one turn of conversation.
 *
 * **Voice only.** A typing mode was built and removed: the panel is a non-activating
 * floating panel, which is what lets it appear over your editor without stealing focus,
 * and the same property means its text field can never reliably take the keyboard. A
 * composer you cannot click into is worse than no composer. Use the web UI to type.
 *
 * The native side drives geometry and mute; it never talks to the API itself. Everything
 * that reaches ARC goes through this file, so there is one place where a request is made
 * and one place where a failure is reported.
 */

import { Orb } from './orb.js';

const canvas = document.getElementById('orb');
const reply = document.getElementById('reply');
const status = document.getElementById('status');
const heard = document.getElementById('heard');

const orb = new Orb(canvas);
orb.start();

/** Conversations are shared with every other front end, so this is a real id in ARC's
 *  store rather than a private one. `origin` is what the web UI reads to show which
 *  threads came from the computer. */
const conversationId = `d-${Date.now().toString(36)}`;
const turns = [];
let inFlight = null;

let listening = false;
let muted = false;
/** Tools running right now: call id -> category. One satellite each. */
const activeTools = new Map();
/** Live mode answers and speaks by itself, so the transcript must NOT be posted to
 *  /chat/stream — doing so runs a second, local reply on top of the one already being
 *  spoken. Read from /voice/status at startup. */
let answersItself = false;

// ── native bridge ───────────────────────────────────────────────────────
//
// Assigned before anything can await, so a call arriving during load is never dropped.

window.arcDesktop = {
  setState(state) {
    document.body.dataset.state = state;
    orb.setState(state === 'centre' ? 'arriving' : 'leaving');

    // Summoning opens the microphone: being summoned *is* the cue to start talking.
    // Parking closes it — nothing listens to you while ARC is out of the way.
    if (state === 'centre' && !muted) setMic(true);
    else if (state !== 'centre') setMic(false);
  },

  setActivity(activity) {
    orb.setActivity(activity);
    if (activity === 'IDLE') status.textContent = '';
  },

  setMuted(next) {
    muted = Boolean(next);
    if (muted) setMic(false);
    else if (document.body.dataset.state === 'centre') setMic(true);
    // Mute is its own axis on the orb, not an activity. Folding it into setActivity
    // meant every SPEAKING/IDLE event from live mode silently un-muted it.
    orb.setMuted(muted);
    status.textContent = muted ? 'muted' : '';
  },
};

document.body.dataset.state = 'corner';

// ── microphone ──────────────────────────────────────────────────────────

/** Open or close the microphone, but only when it is not already in that state.
 *
 *  /voice/toggle is a *toggle*, so asking for a state it is already in would flip it to
 *  the opposite one — the failure that makes a mic appear to close the moment it opens. */
async function setMic(want) {
  if (want === listening) return;
  try {
    const response = await fetch('/voice/toggle', { method: 'POST' });
    const body = await response.json();
    if (body.error) {
      status.textContent = body.error;
      return;
    }
    listening = Boolean(body.listening);
    if (!listening) orb.setLevel(0);
    status.textContent = '';
  } catch {
    status.textContent = 'voice unavailable';
  }
}

/** The event stream: microphone level, transcripts, and mic state from elsewhere. */
function openEvents() {
  const events = new EventSource('/events');

  // ~30 Hz amplitude. This is what makes the orb move while you are talking; without it
  // the cloud just spins at a constant rate and gives no sign of being heard.
  events.addEventListener('level', (message) => {
    orb.setLevel(JSON.parse(message.data).level);
  });

  events.addEventListener('transcript', (message) => {
    const payload = JSON.parse(message.data);
    const text = (payload.text || '').trim();

    // Live mode carries both sides of the conversation on this one channel, so the role
    // decides where the text belongs. Without it ARC's own reply is displayed back as
    // though you had said it.
    if (payload.role === 'assistant') {
      reply.textContent = payload.text;
      reply.scrollTop = reply.scrollHeight;
      orb.setActivity(payload.final ? 'IDLE' : 'THINKING');
      if (payload.final && text) {
        turns.push({ role: 'assistant', content: text });
        persist();
      }
      return;
    }

    heard.textContent = payload.text;
    if (!payload.final || !text) return;

    heard.textContent = '';
    if (answersItself) {
      // Gemini is already answering out loud; posting the transcript would start a
      // second, local reply on top of the one being spoken. Record the turn so the
      // conversation still syncs.
      turns.push({ role: 'user', content: text });
      persist();
    } else {
      ask(text);
    }
  });

  events.addEventListener('voice', (message) => {
    // The mic can be flipped from the menu bar or another front end; without this the
    // page and the server disagree, and each keeps toggling the other back.
    listening = Boolean(JSON.parse(message.data).listening);
    if (!listening) orb.setLevel(0);
    if (!inFlight) status.textContent = '';
  });

  events.addEventListener('state', (message) => {
    const activity = JSON.parse(message.data).activity;
    if (activity) orb.setActivity(activity);
  });

  // One satellite per tool actually in flight, coloured by what kind of work it is.
  events.addEventListener('tool_start', (message) => {
    const payload = JSON.parse(message.data);
    activeTools.set(String(payload.call_id), payload.category || 'general');
    orb.setTools([...activeTools.values()]);
    status.textContent = payload.name ? `${payload.name}…` : 'working…';
  });

  events.addEventListener('tool_end', (message) => {
    const payload = JSON.parse(message.data);
    activeTools.delete(String(payload.call_id));
    orb.setTools([...activeTools.values()]);
    if (!activeTools.size && !inFlight) status.textContent = '';
  });

  // EventSource reconnects on its own; nothing to do but let it.
  events.addEventListener('error', () => {});
}

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
  if (inFlight) return;

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
      // Spoken, because this panel has no other way to answer you.
      body: JSON.stringify({ message, session_id: conversationId, speak: true }),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const stream = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let complete = false;

    while (!complete) {
      const chunk = await stream.read();
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
          complete = true;
          // ARC holds the connection open after `done`, so waiting for EOF waits forever.
          break;
        } else if (event === 'error') {
          throw new Error(payload.error || 'stream failed');
        }
      }
    }
    await stream.cancel().catch(() => {});
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
  orb.setActivity('IDLE');
  inFlight = null;
  status.textContent = '';
  persist();
}

// ── start ───────────────────────────────────────────────────────────────

(async () => {
  try {
    const info = await (await fetch('/voice/status')).json();
    answersItself = Boolean(info.answers_itself);
    listening = Boolean(info.listening);
    if (!info.available) {
      // Voice is the only way in, so a machine without speech support has to say so
      // rather than silently ignoring every summon.
      status.textContent = 'voice unavailable';
    }
  } catch {
    /* the status call is advisory; the panel still works without it */
  }
  openEvents();
})();
