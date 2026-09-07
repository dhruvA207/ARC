"""The panel ARC lives in — a floating overlay, not an application window.

Three differences from ``arc/interface/app.py``, and all three are the point:

* **It does not take focus.** ``NSNonactivatingPanelMask`` means ARC waking up over your
  editor does not deactivate the editor. You keep your cursor, your selection, and your
  undo stack.
* **It floats above everything.** A window level above normal windows, and a collection
  behaviour that follows you between Spaces and over full-screen apps, so ARC is reachable
  from wherever you are rather than being a window you have to go and find.
* **It has no chrome.** Borderless and transparent, because the orb is the interface.

**One geometry.** It parks in the top-right corner and stays there — it never throws
itself into the middle of the screen. What changes is not where it is but how awake it is,
and that is :meth:`OrbPanel.set_muted`: resting, it is click-through, so the corner of the
screen behind it stays usable; woken, it takes the pointer back and listens.

Because a resting panel is click-through it sees no mouse events of its own, and the orb
still has to know when the cursor is near enough to move aside for. That is what
:meth:`OrbPanel._pointer_tick` is: a screen-space read of the cursor, forwarded to the page
only when it is close enough to matter.
"""

from __future__ import annotations

from typing import Any

from arc.log import get_logger

_log = get_logger(__name__)

#: The only state the panel has a position for. ``CENTRE`` survives as the cue the page
#: turns into its arrival animation — it is not a place the window goes any more.
CORNER = "corner"
CENTRE = "centre"

#: Square, so the orb can deform in any direction without running out of canvas — the
#: points swing a long way aside when the cursor parts them.
CORNER_SIZE = (300.0, 300.0)

#: Gap from the screen edges, below the menu bar.
CORNER_MARGIN = 12.0

#: Matches TRANSITION_SECONDS in ui/orb.js, so the points converge at the rate the page
#: thinks they do.
TRANSITION_SECONDS = 0.34

#: How long the arrival is left running before the page is told it has settled.
ARRIVAL_SECONDS = 0.9

#: How often the cursor is sampled while ARC is on screen.
POINTER_HZ = 60.0

