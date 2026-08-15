"""Local dev server for the arc.ai site — static files plus an API proxy.

Why this exists rather than serving the site from ``arc/interface/server.py``: that
server is the *other* ARC and this work must not touch it. It is also loopback-only by
deliberate design (§0.3 — ARC has unrestricted machine access, so its HTTP endpoint must
not be network-reachable), and adding CORS headers to it would be a change to exactly
the thing that is meant not to change.

So the site is served here instead, and anything under ``/api/`` is forwarded to a
running ARC. From the browser's point of view every request is same-origin, so there is
no CORS involved and ARC keeps its headers, its bind address, and its tests untouched.

In production the site is static — GitHub Pages serves the same files with no Python at
all — and the frontend talks to whatever API base it is configured with. This proxy is a
development convenience, not part of the deployed artifact.

Standard library only, matching the rest of the repo (§7: dependencies are a liability).
"""

from __future__ import annotations

import argparse
import errno
import sys
from functools import partial
from http.client import HTTPConnection
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SITE_DIR = Path(__file__).resolve().parent

#: Where a local ARC listens. Matches ``arc.interface.server.DEFAULT_PORT``; read from
#: the running server's endpoint file when one exists so a non-default port still works.
DEFAULT_ARC = "http://127.0.0.1:8787"

#: This server is loopback-only too. The proxy reaches ARC, and ARC can run shell
#: commands, so binding it to 0.0.0.0 would hand the machine to the network through the
#: side door that the main server is careful to keep shut.
BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 4173

#: Streamed in small chunks so server-sent events arrive as they are generated. Buffering
#: here would turn token-by-token streaming back into a progress bar.
CHUNK = 1024

#: Hop-by-hop headers must not be forwarded (RFC 9110 §7.6.1). Content-Length is dropped
#: because the proxied body is re-sent chunked.
_SKIP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
    "server",
    "date",
}


def arc_endpoint() -> str:
    """Return the base URL of a running ARC, falling back to the default port.

    ARC advertises itself in ``~/.arc/run/server.json`` when it starts, so a server on a
    non-default port is found without anyone having to pass a flag.
    """
    try:
        import json

        from arc.paths import run_dir

        target = run_dir() / "server.json"
        if target.is_file():
            data = json.loads(target.read_text(encoding="utf-8"))
            host, port = data.get("host"), data.get("port")
            if host and port:
                return f"http://{host}:{port}"
    except Exception:
        # Reading the advertisement is an optimisation. If ARC is not installed in this
        # interpreter, or the file is stale or malformed, the default is still correct.
        pass
    return DEFAULT_ARC


class Handler(SimpleHTTPRequestHandler):
    """Serve the site, and forward ``/api/*`` to ARC."""

    arc_base: str = DEFAULT_ARC

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy("GET")
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path.startswith("/api/"):
            self._proxy("POST")
            return
        self.send_error(405, "the site itself is static; only /api/ accepts POST")

    def _proxy(self, method: str) -> None:
        """Forward one request to ARC and stream the response straight back.

        Uses ``http.client`` rather than ``urllib.request`` deliberately. ARC answers a
        stream with ``Connection: keep-alive`` on an HTTP/1.0 response that carries no
        ``Content-Length`` and no chunked encoding — the body is delimited by the close.
        ``urllib`` believes the keep-alive, concludes the response has unknown length on
        a persistent connection, and blocks forever. curl and browsers ignore it and read
        to EOF, which is why ARC's own UI never hit this.
        """
        parsed = urlparse(self.arc_base)
        path = self.path[len("/api") :]

        body = None
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""

        headers = {}
        for name in ("Content-Type", "Accept"):
            if self.headers.get(name):
                headers[name] = self.headers[name]
        if body is not None:
            headers["Content-Length"] = str(len(body))

        try:
            conn = HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=600)
            conn.request(method, path, body=body, headers=headers)
            upstream = conn.getresponse()
        except OSError as exc:
            # ARC is not running. Said plainly, because "failed to fetch" in a console is
            # the least useful version of this message.
            detail = str(exc).encode("utf-8", "replace")
            self._send_json(
                503,
                b'{"error": "ARC is not running. Start it with `ARC` or `arc serve`.",'
                b' "detail": "' + detail + b'"}',
            )
            return

        # The body ends when ARC closes the socket, whatever the keep-alive header says.
        # Without this http.client waits for a length that is never coming.
        if upstream.length is None:
            upstream.will_close = True

        try:
            self._relay(upstream)
        finally:
            conn.close()

    def _relay(self, upstream: Any) -> None:
        """Relay status, headers, and body, flushing as chunks arrive."""
        self.send_response(upstream.status)
        for name, value in upstream.getheaders():
            # `Server` and `Date` are skipped because send_response already emitted them
            # and two of each is malformed.
            if name.lower() not in _SKIP_HEADERS:
                self.send_header(name, value)
        self.end_headers()

        try:
            # read1, not read: a single underlying read returns whatever has arrived
            # rather than blocking for a full buffer. With read(1024) a four-token reply
            # would sit unsent until the stream ended, which is the opposite of the point.
            while chunk := upstream.read1(CHUNK):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # The browser navigated away or hit stop. Normal, not an error.
            pass

    def _send_json(self, status: int, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def end_headers(self) -> None:
        # The site is edited and reloaded constantly during development; a cached module
        # that does not match the HTML is a confusing way to lose ten minutes.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("  %s\n" % (fmt % args))


def main(argv: list[str] | None = None) -> int:
    """Run the dev server until interrupted."""
    parser = argparse.ArgumentParser(description="serve the arc.ai site locally")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--arc",
        default=None,
        help="base URL of a running ARC (default: read from ~/.arc/run/server.json)",
    )
    args = parser.parse_args(argv)

    Handler.arc_base = args.arc or arc_endpoint()
    handler = partial(Handler, directory=str(SITE_DIR))

    try:
        httpd = ThreadingHTTPServer((BIND_HOST, args.port), handler)
    except OSError as exc:
        # Overwhelmingly this is "someone is already serving the site" — usually a
        # forgotten `make web` in another terminal. A twelve-frame socketserver
        # traceback buries that, so say it plainly and suggest both ways out.
        if exc.errno == errno.EADDRINUSE:
            print(f"arc.ai: port {args.port} is already in use.", file=sys.stderr)
            print("  another `make web` is probably still running.", file=sys.stderr)
            hint = f"  stop it, or use another port:  make web PORT={args.port + 1}"
            print(hint, file=sys.stderr)
            return 1
        print(f"arc.ai: could not start on port {args.port}: {exc}", file=sys.stderr)
        return 1

    with httpd:
        print(f"arc.ai   http://{BIND_HOST}:{args.port}")
        print(f"api      {Handler.arc_base}  (proxied at /api/)")
        print("stop     Ctrl-C\n", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
