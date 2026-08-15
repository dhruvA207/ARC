/* Syntax highlighting for fenced code blocks.
 *
 * Hand-written rather than a library: highlight.js is ~120 KB for a page whose entire
 * job is a chat window, and §7 treats dependencies as a liability. This covers the
 * languages that actually turn up in ARC's replies and degrades to plain text otherwise.
 *
 * Tokens are emitted as DOM nodes. There is no `innerHTML` here — code blocks are model
 * output, which is untrusted like everything else on this page.
 */

const KEYWORDS = {
  python: `False None True and as assert async await break class continue def del elif
    else except finally for from global if import in is lambda nonlocal not or pass raise
    return try while with yield self`,
  javascript: `async await break case catch class const continue debugger default delete
    do else export extends finally for from function if import in instanceof let new of
    return static super switch this throw try typeof var void while with yield null true
    false undefined`,
  bash: `if then else elif fi for while do done case esac function return export local
    source echo cd set unset read exit`,
  sql: `select from where join left right inner outer on group by order having limit
    insert into values update set delete create table drop alter index as and or not null`,
};

KEYWORDS.py = KEYWORDS.python;
KEYWORDS.js = KEYWORDS.javascript;
KEYWORDS.ts = KEYWORDS.javascript;
KEYWORDS.typescript = KEYWORDS.javascript;
KEYWORDS.sh = KEYWORDS.bash;
KEYWORDS.shell = KEYWORDS.bash;
KEYWORDS.zsh = KEYWORDS.bash;

/** Line-comment marker per language. */
const LINE_COMMENT = {
  python: '#', py: '#', bash: '#', sh: '#', shell: '#', zsh: '#', yaml: '#', yml: '#',
  toml: '#', ruby: '#', rb: '#',
  javascript: '//', js: '//', ts: '//', typescript: '//', json: null, c: '//', cpp: '//',
  java: '//', go: '//', rust: '//', rs: '//', sql: '--',
};

function keywordSet(lang) {
  const raw = KEYWORDS[lang];
  return raw ? new Set(raw.split(/\s+/).filter(Boolean)) : null;
}

function span(cls, text) {
  const el = document.createElement('span');
  el.className = `tok-${cls}`;
  el.textContent = text;
  return el;
}

/**
 * Tokenise `source` and append the result to `target`.
 *
 * A single left-to-right scan. Strings and comments are consumed whole so that a keyword
 * inside a string is not highlighted as one — the mistake that makes naive
 * regex-replacement highlighters look broken on the first realistic snippet.
 */
export function highlight(target, source, lang = '') {
  const language = String(lang || '').toLowerCase();
  const keywords = keywordSet(language);
  const comment = language in LINE_COMMENT ? LINE_COMMENT[language] : '//';

  // Nothing known about this language: keep it readable rather than guessing.
  if (!keywords && language !== 'json' && !comment) {
    target.append(document.createTextNode(source));
    return;
  }

  let i = 0;
  let plain = '';

  const flush = () => {
    if (plain) {
      target.append(document.createTextNode(plain));
      plain = '';
    }
  };

  while (i < source.length) {
    const rest = source.slice(i);

    // Line comment
    if (comment && rest.startsWith(comment)) {
      flush();
      const end = source.indexOf('\n', i);
      const stop = end === -1 ? source.length : end;
      target.append(span('comment', source.slice(i, stop)));
      i = stop;
      continue;
    }

    // Block comment, for the C-family languages
    if (comment === '//' && rest.startsWith('/*')) {
      flush();
      const end = source.indexOf('*/', i + 2);
      const stop = end === -1 ? source.length : end + 2;
      target.append(span('comment', source.slice(i, stop)));
      i = stop;
      continue;
    }

    // Strings, including triple-quoted Python and template literals. Escapes are
    // respected so a quote inside a string does not end it early.
    const quote = rest.match(/^("""|'''|"|'|`)/);
    if (quote) {
      flush();
      const mark = quote[1];
      let j = i + mark.length;
      while (j < source.length) {
        if (source[j] === '\\') {
          j += 2;
          continue;
        }
        if (source.startsWith(mark, j)) {
          j += mark.length;
          break;
        }
        j += 1;
      }
      target.append(span('string', source.slice(i, Math.min(j, source.length))));
      i = j;
      continue;
    }

    // Numbers
    const number = rest.match(/^\d[\d_]*(\.\d+)?([eE][+-]?\d+)?/);
    if (number && !/[\w$]/.test(source[i - 1] || '')) {
      flush();
      target.append(span('number', number[0]));
      i += number[0].length;
      continue;
    }

    // Words: keywords, then function calls, then everything else
    const word = rest.match(/^[A-Za-z_$][\w$]*/);
    if (word) {
      const text = word[0];
      i += text.length;
      if (keywords?.has(text)) {
        flush();
        target.append(span('keyword', text));
      } else if (source[i] === '(') {
        flush();
        target.append(span('fn', text));
      } else if (language === 'json' && source[i] === ':') {
        flush();
        target.append(span('key', text));
      } else {
        plain += text;
      }
      continue;
    }

    plain += source[i];
    i += 1;
  }

  flush();
}
