# arc.ai — the web front end

A chat and memory interface for ARC, in a browser. No orb, no voice: just the
conversation and everything ARC remembers.

This is a **separate build**. It does not modify the ARC application — `ARC` in a
terminal still opens the app window, `arc serve` behaves as before, and
`arc/interface/webui/` (orb, animations, voice) is untouched and still shipped.

## Running it

```bash
make web          # http://127.0.0.1:4173
make open         # open that in a browser
```

Chat and memory need a running ARC. In another terminal:

```bash
ARC               # or: arc serve
```

The site works without one — it says `ARC offline` in the rail and every request
explains itself rather than failing silently.

## How it fits together

```
browser ──▶ web/serve.py ──▶ ARC (127.0.0.1:8787)
            static files      /api/* proxied
```

The proxy exists so the browser sees one origin. The alternative — pointing the page
straight at ARC — needs CORS headers on `arc/interface/server.py`, and that server is
deliberately loopback-only because ARC has unrestricted access to the machine (§0.3).
Proxying keeps that boundary exactly where it was.

`web/serve.py` is a development convenience. It is not part of what gets deployed.

## Deploying

The site is static: HTML, CSS, and ES modules, no build step and no node toolchain.
Whatever is in `web/` is the artifact.

`.github/workflows/pages.yml` publishes it to GitHub Pages on a push to `main`, or on
demand from the Actions tab. Enable Pages once in **Settings → Pages → Source → GitHub
Actions**, and it lands at `https://<user>.github.io/<repo>/`. A custom domain is a
setting on that same page.

**A deployed page has no backend.** GitHub Pages serves files; it cannot run ARC. So the
hosted site loads, shows `ARC offline`, and waits for you to point it at an ARC through
**Connection** in the sidebar. Two things to know before that will work:

- ARC must be reachable from wherever the browser is. Today it listens on loopback only,
  so a hosted page cannot reach it without a tunnel or a deliberate change to how ARC is
  exposed — which is a real security decision, not a config tweak.
- A page served over `https://` cannot call an `http://` API; the browser blocks it as
  mixed content. The Connection dialog says so when the values you enter would trip it.

Until then the honest description is: the site deploys, and it is fully usable locally.

## Files

| | |
|---|---|
| `index.html` | The whole app shell — two views, one dialog |
| `styles.css` | Theme. One accent (`#4A9EFF`), dark and light |
| `js/api.js` | Talking to ARC: configurable base URL, SSE parsing |
| `js/chat.js` | The conversation view — streaming, stop, edit, branch, retry, copy |
| `js/markdown.js` | Small markdown renderer, DOM nodes only |
| `js/highlight.js` | Syntax highlighting for code blocks, hand-written |
| `js/store.js` | Conversations, branches, search and export — in localStorage |
| `js/memory.js` | Search over memory, and adding to it |
| `js/app.js` | View switching, thread list, connection status |
| `serve.py` | Local dev server and API proxy |

## Where conversations live

ARC remembers *facts* — every turn is written to its memory and is searchable — but it
has no notion of a thread you can reopen. `/chat/stream` takes one message and returns
one reply; there is no conversation resource to list or fetch.

So threads live in `localStorage`, which is also what keeps them working if this page is
ever hosted with no backend of its own. The trade-off, plainly: **threads are
per-browser**, and clearing site data loses them. The content is still in ARC's memory
and findable from the Memory view; what is lost is the grouping into named conversations.

## Editing branches, it does not overwrite

Editing a message forks the conversation: the path you were on is kept and a `‹ 2/2 ›`
switcher appears on that turn. The point of editing is usually to compare, so discarding
the reply you already had would defeat it.

A fork is stored as the *tail* of the conversation from the edited turn onward —
`versions[i] = { list: [tail, …], active: n }` — and the visible conversation is always
`turns`, which is head + active tail. Nothing outside `store.js` needs to know forks
exist. Conversation search deliberately reaches into inactive branches: a message you
edited away from is otherwise unreachable except by remembering where you forked.

## Multi-turn context

ARC's `_compose` builds only `[system, user]` — it keeps no conversation history, so a
naive client gets an assistant with amnesia between turns.

The fix is in `buildSystem()` in `chat.js`: the last dozen turns travel in the `system`
field, which `/chat/stream` already accepts. Nothing on ARC's side changes.

**If you touch that function, keep the provenance sentence.** Sending `system` replaces
ARC's default, which carries an instruction never to copy memory markers like
`[episodic, 2026-07-30]` into replies. Without it the model imitates them, the reply is
stored, and it compounds every turn — it took a format change to fix the first time.
There is a test.

## Two things worth knowing if you change this

**Chat must send `speak: false`.** ARC synthesises speech as it generates by default, and
this build has no voice. Leaving it out makes the machine talk to an empty room. There is
a test for it.

**The turn ends at the `done` event, not at end-of-stream.** ARC answers with
`Connection: keep-alive` and never closes the socket, so waiting for EOF waits forever —
measured at 45s and still open, both directly and through the proxy. ARC's own UI does
not notice because it goes idle on a `state` event and leaves the read loop running.
