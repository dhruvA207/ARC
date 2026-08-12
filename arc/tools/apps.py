"""Launching, focusing, and quitting applications.

Opening an app is the first step of almost every screen-control task, and until this
existed there was no way to do it. The alternatives were both bad: driving Spotlight
by keystroke is slow and fails whenever the first result is not the app you meant, and
the shell tool that could run ``open -a`` is deliberately kept away from the voice
session. So "open Chrome" simply could not be carried out.

Apps are resolved by **name, fuzzily**: people say "chrome", not "Google Chrome.app".
The match is deliberately ranked rather than first-hit, so "mail" finds Mail rather than
MailMaven, and an ambiguous name reports the candidates instead of guessing.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from arc.errors import ToolError
from arc.log import get_logger
from arc.tools.registry import tool

_log = get_logger(__name__)

#: Where macOS keeps applications. Utilities is listed separately because it is not
#: reached by the non-recursive scan of its parent.
_APP_DIRECTORIES = (
    Path("/Applications"),
    Path("/Applications/Utilities"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path.home() / "Applications",
)

#: How long to wait for a launched app to actually come to the front. Cold-starting a
#: big application is slow, and reporting success before it is on screen means the next
#: tool call reads the wrong window.
_LAUNCH_TIMEOUT = 15.0


def installed_apps() -> dict[str, Path]:
    """Every installed application, by display name."""
    found: dict[str, Path] = {}
    for directory in _APP_DIRECTORIES:
        if not directory.is_dir():
            continue
        try:
            entries = sorted(directory.iterdir())
        except OSError:  # pragma: no cover - unreadable directory
            continue
        for path in entries:
            if path.suffix == ".app":
                found.setdefault(path.stem, path)
    return found


def resolve_app(name: str) -> tuple[str, Path]:
    """Find the application someone means by ``name``.

    Ranked rather than first-match: an exact name beats a prefix, and a prefix beats a
    substring. Without that ordering "mail" could open anything that merely contains
    the word, which is the kind of wrong that is only noticed after it has acted.
    """
    wanted = name.strip().removesuffix(".app").lower()
    if not wanted:
        raise ToolError("no application name given")

    apps = installed_apps()
    exact = [n for n in apps if n.lower() == wanted]
    prefix = [n for n in apps if n.lower().startswith(wanted) and n not in exact]
    contains = [n for n in apps if wanted in n.lower() and n not in exact and n not in prefix]

    initials = [n for n in apps if _abbreviates(wanted, n) and n not in exact + prefix + contains]

    for tier in (exact, prefix, contains, initials):
        if len(tier) == 1:
            return tier[0], apps[tier[0]]
        if len(tier) > 1:
            raise ToolError(
                f"{name!r} matches several applications: {', '.join(sorted(tier)[:8])}. "
                "Ask which one, or give the full name."
            )

    close = sorted(n for n in apps if wanted[:3] and wanted[:3] in n.lower())[:8]
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    raise ToolError(f"no application named {name!r} is installed.{hint}")


def _abbreviates(query: str, name: str) -> bool:
    """Whether ``query`` is ``name`` with each word shortened to a prefix.

    "vs code" is Visual Studio Code — v + s + code — and no amount of substring matching
    finds it, because the abbreviation spans a word boundary. This is how people
    actually refer to applications, so it is worth one more matching tier.
    """
    words = name.lower().split()
    target = query.replace(" ", "")
    if not target:
        return False

    def consume(word_index: int, position: int) -> bool:
        if position == len(target):
            return True
        if word_index == len(words):
            return False
        if consume(word_index + 1, position):  # this word contributes nothing
            return True
        word = words[word_index]
        return any(
            target.startswith(word[:length], position)
            and consume(word_index + 1, position + length)
            for length in range(1, len(word) + 1)
        )

    return consume(0, 0)


def _frontmost() -> str:
    try:
        from arc.vision.accessibility import frontmost_application

        return frontmost_application()[1]
    except Exception:  # pragma: no cover - defensive
        return ""


@tool(category="apps", mutating=True)
def open_app(name: str) -> str:
    """Open an application, or bring it to the front if it is already running.

    Use this for "open Chrome", "launch Messages", "switch to Safari", "open my
    calendar" — anything that starts with getting an app on screen. Always call this
    before trying to click or type inside an app, so the screen-reading tools are
    looking at the right window.

    The name is matched loosely, so "chrome" finds "Google Chrome".

    Args:
        name: The application's name, or enough of it to identify it.
    """
    resolved, path = resolve_app(name)

    result = subprocess.run(
        ["/usr/bin/open", "-a", str(path)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ToolError(f"could not open {resolved}: {result.stderr.strip() or 'unknown error'}")

    # Wait for it to actually be frontmost. Returning early means the next call reads
    # whatever window happened to still be in front.
    deadline = time.monotonic() + _LAUNCH_TIMEOUT
    while time.monotonic() < deadline:
        if _frontmost() == resolved:
            _log.info("opened app", extra={"app": resolved})
            return f"{resolved} is open and frontmost"
        time.sleep(0.25)

    front = _frontmost()
    _log.warning("app did not come to the front", extra={"app": resolved, "frontmost": front})
    return (
        f"{resolved} was launched but {front or 'something else'} is still frontmost. "
        "It may be slow to start, or waiting on a dialog."
    )


@tool(category="apps", mutating=True)
def quit_app(name: str) -> str:
    """Quit an application.

    Use for "close Chrome", "quit Messages". Asks the app to quit the ordinary way, so
    it can prompt about unsaved work rather than losing it.

    Args:
        name: The application's name, or enough of it to identify it.
    """
    resolved, _path = resolve_app(name)
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", f'tell application "{resolved}" to quit'],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ToolError(f"could not quit {resolved}: {result.stderr.strip() or 'unknown error'}")
    _log.info("quit app", extra={"app": resolved})
    return f"asked {resolved} to quit"


@tool(category="apps")
def list_applications(query: str = "") -> str:
    """List the applications installed on this Mac.

    Use when unsure what an app is really called, or whether it is installed at all,
    before trying to open it.

    Args:
        query: Only list applications whose name contains this. Omit for all.
    """
    apps = sorted(installed_apps())
    if query:
        wanted = query.lower()
        apps = [name for name in apps if wanted in name.lower()]
        if not apps:
            return f"no installed application matches {query!r}"
    return f"{len(apps)} applications:\n" + "\n".join(f"  {name}" for name in apps)


@tool(category="apps")
def frontmost_app() -> str:
    """Report which application is currently in front.

    Worth checking before reading the screen: the screen-reading tools describe the
    frontmost application, so a surprising answer here explains a surprising answer
    there.
    """
    name = _frontmost()
    return f"frontmost application: {name}" if name else "could not determine the frontmost app"
