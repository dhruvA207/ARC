"""ARC as a resident of the machine rather than an application you launch.

Runs as an *accessory* app: no Dock icon, no application menu, nothing in the app
switcher. The only permanent trace is the orb in the menu bar. Double-tap ⌘ brings the
panel to the centre of the screen; double-tap again parks it in the top-right corner,
where it stays — running, reachable, out of the way — until it is wanted again. Nothing
is quit and restarted between conversations.

The UI is served by the ARC process it talks to, so the panel is a client like any other
front end and conversations are shared through ``/conversations`` rather than kept here.
"""

from __future__ import annotations

import threading
from typing import Any

from arc.desktop import menubar, panel
from arc.log import get_logger

_log = get_logger(__name__)


def available() -> bool:
    """Whether the desktop shell can run on this machine."""
    return panel.available()


class DesktopApp:
    """Menu bar item, floating panel, and the hotkey that summons it."""

    def __init__(self, ui_url: str, *, web_url: str | None = None) -> None:
        self._ui_url = ui_url
        self._web_url = web_url or ui_url
        self._panel = panel.OrbPanel(ui_url)
        self._menu: menubar.MenuBar | None = None
        self._hotkey: Any = None
        self._muted = False
        self._app: Any = None

    # ── actions the menu and hotkey call ────────────────────────────────

    def summon(self) -> None:
        self._panel.show(panel.CENTRE)

    def toggle(self) -> None:
        self._panel.toggle()

    def toggle_mute(self) -> bool:
        """Flip the microphone. Returns the new muted state for the menu title."""
        self._muted = not self._muted
        self._panel.set_activity("MUTED" if self._muted else "IDLE")
        _log.info("microphone toggled", extra={"muted": self._muted})
        return self._muted

    def open_web(self) -> None:
        import AppKit
        from Foundation import NSURL

        AppKit.NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(self._web_url))

    def quit(self) -> None:
        import AppKit

        if self._hotkey is not None:
            self._hotkey.uninstall()
        if self._menu is not None:
            self._menu.remove()
        AppKit.NSApp.terminate_(None)

    # ── run ─────────────────────────────────────────────────────────────

    def run(self) -> int:
        """Own the main thread and the run loop until quit."""
        import AppKit

        from arc.desktop.hotkey import DoubleTapCommand

        app = AppKit.NSApplication.sharedApplication()
        # Accessory: no Dock icon and no app switcher entry, which is the whole point of
        # this being a resident rather than an application.
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self._app = app

        self._menu = menubar.MenuBar(
            on_summon=self.summon,
            on_toggle_mute=self.toggle_mute,
            on_open_web=self.open_web,
            on_quit=self.quit,
        )
        if not self._menu.install():
            _log.warning("menu bar item could not be installed")

        self._panel.build()
        # Parked, not centred: appearing over whatever the user is doing the instant ARC
        # starts would be the opposite of getting out of the way.
        self._panel.show(panel.CORNER, animate=False)

        self._hotkey = DoubleTapCommand(self.toggle)
        if not self._hotkey.install():
            _log.warning("global hotkey unavailable; use the menu bar item")

        _log.info("desktop shell running", extra={"ui": self._ui_url})
        app.run()
        return 0


def run(config: Any, *, port: int, serve: bool = True) -> int:
    """Start ARC's server in this process, then the desktop shell on the main thread.

    Same arrangement as ``arc ui``: the server runs on a background thread while AppKit
    owns the main one, because the main run loop is what speech recognition needs pumped.
    """
    from http.server import ThreadingHTTPServer

    from arc.interface import server

    if serve:
        runtime = server.Runtime(config)
        handler = type("BoundHandler", (server._Handler,), {"runtime": runtime})
        httpd = ThreadingHTTPServer((server.BIND_HOST, port), handler)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, name="arc-http", daemon=True).start()
        server.write_endpoint(port)
        print(f"ARC serving on http://{server.BIND_HOST}:{port} (loopback only)")

    base = f"http://{server.BIND_HOST}:{port}"
    try:
        return DesktopApp(f"{base}/desktop/", web_url=base).run()
    finally:
        if serve:
            server.clear_endpoint()
