"""The panel ARC lives in — a floating overlay, not an application window.

Three differences from ``arc/interface/app.py``, and all three are the point:

* **It does not take focus.** ``NSNonactivatingPanelMask`` means summoning ARC over your
  editor does not deactivate the editor. You keep your cursor, your selection, and your
  undo stack.
* **It floats above everything.** A window level above normal windows, and a collection
  behaviour that follows you between Spaces and over full-screen apps, so ARC is reachable
  from wherever you are rather than being a window you have to go and find.
* **It has no chrome.** Borderless and transparent, because the orb is the interface.

Two geometries: CENTRE, where it is the thing you are talking to, and CORNER, where it has
shrunk to the top right to get out of your way while it works. Moving between them is a
frame animation, and the page is told which state it is in so the orb can animate to match.
"""

from __future__ import annotations

from typing import Any

from arc.log import get_logger

_log = get_logger(__name__)

CENTRE = "centre"
CORNER = "corner"

#: Centre is sized for a conversation; corner is sized for an orb and a line of status.
CENTRE_SIZE = (720.0, 520.0)
CORNER_SIZE = (280.0, 132.0)

#: Gap from the screen edges when parked in the corner, below the menu bar.
CORNER_MARGIN = 16.0

TRANSITION_SECONDS = 0.34


def available() -> bool:
    """Whether a panel can be opened on this machine."""
    try:
        import AppKit  # noqa: F401
        import WebKit  # noqa: F401
    except Exception:
        return False
    return True


class OrbPanel:
    """A borderless floating panel hosting the orb UI."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._window: Any = None
        self._webview: Any = None
        self._state = CORNER

    # ── geometry ────────────────────────────────────────────────────────

    def _frame_for(self, state: str) -> Any:
        import AppKit

        screen = AppKit.NSScreen.mainScreen()
        # visibleFrame excludes the menu bar and Dock, which is what keeps the corner
        # position from sliding under the menu bar on a laptop display.
        area = screen.visibleFrame()

        if state == CENTRE:
            width, height = CENTRE_SIZE
            x = area.origin.x + (area.size.width - width) / 2
            # Slightly above true centre: centred text sits low to the eye, and this is
            # roughly where Spotlight puts itself for the same reason.
            y = area.origin.y + (area.size.height - height) / 2 + area.size.height * 0.08
        else:
            width, height = CORNER_SIZE
            x = area.origin.x + area.size.width - width - CORNER_MARGIN
            y = area.origin.y + area.size.height - height - CORNER_MARGIN

        return AppKit.NSMakeRect(x, y, width, height)

    # ── lifecycle ───────────────────────────────────────────────────────

    def build(self) -> None:
        """Create the panel and start loading the UI."""
        import AppKit
        import WebKit
        from Foundation import NSURL, NSURLRequest

        style = (
            AppKit.NSWindowStyleMaskBorderless
            | AppKit.NSWindowStyleMaskNonactivatingPanel
            | AppKit.NSWindowStyleMaskFullSizeContentView
        )

        window = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            self._frame_for(CORNER), style, AppKit.NSBackingStoreBuffered, False
        )

        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setHasShadow_(True)
        window.setMovableByWindowBackground_(True)
        # Above ordinary windows but below the screen saver and system alerts. Floating is
        # the level Apple uses for palettes; anything higher would sit over dialogs, which
        # is antisocial.
        window.setLevel_(AppKit.NSFloatingWindowLevel)
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        # Without this the panel vanishes the moment you click your editor.
        window.setHidesOnDeactivate_(False)
        window.setReleasedWhenClosed_(False)

        config = WebKit.WKWebViewConfiguration.alloc().init()
        webview = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            window.contentView().bounds(), config
        )
        webview.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        # The page paints its own translucent backdrop; an opaque webview would draw a
        # rectangle behind the orb and undo the whole effect.
        with _Suppressed():
            webview.setValue_forKey_(False, "drawsBackground")

        window.contentView().addSubview_(webview)
        webview.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(self._url)))

        self._window = window
        self._webview = webview

    # ── state ───────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    def show(self, state: str = CENTRE, *, animate: bool = True) -> None:
        """Bring the panel on screen in the given geometry."""
        import AppKit

        if self._window is None:
            self.build()

        first_time = not self._window.isVisible()
        if first_time:
            # Placed before it is shown, so it does not appear in the old spot and slide.
            self._window.setFrame_display_(self._frame_for(state), False)

        # orderFrontRegardless, not makeKeyAndOrderFront: the panel must appear without
        # ARC becoming the active application and stealing the user's focus.
        self._window.orderFrontRegardless()
        self.set_state(state, animate=animate and not first_time)

        if state == CENTRE:
            # Only when centred does it accept typing, and even then the app behind stays
            # active — a non-activating panel can be key without its owner being frontmost.
            self._window.makeKeyWindow()
            AppKit.NSApp.activateIgnoringOtherApps_(False)

    def set_state(self, state: str, *, animate: bool = True) -> None:
        """Move between centre and corner."""
        import AppKit

        if self._window is None or state == self._state:
            self._notify_page(state)
            self._state = state
            return

        target = self._frame_for(state)
        if animate:
            context = AppKit.NSAnimationContext.currentContext()
            AppKit.NSAnimationContext.beginGrouping()
            context.setDuration_(TRANSITION_SECONDS)
            self._window.animator().setFrame_display_(target, True)
            AppKit.NSAnimationContext.endGrouping()
        else:
            self._window.setFrame_display_(target, True)

        self._state = state
        # Told before the frame settles so the orb animation runs alongside the move
        # rather than after it.
        self._notify_page(state)

    def hide(self) -> None:
        if self._window is not None:
            self._window.orderOut_(None)

    def toggle(self) -> None:
        """What the hotkey calls: centre it, or park it if it is already centred."""
        if self._window is None or not self._window.isVisible():
            self.show(CENTRE)
        elif self._state == CENTRE:
            self.show(CORNER)
        else:
            self.show(CENTRE)

    # ── page bridge ─────────────────────────────────────────────────────

    def _notify_page(self, state: str) -> None:
        """Tell the UI which geometry it is in so the orb can animate to match."""
        if self._webview is None:
            return
        script = f"window.arcDesktop && window.arcDesktop.setState({state!r})"
        with _Suppressed():
            self._webview.evaluateJavaScript_completionHandler_(script, None)

    def set_activity(self, activity: str) -> None:
        """THINKING, WORKING, IDLE — drives the coloured orbs on the page."""
        if self._webview is None:
            return
        script = f"window.arcDesktop && window.arcDesktop.setActivity({activity!r})"
        with _Suppressed():
            self._webview.evaluateJavaScript_completionHandler_(script, None)


class _Suppressed:
    """Swallow AppKit key-value quirks that differ across macOS versions.

    ``drawsBackground`` is private on WKWebView and has moved before; a panel that refuses
    to open because one cosmetic setter was renamed is a bad trade.
    """

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if exc_type is not None:
            _log.debug("appkit call failed", extra={"error": repr(exc)})
        return True
