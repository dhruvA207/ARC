"""Double-tap ⌘ as a global summon key.

Chosen over a normal shortcut because it consumes nothing. A system-wide ⌘S would be
registered ahead of the focused application and stop Save working in every app on the
machine; ⌥Space and friends are free but still take a chord away from you. Double-tapping
a modifier takes nothing at all, which is why Spotlight replacements tend to use it.

The cost is that it is timing-based. Watching flag changes means the *only* signal is
"command went down, came up, went down again, quickly" — so it has to be careful not to
fire while someone is typing ⌘-shortcuts at speed. Two guards do that: any non-modifier
key pressed while command is held cancels the sequence, and the two taps must fall inside
a short window.

Needs Accessibility. macOS will not deliver global key events to a process without it,
so the caller is expected to have checked (`arc doctor` reports it).
"""

from __future__ import annotations

import time
from collections.abc import Callable

from arc.log import get_logger

_log = get_logger(__name__)

#: Both taps must land inside this. Long enough to be comfortable, short enough that two
#: unrelated ⌘-presses a second apart are not read as a summon.
WINDOW_SECONDS = 0.45

#: A tap shorter than this is a bounce, not a press.
MIN_TAP_SECONDS = 0.02


class DoubleTapCommand:
    """Watches modifier changes and calls ``on_trigger`` on a double-tap of ⌘.

    Kept free of AppKit so the decision logic can be tested without an event loop or a
    window server; ``install`` is the only part that touches the frameworks.
    """

    def __init__(self, on_trigger: Callable[[], None]) -> None:
        self._on_trigger = on_trigger
        self._last_release: float | None = None
        self._pressed_at: float | None = None
        self._dirty = False
        self._monitors: list[object] = []

    # ── decision logic ──────────────────────────────────────────────────

    def key_pressed(self) -> None:
        """A non-modifier key went down. Whatever this is, it is not a summon."""
        self._dirty = True

    def flags_changed(self, command_down: bool, now: float | None = None) -> bool:
        """Feed one modifier transition. Returns True when a double-tap completes."""
        moment = time.monotonic() if now is None else now

        if command_down:
            self._pressed_at = moment
            self._dirty = False
            return False

        # Command released.
        pressed_at, self._pressed_at = self._pressed_at, None
        if pressed_at is None:
            return False

        held = moment - pressed_at
        if self._dirty or held < MIN_TAP_SECONDS:
            # A shortcut was typed, or the press was noise. Reset rather than count it,
            # so ⌘C ⌘V in quick succession never summons anything.
            self._last_release = None
            self._dirty = False
            return False

        previous, self._last_release = self._last_release, moment
        if previous is not None and moment - previous <= WINDOW_SECONDS:
            self._last_release = None
            return True
        return False

    # ── installation ────────────────────────────────────────────────────

    def install(self) -> bool:
        """Start watching. Returns False when the frameworks are unavailable."""
        try:
            import AppKit
        except Exception:
            _log.warning("hotkey unavailable: AppKit could not be imported")
            return False

        def on_flags(event) -> None:  # noqa: ANN001 - AppKit event
            command = bool(event.modifierFlags() & AppKit.NSEventModifierFlagCommand)
            if self.flags_changed(command):
                self._on_trigger()

        def on_key(event) -> None:  # noqa: ANN001 - AppKit event
            self.key_pressed()

        # Global monitors see events destined for *other* applications; local monitors
        # see our own. Both are needed or the hotkey stops working whenever ARC itself
        # happens to be frontmost.
        flags_mask = AppKit.NSEventMaskFlagsChanged
        key_mask = AppKit.NSEventMaskKeyDown

        self._monitors.append(
            AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(flags_mask, on_flags)
        )
        self._monitors.append(
            AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(key_mask, on_key)
        )

        def local_flags(event):  # noqa: ANN001, ANN202 - AppKit event
            on_flags(event)
            return event

        def local_key(event):  # noqa: ANN001, ANN202 - AppKit event
            on_key(event)
            return event

        self._monitors.append(
            AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(flags_mask, local_flags)
        )
        self._monitors.append(
            AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(key_mask, local_key)
        )
        return True

    def uninstall(self) -> None:
        try:
            import AppKit
        except Exception:
            return
        for monitor in self._monitors:
            if monitor is not None:
                AppKit.NSEvent.removeMonitor_(monitor)
        self._monitors.clear()
