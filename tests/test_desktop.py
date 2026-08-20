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


def test_the_panel_is_voice_only() -> None:
    """A typing mode was built and removed.

    The panel is a non-activating floating panel — the property that lets it appear over
    your editor without stealing focus — and that same property means its text field can
    never reliably take the keyboard. A composer you cannot click into is worse than no
    composer.
    """
    body = (UI / "panel.js").read_text(encoding="utf-8")
    assert "speak: true" in body, "the panel has no other way to answer you"
    assert "getElementById('input')" not in body
    assert "getElementById('composer')" not in body

    html = (UI / "index.html").read_text(encoding="utf-8")
    assert "<form" not in html
    assert "<input" not in html


def test_the_orb_reacts_to_the_microphone() -> None:
    """The server pushes `level` at ~30 Hz; without a listener the orb never moves.

    That was the bug where talking to ARC produced no visible change at all.
    """
    body = (UI / "panel.js").read_text(encoding="utf-8")
    assert "addEventListener('level'" in body
    assert "orb.setLevel(" in body

    orb = (UI / "orb.js").read_text(encoding="utf-8")
    assert "setLevel(level)" in orb
    # Fast attack, slow release — the orb should jump when you start talking and settle
    # gently, not chatter with the level packets.
    assert "this.level > this._level ? 14 : 4" in orb


def test_muting_changes_the_colour() -> None:
    """Mute is a live state you need to notice from across the desk, so it gets a hue."""
    orb = (UI / "orb.js").read_text(encoding="utf-8")
    assert "MUTED_RGB" in orb
    # A muted orb must not twitch to a level packet arriving before the mic closes.
    assert "if (this.muted) return;" in orb


def test_mute_is_not_derived_from_the_activity_string() -> None:
    """Live mode emits SPEAKING and IDLE continuously while you talk.

    When mute was folded into setActivity every one of those events un-muted the orb, so
    muting appeared to do nothing the moment anyone spoke.
    """
    orb = (UI / "orb.js").read_text(encoding="utf-8")
    assert "setMuted(muted)" in orb

    activity = orb[orb.index("  setActivity(activity) {") : orb.index("  setLevel(level)")]
    assert "this.muted" not in activity, "setActivity still writes the mute state"

    panel = (UI / "panel.js").read_text(encoding="utf-8")
    assert "orb.setMuted(" in panel


def test_the_animation_loop_survives_a_bad_frame() -> None:
    """rAF must be rescheduled before the work, not after.

    With the reschedule last, a single exception in step or draw killed the loop
    permanently and the orb froze mid-conversation.
    """
    orb = (UI / "orb.js").read_text(encoding="utf-8")
    body = orb[orb.index("  start() {") : orb.index("  stop() {")]
    reschedule = body.index("this._raf = requestAnimationFrame(frame);")
    assert reschedule < body.index("this._step(dt)"), "the loop dies on one bad frame"
    assert "catch (error)" in body


def test_point_colour_is_chosen_per_band_not_per_point() -> None:
    """Assigning fillStyle from a template string parses a CSS colour every time.

    At 1500 points a frame that is ~90,000 string allocations a second, which is what
    made the orb stutter and drop frames while the microphone was open.
    """
    orb = (UI / "orb.js").read_text(encoding="utf-8")
    assert "const BANDS" in orb
    points = orb[orb.index("  _points(cx, cy, radius) {") : orb.index("  _satelliteStack")]
    assert points.count("ctx.fillStyle") <= 2, "fillStyle is still set per point"


def test_the_centred_orb_sits_on_a_tinted_backdrop() -> None:
    """The tint follows the orb's own colour, so the points read against something."""
    orb = (UI / "orb.js").read_text(encoding="utf-8")
    assert "_backdrop(cx, cy, radius)" in orb
    backdrop = orb[orb.index("  _backdrop(cx, cy, radius) {") : orb.index("  _points(cx")]
    assert "this.muted ? MUTED_RGB : ACTIVE_RGB" in backdrop
    # Contained inside the cloud. An earlier version reached 2.6x the radius, clipped to
    # a hard edge along the top of the panel, and needed a ring to hide the seam.
    assert "2.6" not in backdrop


