"""Writing and sending messages.

Drafting and sending are deliberately the *same* tool called twice rather than two
tools, because the whole point is that the second call is a separate decision. The
first call works out exactly who would receive what and reports it; nothing has left
the machine. The second call, with ``confirm=True``, actually sends.

Why sending is treated differently from everything else ARC does: it is the one action
with no way back. A moved window can be moved again, a deleted file is in the trash, a
runaway agent is killable — a sent message is gone, and it went to another person.

The gate is a *setting*, ``policy.confirmation.require_for_sending``, not a rule welded
into the code. Turn it off and ARC sends on the first call. That is deliberate: a
safeguard nobody can switch off is a decision taken away from its owner, and this one
belongs to the person whose messages they are.

**Scripted, not clicked.** Messages is driven through its AppleScript interface rather
than by pointing at the screen. Driving the window was tried and is genuinely unsafe:
the sidebar collapses when the window is narrow, a person's name appears in the contact
card and the message history as well as the sidebar, and a click that lands a few
pixels wrong is followed by Select All and a burst of typing into whatever took focus.
Naming the recipient leaves nothing to aim at.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any

from arc.config import Config
from arc.errors import ToolError
from arc.log import get_logger
from arc.tools.registry import tool

_log = get_logger(__name__)

#: Long enough for Messages to answer, short enough that a wedged Messages does not
#: hold a voice turn open indefinitely.
_SCRIPT_TIMEOUT = 20.0


def _requires_confirmation() -> bool:
    """Whether sending needs a second, explicit call. A setting, not a rule."""
    try:
        return bool(Config.load().get("policy.confirmation.require_for_sending", True))
    except Exception:  # pragma: no cover - a broken config must not mean "just send"
        return True


def _osascript(script: str) -> str:
    """Run one AppleScript and return its output."""
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=_SCRIPT_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError("Messages did not respond") from exc

    if result.returncode != 0:
        error = result.stderr.strip()
        if "-1743" in error or "not allowed" in error.lower():
            raise ToolError(
                "macOS is blocking ARC from controlling Messages. Allow it under "
                "System Settings > Privacy & Security > Automation."
            )
        raise ToolError(f"Messages refused: {error or 'unknown error'}")
    return result.stdout.strip()


def _split(raw: str) -> list[str]:
    """AppleScript returns lists as comma-separated text."""
    return [
        part.strip() for part in raw.split(",") if part.strip() and part.strip() != "missing value"
    ]


def resolve_recipient(to: str) -> tuple[str, str]:
    """Work out who ``to`` means, as ``(kind, exact name)``.

    ``kind`` is ``buddy`` for a person and ``chat`` for a group. Both are checked
    because they are addressed differently, and a group has a name where a one-to-one
    conversation usually does not.
    """
    wanted = to.strip()
    if not wanted:
        raise ToolError("no recipient given")
    quoted = wanted.replace('"', '\\"')

    people = _split(
        _osascript(
            f'tell application "Messages" to get name of every buddy whose name contains "{quoted}"'
        )
    )
    groups = _split(
        _osascript(
            f'tell application "Messages" to get name of every chat whose name contains "{quoted}"'
        )
    )

    # One person can be several buddies — the same contact on iMessage and on SMS —
    # so the same name comes back more than once. Left as duplicates it looks like an
    # ambiguity and every exact match gets bounced back as "which one?".
    exact = list(dict.fromkeys(n for n in people if n.lower() == wanted.lower()))
    if len(exact) == 1:
        return "buddy", exact[0]

    candidates = [("buddy", n) for n in people] + [("chat", n) for n in groups]
    unique = list(dict.fromkeys(candidates))
    if not unique:
        raise ToolError(
            f"nobody called {to!r} in Messages. Check the spelling, or give the name "
            "exactly as it appears in the conversation list."
        )
    if len(unique) > 1:
        names = ", ".join(sorted({name for _kind, name in unique})[:8])
        raise ToolError(f"{to!r} matches several conversations: {names}. Which one?")
    return unique[0]


def _address_of(kind: str, name: str) -> str:
    """The ``imessage:`` target for a conversation — a handle, or a group's GUID."""
    quoted = name.replace('"', '\\"')
    if kind == "buddy":
        handle = _osascript(
            f'tell application "Messages" to get handle of first buddy whose name is "{quoted}"'
        )
        return handle
    guid = _osascript(
        f'tell application "Messages" to get id of first chat whose name is "{quoted}"'
    )
    return f"chat?guid={guid}"


def _window_title() -> str:
    """The Messages window title, which is the conversation currently shown."""

    try:
        tree = _messages_tree()
    except ToolError:
        return ""
    return (
        next(
            (child.label for child in tree.children if child.role == "AXWindow"),
            "",
        )
        or ""
    )


def show_conversation(kind: str, name: str) -> bool:
    """Bring Messages up with this conversation open. True if it visibly switched.

    Two steps, and both are needed. ``open -a`` gets Messages running and frontmost;
    the ``imessage:`` URL selects the conversation. Sending the URL at an app that is
    not up yet brings the app forward but leaves whatever conversation was last open,
    which is why this checks the window title and tries once more.
    """
    subprocess.run(
        ["/usr/bin/open", "-a", "Messages"],
        capture_output=True,
        timeout=_SCRIPT_TIMEOUT,
        check=False,
    )

    address = _address_of(kind, name)
    if not address:
        return False

    wanted = name.split(",")[0].strip().lower()
    for _attempt in range(2):
        subprocess.run(
            ["/usr/bin/open", f"imessage://{address}"],
            capture_output=True,
            timeout=_SCRIPT_TIMEOUT,
            check=False,
        )
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if wanted and wanted in _window_title().lower():
                return True
            time.sleep(0.25)
    return False


