/* Talking to ARC.
 *
 * The base URL is configurable and remembered, because the same static bundle has to
 * work in two places: served by `web/serve.py`, where `/api` is proxied to a local ARC
 * and everything is same-origin; and hosted somewhere like GitHub Pages, where there is
 * no backend at all and the page has to be pointed at an ARC it can reach.
 *
 * `/chat/stream` is server-sent events over POST, so `EventSource` is no use — it only
 * does GET. The stream is read off `fetch` and parsed here.
 */

const STORE_KEY = 'arc.apiBase';
export const DEFAULT_BASE = '/api';

/** The API base in effect, without a trailing slash. */
export function base() {
  const saved = localStorage.getItem(STORE_KEY);
  return (saved || DEFAULT_BASE).replace(/\/+$/, '');
}

export function setBase(value) {
  const clean = (value || '').trim().replace(/\/+$/, '');
  if (clean) localStorage.setItem(STORE_KEY, clean);
  else localStorage.removeItem(STORE_KEY);
}

/** True when the page is https but the API is not — the browser will block it. */
export function isMixedContent(value = base()) {
  return location.protocol === 'https:' && /^http:\/\//i.test(value);
}

async function json(path, options = {}) {
  const response = await fetch(base() + path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
}

export const health = () => json('/health');

export const searchMemory = (query, limit = 20) =>
  json(`/memory/search?q=${encodeURIComponent(query)}&limit=${limit}`);

export const addMemory = (text) =>
  json('/memory/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });

/**
 * Stream one conversational turn.
 *
 * `speak: false` matters: ARC's default is to synthesise speech as it generates, and
 * this build has no voice. Leaving it out would make the machine talk to an empty room.
 *
 * @returns {Promise<{reply: string, finish_reason: string}>}
 */
export async function streamChat({ message, sessionId, signal, onToken, onState }) {
  const response = await fetch(base() + '/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ message, session_id: sessionId, speak: false }),
    signal,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let reply = '';
  let finish = 'stop';
  let complete = false;

  // The turn ends at the `done` *event*, not at end-of-stream: ARC holds the connection
  // open after the last token (it answers with `Connection: keep-alive` and never closes
  // the socket), so waiting for EOF here would wait forever. ARC's own UI does not
  // notice because it goes idle on the `state` event and simply leaves the read loop
  // running; this client needs the reply, so it has to stop deliberately.
  while (!complete) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

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
        reply += payload.text;
        onToken?.(payload.text);
      } else if (event === 'state') {
        onState?.(payload.activity);
      } else if (event === 'error') {
        throw new Error(payload.error || 'stream failed');
      } else if (event === 'done') {
        finish = payload.finish_reason || finish;
        // `done` carries the assembled reply. Preferred over the accumulated tokens so
        // a dropped frame cannot silently truncate what gets shown.
        if (payload.reply) reply = payload.reply;
        complete = true;
        break;
      }
    }
  }

  // Let go of the socket. Without this the connection stays open for the life of the
  // page and each turn leaks another one.
  await reader.cancel().catch(() => {});

  return { reply, finish_reason: finish };
}
