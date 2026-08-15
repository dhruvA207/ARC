/* The memory view — what ARC remembers, searched the way ARC searches it.
 *
 * Search goes through `/memory/search`, which is the hybrid retriever, not a LIKE query.
 * Results therefore come back ranked with the provenance ARC used to rank them, and that
 * provenance is shown: "why did this come back?" is the first question when retrieval
 * misbehaves, and hiding the answer helps nobody.
 */

import { searchMemory, addMemory } from './api.js';

const form = document.getElementById('memory-search');
const query = document.getElementById('memory-q');
const results = document.getElementById('memory-results');
const addForm = document.getElementById('memory-add');
const addText = document.getElementById('memory-text');

function note(text, bad = false) {
  results.replaceChildren();
  const p = document.createElement('p');
  p.className = bad ? 'note bad' : 'note';
  p.textContent = text;
  results.append(p);
}

function tag(text, className = 'tag') {
  const span = document.createElement('span');
  span.className = className;
  span.textContent = text;
  return span;
}

/** Trim an ISO timestamp to the date. The clock time is noise in a list. */
function day(value) {
  return typeof value === 'string' ? value.slice(0, 10) : '';
}

function card(record) {
  const item = document.createElement('article');
  item.className = 'memory';

  const content = document.createElement('p');
  content.className = 'memory-content';
  content.textContent = record.content ?? '';
  item.append(content);

  const meta = document.createElement('div');
  meta.className = 'memory-meta';

  if (record.layer) meta.append(tag(record.layer, 'tag layer'));
  if (record.kind) meta.append(tag(record.kind));
  const occurred = day(record.occurred_at || record.created_at);
  if (occurred) meta.append(tag(occurred));
  if (typeof record.score === 'number') meta.append(tag(`score ${record.score}`));
  if (record.source) meta.append(tag(record.source));

  // Which retrieval strategies found it, and at what rank.
  const sources = record.sources && Object.keys(record.sources);
  if (sources?.length) meta.append(tag(`via ${sources.join(', ')}`));

  item.append(meta);
  return item;
}

async function run(event) {
  event?.preventDefault();
  const text = query.value.trim();
  if (!text) {
    note('Type something to search for.');
    return;
  }

  note('Searching…');
  try {
    const { results: hits } = await searchMemory(text);
    if (!hits?.length) {
      note(`Nothing remembered about “${text}”.`);
      return;
    }
    results.replaceChildren(...hits.map(card));
  } catch (error) {
    note(String(error.message || error), true);
  }
}

async function add(event) {
  event.preventDefault();
  const text = addText.value.trim();
  if (!text) return;

  const button = addForm.querySelector('button');
  button.disabled = true;
  try {
    await addMemory(text);
    addText.value = '';
    query.value = text;
    await run();
  } catch (error) {
    note(String(error.message || error), true);
  } finally {
    button.disabled = false;
  }
}

export function focus() {
  query.focus();
  if (!results.childElementCount) {
    note('Search ARC’s memory, or add something to it.');
  }
}

form.addEventListener('submit', run);
addForm.addEventListener('submit', add);
