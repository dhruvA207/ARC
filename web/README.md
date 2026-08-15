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
| `js/chat.js` | The conversation view |
| `js/memory.js` | Search over memory, and adding to it |
| `js/app.js` | View switching, connection status |
| `serve.py` | Local dev server and API proxy |

## Two things worth knowing if you change this

**Chat must send `speak: false`.** ARC synthesises speech as it generates by default, and
this build has no voice. Leaving it out makes the machine talk to an empty room. There is
a test for it.

**The turn ends at the `done` event, not at end-of-stream.** ARC answers with
`Connection: keep-alive` and never closes the socket, so waiting for EOF waits forever —
measured at 45s and still open, both directly and through the proxy. ARC's own UI does
not notice because it goes idle on a `state` event and leaves the read loop running.
