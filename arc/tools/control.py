"""Taking and releasing control of the mouse and keyboard, as tools.

Distinct from :mod:`arc.tools.camera`, and the distinction matters: **this** is ARC
driving the machine itself, with the blue glow up and the abort phrase live. Camera
gestures are the opposite arrangement — the user's own hand drives, ARC only watches.
Neither is a way of doing the other.

Control is deliberately not acquired implicitly by the first click. ``session.require()``
refuses a synthetic event when no session is open, and the fix is to call
:func:`start_screen_control`, which puts the indicator on screen *before* anything
moves. An agent that could quietly begin driving would defeat the one guarantee the
indicator exists to make.
"""

from __future__ import annotations

from arc.control import session as control_session
from arc.errors import ArcError, ToolError
from arc.log import get_logger
from arc.tools.registry import tool

_log = get_logger(__name__)


@tool(category="input", mutating=True)
def start_screen_control(reason: str = "asked to control the screen") -> str:
    """Take control of this Mac's mouse and keyboard so ARC can operate it directly.
    Call this for "control my screen", "take over my screen", "use your screen control
    features", "control the computer", or "do it yourself" — and before any click,
    drag, scroll or keystroke, which all refuse until this has been called. This is ARC
    driving the machine and is NOT the camera feature: no camera is involved, and
    enable_camera_gestures is a different thing that lets the user's hands steer.

    A blue glow appears around every display for as long as control is held, and it ends
    the moment the user moves the physical mouse.

    Args:
        reason: Short description of what the control is for, shown in the audit log.
    """
    existing = control_session.current()
    if existing is not None and existing.state.active:
        return f"already controlling the screen ({existing.state.reason})"

    try:
        control_session.start(reason=reason)
    except ArcError as exc:
        raise ToolError(f"could not take control of the screen: {exc}") from exc

    _log.info("screen control started", extra={"reason": reason})
    return (
        f"screen control on — {reason}. The blue glow is up on every display. "
        "Mouse and keyboard tools will now work; move the physical mouse to take it back."
    )


@tool(category="input", mutating=True)
def stop_screen_control() -> str:
    """Give the mouse and keyboard back and clear the blue glow.

    Call this for "stop controlling my screen", "give me back control", "release the
    mouse", or once a task needing direct control is finished.
    """
    session = control_session.current()
    if session is None or not session.state.active:
        return "ARC is not controlling the screen"

    held = session.state.to_dict().get("held_seconds", 0.0)
    control_session.stop("asked to stop")
    _log.info("screen control stopped", extra={"held_seconds": held})
    return f"screen control off after {held}s — the mouse and keyboard are yours"


@tool(category="input")
def screen_control_status() -> str:
    """Report whether ARC currently has control of the mouse and keyboard."""
    session = control_session.current()
    if session is None or not session.state.active:
        return "ARC is not controlling the screen"
    state = session.state.to_dict()
    return f"ARC has screen control ({state['reason']}), held {state['held_seconds']}s"
