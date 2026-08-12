"""Tests for launching and resolving applications.

Opening an app is the first step of nearly every screen-control task, and until these
tools existed there was no way to do it at all — "open Chrome" could only be attempted
by driving Spotlight with keystrokes.

Resolution is what is tested here rather than launching: it is the part with judgement
in it, and a wrong answer opens the wrong application.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arc.errors import ToolError
from arc.tools.apps import _abbreviates, resolve_app

INSTALLED = {
    "Google Chrome": Path("/Applications/Google Chrome.app"),
    "Messages": Path("/System/Applications/Messages.app"),
    "Mail": Path("/System/Applications/Mail.app"),
    "MailMaven": Path("/Applications/MailMaven.app"),
    "Visual Studio Code": Path("/Applications/Visual Studio Code.app"),
    "System Settings": Path("/System/Applications/System Settings.app"),
}


@pytest.fixture(autouse=True)
def installed(monkeypatch) -> None:
    from arc.tools import apps

    monkeypatch.setattr(apps, "installed_apps", lambda: INSTALLED)


def test_a_common_short_name_finds_the_app() -> None:
    assert resolve_app("chrome")[0] == "Google Chrome"


def test_an_exact_name_beats_a_longer_one_containing_it() -> None:
    """Otherwise "mail" is ambiguous with MailMaven, or worse, silently opens it."""
    assert resolve_app("Mail")[0] == "Mail"


def test_case_and_the_app_suffix_do_not_matter() -> None:
    assert resolve_app("MESSAGES.app")[0] == "Messages"


def test_an_abbreviation_spanning_words_resolves() -> None:
    """ "vs code" is Visual Studio Code — no substring search finds that."""
    assert resolve_app("vs code")[0] == "Visual Studio Code"
    assert resolve_app("sys settings")[0] == "System Settings"


def test_an_ambiguous_name_asks_rather_than_guesses() -> None:
    """Opening the wrong application is only noticed after it has acted."""
    with pytest.raises(ToolError, match="matches several"):
        resolve_app("ma")


def test_an_unknown_app_suggests_something() -> None:
    with pytest.raises(ToolError) as caught:
        resolve_app("chrom3z")
    assert "no application named" in str(caught.value)


def test_an_empty_name_is_refused() -> None:
    with pytest.raises(ToolError):
        resolve_app("   ")


@pytest.mark.parametrize(
    ("query", "name", "expected"),
    [
        ("vscode", "Visual Studio Code", True),
        ("gchrome", "Google Chrome", True),
        ("asettings", "System Settings", False),
        ("zzz", "Messages", False),
    ],
)
def test_abbreviation_matching(query: str, name: str, expected: bool) -> None:
    assert _abbreviates(query, name) is expected


def test_opening_an_app_is_mutating() -> None:
    """It puts a window on screen, so --dry-run has to skip it."""
    from arc.tools import registry

    assert registry.get("open_app").mutating
    assert not registry.get("list_applications").mutating


def test_apps_are_reachable_by_voice() -> None:
    """Without these the voice session had no way to open anything."""
    from arc.config import Config

    allowed = set(Config.load().section("voice").get("live_tools") or [])
    assert {"open_app", "list_applications", "frontmost_app"} <= allowed