def _send(kind: str, name: str, text: str) -> None:
    escaped_name = name.replace('"', '\\"')
    escaped_text = text.replace("\\", "\\\\").replace('"', '\\"')
    if kind == "buddy":
        script = (
            f'tell application "Messages" to send "{escaped_text}" '
            f'to (first buddy whose name is "{escaped_name}")'
        )
    else:
        script = (
            f'tell application "Messages" to send "{escaped_text}" '
            f'to (first chat whose name is "{escaped_name}")'
        )
    _osascript(script)


@tool(category="messaging", mutating=True)
def send_message(to: str, text: str, confirm: bool = False) -> str:
    """Write a message to someone in Messages, and send it only once permitted.

    Call this for "message Caylin", "text my mum", "send a message to ...". Called
    normally it works out exactly who would receive it and reports the message back
    WITHOUT sending — read that back to the user and ask whether to send it.

    Only when they say yes, call this again with the same `to` and `text` plus
    confirm=true. Never pass confirm=true on the user's original request: sending is
    the one thing ARC cannot undo, so it takes a separate yes.

    Args:
        to: Who to message, as their name appears in Messages.
        text: The message to send.
        confirm: True only after the user has approved this exact message.
    """
    if not text.strip():
        raise ToolError("refusing to send an empty message")

    kind, recipient = resolve_recipient(to)
    described = recipient if kind == "buddy" else f"the group {recipient}"

    # Open the conversation before doing anything else, so the message is drafted and
    # sent somewhere the user can see. Sending invisibly works, but it means approving
    # a message you cannot look at, in a thread you cannot see.
    shown = show_conversation(kind, recipient)

    if _requires_confirmation() and not confirm:
        _log.info("message awaiting permission", extra={"to": recipient, "shown": shown})
        where = "It is open on screen." if shown else "(Messages is open, but on another thread.)"
        return (
            f'Ready to send to {described}: "{text}"\n{where}\n'
            "NOT SENT. Read this back to the user and ask whether to send it. If they "
            "agree, call send_message again with the same arguments and confirm=true."
        )

    _send(kind, recipient, text)
    _log.info("message sent", extra={"to": recipient, "confirmed": confirm})
    return f'Sent to {described}: "{text}"'


@tool(category="messaging", mutating=True)
def open_messages(to: str = "") -> str:
    """Open the Messages app, optionally straight to someone's conversation.

    Call this for "open Messages", "open my messages", "show me my texts", or "pull up
    my conversation with Elvin". Sends nothing — it only puts the thread on screen.

    Args:
        to: Whose conversation to open. Omit to just open Messages.
    """
    if not to.strip():
        from arc.tools.apps import open_app

        return open_app("Messages")

    kind, recipient = resolve_recipient(to)
    described = recipient if kind == "buddy" else f"the group {recipient}"
    if show_conversation(kind, recipient):
        return f"Messages is open on the conversation with {described}"
    return (
        f"Messages is open, but it did not switch to {described} — "
        "the conversation may not exist yet."
    )


@tool(category="messaging")
def list_conversations(query: str = "") -> str:
    """List the people and groups that can be messaged.

    Use to check how a name is really spelled before writing to them.

    Args:
        query: Only list names containing this. Omit for all.
    """
    quoted = query.replace('"', '\\"')
    clause = f' whose name contains "{quoted}"' if query else ""
    people = _split(_osascript(f'tell application "Messages" to get name of every buddy{clause}'))
    groups = _split(_osascript(f'tell application "Messages" to get name of every chat{clause}'))

    if not people and not groups:
        return f"nothing matching {query!r}" if query else "no conversations found"

    lines = []
    if people:
        lines.append(f"people ({len(people)}):")
        lines += [f"  {name}" for name in sorted(set(people))[:40]]
    if groups:
        lines.append(f"groups ({len(groups)}):")
        lines += [f"  {name}" for name in sorted(set(groups))[:20]]
    return "\n".join(lines)


@tool(category="messaging")
def read_conversation(to: str, limit: int = 12) -> str:
    """Read the recent messages in a conversation, to see what is being replied to.

    Args:
        to: Whose conversation to read.
        limit: How many of the most recent messages to return.
    """
    from arc.vision import accessibility as ax

    _kind, recipient = resolve_recipient(to)

    tree = _messages_tree()
    bubbles = [
        element.label
        for element in ax.actionable_elements(tree)
        if element.role == "AXGroup" and "," in element.label
    ]
    if not bubbles:
        return (
            f"{recipient} is a known conversation, but no messages are visible. "
            "Open it in Messages to read it."
        )
    return "\n".join(f"  {line}" for line in bubbles[-limit:])


def _messages_tree() -> Any:
    """The Messages accessibility tree, read by pid rather than by frontmost.

    Reading "the frontmost app" is wrong here: anything that steals focus — a
    notification, or another display — would have this describing a different
    application entirely.
    """
    import AppKit

    from arc.vision import accessibility as ax

    workspace = AppKit.NSWorkspace.sharedWorkspace()
    pid = next(
        (
            int(app.processIdentifier())
            for app in workspace.runningApplications()
            if str(app.localizedName() or "") == "Messages"
        ),
        None,
    )
    if pid is None:
        raise ToolError("Messages is not running")
    return ax.read_tree(pid)
