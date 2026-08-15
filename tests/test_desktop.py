"""Tests for the desktop shell.

The AppKit parts cannot be exercised without a window server, so what is covered here is
everything that can be: the double-tap decision logic, which is pure timing and the most
likely thing to misbehave, and the contracts the shell depends on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arc.desktop.hotkey import MIN_TAP_SECONDS, WINDOW_SECONDS, DoubleTapCommand

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "arc" / "desktop" / "ui"


class _Recorder:
    def __init__(self) -> None:
        self.fired = 0

    def __call__(self) -> None:
        self.fired += 1


def _tap(watcher: DoubleTapCommand, at: float, hold: float = 0.08) -> bool:
    """One press-and-release of the command key."""
    watcher.flags_changed(True, now=at)
    return watcher.flags_changed(False, now=at + hold)


# --- the hotkey ---------------------------------------------------------------------


def test_two_quick_taps_fire() -> None:
    watcher = DoubleTapCommand(_Recorder())
    assert _tap(watcher, 0.0) is False
    assert _tap(watcher, 0.2) is True


def test_two_slow_taps_do_not_fire() -> None:
    """Two unrelated presses a second apart are not a summon."""
    watcher = DoubleTapCommand(_Recorder())
    _tap(watcher, 0.0)
    assert _tap(watcher, WINDOW_SECONDS + 0.2) is False


def test_a_shortcut_typed_while_command_is_held_cancels_it() -> None:
    """⌘C then ⌘V in quick succession must never summon anything.

    This is the failure that makes modifier double-taps unusable, so it is the first
    thing worth pinning.
    """
    watcher = DoubleTapCommand(_Recorder())

    watcher.flags_changed(True, now=0.0)
    watcher.key_pressed()  # the C in ⌘C
    assert watcher.flags_changed(False, now=0.1) is False

    watcher.flags_changed(True, now=0.2)
    watcher.key_pressed()  # the V in ⌘V
    assert watcher.flags_changed(False, now=0.3) is False


def test_a_shortcut_then_a_clean_double_tap_still_works() -> None:
    """Cancelling must reset cleanly, not wedge the watcher."""
    watcher = DoubleTapCommand(_Recorder())
    watcher.flags_changed(True, now=0.0)
    watcher.key_pressed()
    watcher.flags_changed(False, now=0.1)

    assert _tap(watcher, 1.0) is False
    assert _tap(watcher, 1.2) is True


def test_a_bounce_is_not_a_tap() -> None:
    watcher = DoubleTapCommand(_Recorder())
    _tap(watcher, 0.0)
    assert _tap(watcher, 0.2, hold=MIN_TAP_SECONDS / 2) is False


def test_three_taps_fire_once_not_twice() -> None:
    """The counter resets on fire, so a third tap starts a new sequence."""
    recorder = _Recorder()
    watcher = DoubleTapCommand(recorder)
    assert _tap(watcher, 0.0) is False
    assert _tap(watcher, 0.15) is True
    assert _tap(watcher, 0.30) is False


def test_a_release_without_a_press_is_ignored() -> None:
    """Modifier state can be observed mid-press when monitors are installed."""
    watcher = DoubleTapCommand(_Recorder())
    assert watcher.flags_changed(False, now=0.0) is False


# --- contracts the shell relies on --------------------------------------------------


def test_the_panel_ui_ships_with_the_package() -> None:
    for name in ("index.html", "panel.css", "panel.js", "orb.js"):
        assert (UI / name).is_file(), f"desktop/ui/{name} is missing"


def test_the_panel_is_served_from_arcs_own_process() -> None:
    """Same-origin with the API, so the panel needs no proxy and no CORS."""
    from arc.interface import server

    assert server.DESKTOP_UI_DIR.name == "ui"
    assert server.DESKTOP_UI_DIR.parent.name == "desktop"


def test_the_panel_does_not_ask_arc_to_speak() -> None:
    body = (UI / "panel.js").read_text(encoding="utf-8")
    assert "speak: false" in body


def test_the_panel_marks_its_conversations_as_desktop() -> None:
    """The web UI reads `origin` to show which threads came from the computer."""
    body = (UI / "panel.js").read_text(encoding="utf-8")
    assert "'desktop'" in body


def test_the_panel_never_activates_arc_over_the_users_app() -> None:
    """Summoning ARC must not deactivate whatever you were typing in."""
    source = (ROOT / "arc" / "desktop" / "panel.py").read_text(encoding="utf-8")
    assert "orderFrontRegardless" in source
    assert "NonactivatingPanel" in source
    # The call, not the word: the comment above it explains why this is the wrong API,
    # and that explanation is worth keeping.
    assert ".makeKeyAndOrderFront_(" not in source, "that would steal focus"
    assert "activateIgnoringOtherApps_(True)" not in source, "that would steal focus"


def test_the_shell_has_no_dock_icon() -> None:
    source = (ROOT / "arc" / "desktop" / "app.py").read_text(encoding="utf-8")
    assert "NSApplicationActivationPolicyAccessory" in source


@pytest.mark.parametrize("state", ["centre", "corner"])
def test_both_geometries_are_defined(state: str) -> None:
    from arc.desktop import panel

    assert state in (panel.CENTRE, panel.CORNER)
