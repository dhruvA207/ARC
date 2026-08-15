/* Conversations, kept in the browser.
 *
 * ARC remembers *facts* — every turn is written to its memory and is searchable — but it
 * has no notion of a thread you can reopen. `/chat/stream` takes one message and returns
 * one reply; there is no conversation resource to list or fetch.
 *
 * So threads live here, in localStorage. That is genuinely where they belong for now:
 * the site is static and may end up hosted with no backend of its own, and this keeps
 * conversations working in both cases without touching ARC.
 *
 * The trade-off, stated plainly: threads are per-browser. Clearing site data loses them.
 * The *content* is still in ARC's memory and searchable from the Memory view — what is
 * lost is the grouping into named conversations.
 */

const KEY = 'arc.conversations';
const LIMIT = 200; // turns kept per thread, oldest dropped first

let cache = null;

function load() {
  if (cache) return cache;
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '[]');
    cache = Array.isArray(raw) ? raw : [];
  } catch {
    cache = [];
  }
  return cache;
}

function persist() {
  try {
    localStorage.setItem(KEY, JSON.stringify(cache));
  } catch {
    // Quota exceeded, or storage disabled (private windows). The conversation still
    // works for this session; it just will not survive a reload.
  }
}

function id() {
  return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
}

/** Newest first. */
export function list() {
  return [...load()].sort((a, b) => b.updated - a.updated);
}

export function get(cid) {
  return load().find((c) => c.id === cid) || null;
}

export function create() {
  const conversation = { id: id(), title: 'New conversation', turns: [], updated: Date.now() };
  load().unshift(conversation);
  persist();
  return conversation;
}

export function remove(cid) {
  cache = load().filter((c) => c.id !== cid);
  persist();
}

export function rename(cid, title) {
  const conversation = get(cid);
  if (!conversation) return;
  conversation.title = title.trim().slice(0, 80) || 'Untitled';
  conversation.updated = Date.now();
  persist();
}

/** Append a turn, titling the thread from the first thing the user said. */
export function addTurn(cid, role, content) {
  const conversation = get(cid);
  if (!conversation) return;

  conversation.turns.push({ role, content, at: Date.now() });
  if (conversation.turns.length > LIMIT) conversation.turns.splice(0, conversation.turns.length - LIMIT);

  if (role === 'user' && conversation.turns.filter((t) => t.role === 'user').length === 1) {
    conversation.title = content.trim().split('\n')[0].slice(0, 60) || 'New conversation';
  }
  conversation.updated = Date.now();
  persist();
}

/** Replace the last assistant turn — used by regenerate, and to finalise a stream. */
export function replaceLastReply(cid, content) {
  const conversation = get(cid);
  if (!conversation) return;
  for (let i = conversation.turns.length - 1; i >= 0; i -= 1) {
    if (conversation.turns[i].role === 'assistant') {
      conversation.turns[i].content = content;
      conversation.updated = Date.now();
      persist();
      return;
    }
  }
}

/** Drop the trailing assistant turn, so a regenerate does not stack replies. */
export function dropLastReply(cid) {
  const conversation = get(cid);
  if (!conversation) return;
  const last = conversation.turns[conversation.turns.length - 1];
  if (last?.role === 'assistant') {
    conversation.turns.pop();
    persist();
  }
}

// ── branching ───────────────────────────────────────────────────────────
//
// Editing a message forks the conversation rather than overwriting it: the reply you
// already had is worth keeping, because the point of editing is usually to compare.
//
// A fork is stored as the *tail* of the conversation from the edited turn onward.
// `versions[i] = { list: [tail, tail, ...], active: n }`, and the visible conversation is
// always `turns`. Head + active tail is the invariant; `turns` is what everything else
// reads, so nothing outside this section needs to know forks exist.

function tailStore(conversation, index) {
  conversation.versions ??= {};
  conversation.versions[index] ??= { list: [conversation.turns.slice(index)], active: 0 };
  return conversation.versions[index];
}

/**
 * Replace the user message at `index` with `content`, keeping the old path as a version.
 *
 * Returns the new turn count so the caller can regenerate from here.
 */
export function editTurn(cid, index, content) {
  const conversation = get(cid);
  if (!conversation) return null;

  const entry = tailStore(conversation, index);
  // Save whatever is on screen into the slot it came from before forking away from it.
  entry.list[entry.active] = conversation.turns.slice(index);

  const tail = [{ role: 'user', content, at: Date.now() }];
  entry.list.push(tail);
  entry.active = entry.list.length - 1;

  // Forks recorded further down the old tail describe turns that no longer exist.
  for (const key of Object.keys(conversation.versions)) {
    if (Number(key) > index) delete conversation.versions[key];
  }

  conversation.turns = conversation.turns.slice(0, index).concat(tail);
  conversation.updated = Date.now();
  persist();
  return conversation;
}

/** How many versions exist at `index`, and which is showing. */
export function versionInfo(cid, index) {
  const entry = get(cid)?.versions?.[index];
  if (!entry || entry.list.length < 2) return null;
  return { count: entry.list.length, active: entry.active };
}

/** Show version `n` of the fork at `index`. */
export function useVersion(cid, index, n) {
  const conversation = get(cid);
  const entry = conversation?.versions?.[index];
  if (!entry || n < 0 || n >= entry.list.length) return null;

  entry.list[entry.active] = conversation.turns.slice(index);
  entry.active = n;
  conversation.turns = conversation.turns.slice(0, index).concat(entry.list[n]);
  conversation.updated = Date.now();
  persist();
  return conversation;
}

// ── search and export ───────────────────────────────────────────────────

/** Conversations whose title or any turn matches, with the first matching snippet. */
export function search(query) {
  const needle = query.trim().toLowerCase();
  if (!needle) return list().map((c) => ({ conversation: c, snippet: null }));

  const found = [];
  for (const conversation of list()) {
    if (conversation.title.toLowerCase().includes(needle)) {
      found.push({ conversation, snippet: null });
      continue;
    }
    // Inactive branches are searched too. A message you edited away from is still
    // something you might go looking for, and it would otherwise be unreachable except
    // by remembering which turn you forked at.
    const everywhere = [...conversation.turns];
    for (const entry of Object.values(conversation.versions ?? {})) {
      for (const tail of entry.list) everywhere.push(...tail);
    }

    const hit = everywhere.find((t) => t.content.toLowerCase().includes(needle));
    if (hit) {
      const at = hit.content.toLowerCase().indexOf(needle);
      const from = Math.max(0, at - 30);
      found.push({
        conversation,
        snippet: (from ? '…' : '') + hit.content.slice(from, at + needle.length + 40).trim(),
      });
    }
  }
  return found;
}

/** One conversation as Markdown. */
export function toMarkdown(cid) {
  const conversation = get(cid);
  if (!conversation) return '';

  const when = new Date(conversation.updated).toISOString().slice(0, 10);
  const lines = [`# ${conversation.title}`, '', `_Exported from ARC on ${when}_`, ''];
  for (const turn of conversation.turns) {
    lines.push(`### ${turn.role === 'user' ? 'You' : 'ARC'}`, '', turn.content, '');
  }
  return lines.join('\n');
}

/** Everything, as JSON — the format `import` would read back. */
export function toJSON() {
  return JSON.stringify({ exported: new Date().toISOString(), conversations: load() }, null, 2);
}