def test_satellites_are_driven_by_real_tool_calls() -> None:
    """A fixed three markers said the same thing whether one tool ran or five."""
    orb = (UI / "orb.js").read_text(encoding="utf-8")
    assert "setTools(categories)" in orb
    assert "CATEGORY_COLOURS" in orb

    activity = orb[orb.index("  setActivity(activity) {") : orb.index("  /** The tools running")]
    assert "this.tools" not in activity, "activity is still inventing markers"

    panel = (UI / "panel.js").read_text(encoding="utf-8")
    assert "tool_start" in panel
    assert "tool_end" in panel
    assert "orb.setTools(" in panel


def test_live_mode_reports_its_tool_calls() -> None:
    """Live dispatches tools itself; without this nothing downstream knows work is on."""
    source = (ROOT / "arc" / "voice" / "live.py").read_text(encoding="utf-8")
    assert "on_tool" in source
    assert "self._emit_tool(call_id, name, True)" in source
    # In a finally block: a tool that raises must still clear its marker, or the panel
    # shows work in progress that ended some time ago.
    finish = source.index("self._emit_tool(call_id, name, False)")
    assert "finally:" in source[finish - 220 : finish]

    server = (ROOT / "arc" / "interface" / "server.py").read_text(encoding="utf-8")
    assert "_push_tool" in server
    assert '"tool_start" if running else "tool_end"' in server


def test_the_microphone_stays_shut_until_playback_has_actually_finished() -> None:
    """The audio queue emptying is not the end of playback.

    `stream.write` returns once the device buffer accepts a chunk, not once the speaker
    has sounded it. Reopening the mic in that gap fed ARC its own closing words, which
    the Live API read as the user starting to speak — so it answered its own tail.
    """
    source = (ROOT / "arc" / "voice" / "live.py").read_text(encoding="utf-8")
    assert "ECHO_HANGOVER_SECONDS" in source
    gate = source[source.index("    def _hearing_itself") : source.index("    async def _send")]
    assert "_last_output_at" in gate, "the gate still reopens the instant the queue drains"
    assert "self._last_output_at = time.monotonic()" in source


def test_there_is_no_listening_caption() -> None:
    """The orb turning blue and moving already says it; a caption under it was noise."""
    panel = (UI / "panel.js").read_text(encoding="utf-8")
    assert "listening…" not in panel


def test_the_microphone_is_not_toggled_into_the_state_it_is_already_in() -> None:
    """`/voice/toggle` is a toggle, so asking for the state it already holds flips it.

    That is the bug where the mic appears to close the instant it opens.
    """
    body = (UI / "panel.js").read_text(encoding="utf-8")
    assert "if (want === listening) return;" in body


def test_live_mode_transcripts_are_not_posted_back() -> None:
    """In live mode Gemini answers out loud already; posting would start a second reply."""
    body = (UI / "panel.js").read_text(encoding="utf-8")
    assert "answersItself" in body

    # `ask()` must be reachable only from the branch where ARC is *not* already
    # answering. Checked structurally rather than as one literal line, so the guard can
    # be reshaped without the test reading as though it were deleted.
    branch = body[
        body.index("if (answersItself) {") : body.index("  });", body.index("if (answersItself) {"))
    ]
    assert "ask(" not in branch.split("} else {")[0], (
        "posts a transcript live mode is already answering"
    )
    assert "ask(text);" in branch


def test_assistant_speech_is_not_treated_as_something_you_said() -> None:
    """Routing by role is what stops ARC's reply being fed back in as a new question."""
    body = (UI / "panel.js").read_text(encoding="utf-8")
    assert "payload.role === 'assistant'" in body


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
