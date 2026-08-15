/* A small markdown renderer.
 *
 * Built rather than pulled in: the repo treats dependencies as a liability (§7), and the
 * subset a chat reply actually uses is short — headings, emphasis, code, lists, links,
 * quotes. This is not a CommonMark implementation and does not try to be.
 *
 * **Everything becomes DOM nodes, never HTML strings.** Model output is untrusted input,
 * and so are stored memories, which this also renders. There is no `innerHTML` anywhere
 * in the file and a test enforces that.
 */

import { highlight } from './highlight.js';

/** Inline: `code`, **bold**, *italic*, [text](url). Returns an array of nodes. */
function inline(text) {
  const nodes = [];
  // One pass, alternating between the token patterns and the plain text between them.
  const pattern = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(\[[^\]\n]+\]\([^)\s]+\))/g;
  let last = 0;
  let match;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(document.createTextNode(text.slice(last, match.index)));
    const token = match[0];

    if (token.startsWith('`')) {
      const code = document.createElement('code');
      code.textContent = token.slice(1, -1);
      nodes.push(code);
    } else if (token.startsWith('**')) {
      const strong = document.createElement('strong');
      strong.textContent = token.slice(2, -2);
      nodes.push(strong);
    } else if (token.startsWith('*')) {
      const em = document.createElement('em');
      em.textContent = token.slice(1, -1);
      nodes.push(em);
    } else {
      const [, label, href] = token.match(/\[([^\]]+)\]\(([^)\s]+)\)/);
      // Only http(s). A `javascript:` href in a model reply is exactly the kind of thing
      // that should never become a live link.
      if (/^https?:\/\//i.test(href)) {
        const link = document.createElement('a');
        link.href = href;
        link.textContent = label;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        nodes.push(link);
      } else {
        nodes.push(document.createTextNode(token));
      }
    }
    last = pattern.lastIndex;
  }

  if (last < text.length) nodes.push(document.createTextNode(text.slice(last)));
  return nodes;
}

function para(tag, text) {
  const el = document.createElement(tag);
  el.append(...inline(text));
  return el;
}

/** Render markdown into `target`, replacing whatever was there. */
export function render(target, source) {
  target.replaceChildren();
  const lines = String(source ?? '').split('\n');
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code. Held verbatim — no inline parsing inside.
    //
    // Leading whitespace is allowed because models indent fences inside lists, and the
    // list branch below rewrites `3. ```py` into a bare fence rather than rendering the
    // backticks as text — which is what a stricter `^```` produced in practice.
    const fence = line.match(/^\s*```(\w*)\s*$/);
    if (fence) {
      const body = [];
      i += 1;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) body.push(lines[i++]);
      i += 1; // closing fence, or end of input on an unterminated block

      const pre = document.createElement('pre');
      const code = document.createElement('code');
      if (fence[1]) code.dataset.lang = fence[1];
      highlight(code, body.join('\n'), fence[1]);
      pre.append(code);
      target.append(pre);
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      target.append(para(`h${heading[1].length + 1}`, heading[2]));
      i += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const body = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) body.push(lines[i++].replace(/^>\s?/, ''));
      const quote = document.createElement('blockquote');
      quote.append(para('p', body.join(' ')));
      target.append(quote);
      continue;
    }

    const MARKER = /^\s*([-*+]|\d+\.)\s+/;
    if (MARKER.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const list = document.createElement(ordered ? 'ol' : 'ul');

      while (i < lines.length && MARKER.test(lines[i])) {
        const content = lines[i].replace(MARKER, '');
        // A fence opening on the same line as a list marker — `6. ```python`, which is
        // how models routinely introduce a code block mid-list. Close the list and hand
        // the line back to the fence branch with the marker stripped.
        if (content.startsWith('```')) {
          lines[i] = content;
          break;
        }
        list.append(para('li', content));
        i += 1;
      }

      if (list.childElementCount) target.append(list);
      continue;
    }

    if (!line.trim()) {
      i += 1;
      continue;
    }

    // A paragraph runs until a blank line or the start of another block.
    const body = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(```|#{1,4}\s|>\s?|\s*([-*+]|\d+\.)\s)/.test(lines[i])
    ) {
      body.push(lines[i++]);
    }
    target.append(para('p', body.join('\n')));
  }
}