#: The cursor has to be almost touching the orb before it reacts: `near` is 1 within this
#: many points of the orb's edge, and ramps to 0 over `POINTER_FALLOFF_PT` beyond that.
#: Kept tight on purpose — an orb that flinched at a cursor halfway across the screen
#: would be noise. This is a courtesy to whatever is behind ARC, not a hover target.
POINTER_NEAR_PT = 24.0
POINTER_FALLOFF_PT = 64.0


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
        self._pointer_timer: Any = None
        self._pointer_active = False

    # ── geometry ────────────────────────────────────────────────────────

    def _current_screen(self) -> Any:
        """The display the cursor is on, or the main one if that cannot be determined.

        ``NSScreen.mainScreen`` is "the screen with the key window", which for an
        accessory app with no key window is whichever display the frontmost *other*
        application happens to be on. On a two-display desk that put ARC on the external
        monitor while the user was working on the laptop, with no way to tell it was even
        running. The cursor is the better answer to "which screen is the user at".
        """
        import AppKit

        location = AppKit.NSEvent.mouseLocation()
        for screen in AppKit.NSScreen.screens():
            frame = screen.frame()
            if (
                frame.origin.x <= location.x < frame.origin.x + frame.size.width
                and frame.origin.y <= location.y < frame.origin.y + frame.size.height
            ):
                return screen
        return AppKit.NSScreen.mainScreen()

    def _corner_frame(self) -> Any:
        """Where ARC lives: top right of the display the user is currently at."""
        import AppKit

        # visibleFrame excludes the menu bar and Dock, which is what keeps the corner
        # position from sliding under the menu bar on a laptop display.
        area = self._current_screen().visibleFrame()

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
            self._corner_frame(), style, AppKit.NSBackingStoreBuffered, False
        )

        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        # No shadow and not draggable: it is a fixed resident of the corner, and a shadow
        # would draw a faint rectangle around a window whose whole point is having no edge.
        window.setHasShadow_(False)
        window.setMovableByWindowBackground_(False)
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
        # ARC starts at rest, and a resting panel must not swallow clicks aimed at the
        # corner of the screen behind it.
        window.setIgnoresMouseEvents_(True)

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
        self._start_pointer_tracking()

    # ── state ───────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    def show(self) -> None:
        """Bring the panel on screen, in the corner, without taking focus."""
        if self._window is None:
            self.build()

        if not self._window.isVisible():
            # Placed before it is shown, so it never appears in a stale spot and slides.
            self._window.setFrame_display_(self._corner_frame(), False)

        # orderFrontRegardless, not makeKeyAndOrderFront: the panel must appear without
        # ARC becoming the active application and stealing the user's focus.
        self._window.orderFrontRegardless()

    def intro(self) -> None:
        """First run: the points converge into the orb, in the corner where it lives.

        There is no full-screen arrival any more. ARC appears where it is going to stay.
        """
        import AppKit

        if self._window is None:
            self.build()

        self._window.setFrame_display_(self._corner_frame(), False)
        self._window.setIgnoresMouseEvents_(True)
        self._window.orderFrontRegardless()
        self._state = CORNER

        # The page reads anything that is not `corner` as "play the arrival".
        self._notify_page(CENTRE)

        def settle(_timer: Any) -> None:
            self._notify_page(CORNER)

        AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(ARRIVAL_SECONDS, False, settle)

    def hide(self) -> None:
        if self._window is not None:
            self._window.orderOut_(None)

    # ── pointer proximity ───────────────────────────────────────────────

    def _start_pointer_tracking(self) -> None:
        """Sample the cursor and tell the page when it comes close enough to part the orb.

        A timer rather than an event monitor. A resting panel is deliberately
        click-through, so it is not in the responder chain and receives no mouse events at
        all; ``NSEvent.mouseLocation`` is a screen-space read that does not need to be.
        """
        import AppKit

        if self._pointer_timer is not None:
            return

        def tick(_timer: Any) -> None:
            with _Suppressed():
                self._pointer_tick()

        self._pointer_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            1.0 / POINTER_HZ, True, tick
        )

    def _pointer_tick(self) -> None:
        """One sample: cursor position in the page's coordinates, and how close it is."""
        import AppKit

        if self._window is None or self._webview is None or not self._window.isVisible():
            return

        location = AppKit.NSEvent.mouseLocation()
        frame = self._window.frame()

        # AppKit's screen origin is bottom-left and the page's is top-left, so y flips.
        x = location.x - frame.origin.x
        y = frame.origin.y + frame.size.height - location.y

        # Must agree with ui/orb.js `_draw`, which is where these two numbers come from.
        orb_x = frame.size.width / 2.0
        orb_y = frame.size.height * 0.46
        orb_radius = min(frame.size.width, frame.size.height) * 0.3

        distance = ((x - orb_x) ** 2 + (y - orb_y) ** 2) ** 0.5
        edge = orb_radius + POINTER_NEAR_PT
        if distance <= edge:
            near = 1.0
        elif distance >= edge + POINTER_FALLOFF_PT:
            near = 0.0
        else:
            near = 1.0 - (distance - edge) / POINTER_FALLOFF_PT

        # Nothing to say while the cursor is elsewhere — but the *first* frame after it
        # leaves still has to be sent, or the cloud stays parted around a cursor that has
        # gone.
        if near <= 0.0 and not self._pointer_active:
            return
        self._pointer_active = near > 0.0

        with _Suppressed():
            self._webview.evaluateJavaScript_completionHandler_(
                "window.arcDesktop && window.arcDesktop.setPointer("
                f"{x:.1f}, {y:.1f}, {near:.3f})",
                None,
            )

    # ── page bridge ─────────────────────────────────────────────────────

    def _notify_page(self, state: str) -> None:
        """Tell the UI which state it is in so the orb can animate to match."""
        if self._webview is None:
            return
        script = f"window.arcDesktop && window.arcDesktop.setState({state!r})"
        with _Suppressed():
            self._webview.evaluateJavaScript_completionHandler_(script, None)

    def set_activity(self, activity: str) -> None:
        """THINKING, WORKING, IDLE — drives the coloured satellites on the page."""
        self._call(f"setActivity({activity!r})")

    def set_muted(self, muted: bool) -> None:
        """Rest or wake — the only thing about this panel that changes.

        Resting: the darker blue, the microphone shut, and the pointer passes straight
        through, so the top-right corner of the screen is still somewhere you can click.
        Woken: purple, listening, and the panel takes the pointer back.
        """
        if self._window is not None:
            with _Suppressed():
                self._window.setIgnoresMouseEvents_(bool(muted))
                if not muted:
                    # Waking is the one moment it is allowed to move: onto the display
                    # the user is currently at. It never moves while it rests, so it does
                    # not wander around under you.
                    self._window.setFrame_display_(self._corner_frame(), True)
                    self._window.orderFrontRegardless()
        self._call(f"setMuted({'true' if muted else 'false'})")

    def _call(self, expression: str) -> None:
        """Call into the page, tolerating a webview that has not finished loading."""
        if self._webview is None:
            return
        with _Suppressed():
            self._webview.evaluateJavaScript_completionHandler_(
                f"window.arcDesktop && window.arcDesktop.{expression}", None
            )


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
