"""Tests for writing and sending messages.

The property under test is the one that matters: ARC does not send anything until it
has been told to, twice. A sent message is the only thing ARC does that cannot be
undone — no kill switch retrieves it, and it went to another person.

Nothing here talks to Messages; the send is stubbed, because a test that sends real
messages to real people is not a test anyone wants running.
"""

from __future__ import annotations

import pytest

from arc.errors import ToolError
from arc.tools import messaging


@pytest.fixture
def sends(monkeypatch) -> list[tuple[str, str, str]]:
    """Capture what would have been sent, and send nothing."""
    captured: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        messaging, "_send", lambda kind, name, text: captured.append((kind, name, text))
    )
    monkeypatch.setattr(messaging, "resolve_recipient", lambda to: ("buddy", "Elvin"))
    # Stubbed too, or every test brings Messages to the front.
    monkeypatch.setattr(messaging, "show_conversation", lambda kind, name: True)
    return captured


@pytest.fixture
def gate_on(monkeypatch) -> None:
    monkeypatch.setattr(messaging, "_requires_confirmation", lambda: True)


# ── Not without permission ──────────────────────────────────────────────────────


def test_the_first_call_does_not_send(sends, gate_on) -> None:
    result = messaging.send_message(to="Elvin", text="hello")
    assert sends == [], "a message was sent without permission"
    assert "NOT SENT" in result


def test_the_first_call_reports_exactly_what_would_go(sends, gate_on) -> None:
    """The user is approving a specific message to a specific person, so both have to
    be in front of them before they say yes."""
    result = messaging.send_message(to="Elvin", text="see you at six")
    assert "Elvin" in result
    assert "see you at six" in result


def test_confirming_sends_it(sends, gate_on) -> None:
    messaging.send_message(to="Elvin", text="hello", confirm=True)
    assert sends == [("buddy", "Elvin", "hello")]


def test_an_empty_message_is_refused(sends, gate_on) -> None:
    with pytest.raises(ToolError):
        messaging.send_message(to="Elvin", text="   ")
    assert sends == []


# ── The gate is a setting, not a rule ───────────────────────────────────────────


def test_turning_the_setting_off_sends_on_the_first_call(sends, monkeypatch) -> None:
    """It has to remain the owner's decision. A safeguard nobody can switch off is a
    choice taken away from the person whose messages these are."""
    monkeypatch.setattr(messaging, "_requires_confirmation", lambda: False)
    messaging.send_message(to="Elvin", text="hello")
    assert sends == [("buddy", "Elvin", "hello")]


def test_the_gate_defaults_to_on() -> None:
    from arc.config import Config

    assert Config.load().get("policy.confirmation.require_for_sending") is True


def test_a_broken_config_does_not_mean_just_send(monkeypatch) -> None:
    """Failing open here would send messages because a YAML file was malformed."""
    from arc.config import Config

    def explode(*_args, **_kwargs):
        raise RuntimeError("bad config")

    monkeypatch.setattr(Config, "load", staticmethod(explode))
    assert messaging._requires_confirmation() is True


# ── Recipients ──────────────────────────────────────────────────────────────────


def test_an_unknown_recipient_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(messaging, "_osascript", lambda script: "")
    with pytest.raises(ToolError, match="nobody called"):
        messaging.resolve_recipient("Nobody")


def test_an_ambiguous_recipient_asks_which(monkeypatch) -> None:
    """Sending to the wrong person is exactly as unrecoverable as sending the wrong
    message, so a guess is not acceptable here."""
    monkeypatch.setattr(
        messaging, "_osascript", lambda script: "Sam Reed, Sam Turner" if "buddy" in script else ""
    )
    with pytest.raises(ToolError, match="matches several"):
        messaging.resolve_recipient("Sam")


def test_an_exact_name_wins_over_a_longer_one(monkeypatch) -> None:
    monkeypatch.setattr(
        messaging, "_osascript", lambda script: "Sam, Sammy Jones" if "buddy" in script else ""
    )
    assert messaging.resolve_recipient("Sam") == ("buddy", "Sam")


def test_an_empty_recipient_is_refused() -> None:
    with pytest.raises(ToolError):
        messaging.resolve_recipient("  ")


def test_quotes_in_a_message_cannot_break_out_of_the_script(sends, gate_on) -> None:
    """The text goes into an AppleScript string; an unescaped quote would change what
    the script does rather than what it says."""
    messaging.send_message(to="Elvin", text='say "hi" \\ ok', confirm=True)
    assert sends[0][2] == 'say "hi" \\ ok'


def test_sending_is_mutating_and_reachable_by_voice() -> None:
    from arc.config import Config
    from arc.tools import registry

    assert registry.get("send_message").mutating
    assert "send_message" in set(Config.load().section("voice").get("live_tools") or [])


# ── Resolving people ────────────────────────────────────────────────────────────


def test_the_same_person_on_two_services_is_not_ambiguous(monkeypatch) -> None:
    """A contact is a separate buddy on iMessage and on SMS, so the name comes back
    twice. Read as an ambiguity, every exact match was bounced back as "which one?"."""
    monkeypatch.setattr(
        messaging, "_osascript", lambda script: "Elvin, Elvin" if "buddy" in script else ""
    )
    assert messaging.resolve_recipient("Elvin") == ("buddy", "Elvin")


def test_a_person_wins_over_a_group_that_merely_mentions_them(monkeypatch) -> None:
    """ "Elvin" is a person; "Elvin's crew pt 2" is a group containing the word."""

    def fake(script: str) -> str:
        return "Elvin" if "buddy" in script else "Elvin crew pt 2"

    monkeypatch.setattr(messaging, "_osascript", fake)
    assert messaging.resolve_recipient("Elvin") == ("buddy", "Elvin")


# ── Opening the app ─────────────────────────────────────────────────────────────


def test_the_conversation_is_opened_before_drafting(sends, gate_on, monkeypatch) -> None:
    """Sending invisibly means approving a message you cannot look at, in a thread you
    cannot see."""
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        messaging, "show_conversation", lambda kind, name: bool(shown.append((kind, name))) or True
    )
    messaging.send_message(to="Elvin", text="hi")
    assert shown == [("buddy", "Elvin")]


def test_opening_messages_is_reachable_and_mutating() -> None:
    from arc.config import Config
    from arc.tools import registry

    assert registry.get("open_messages").mutating
    assert "open_messages" in set(Config.load().section("voice").get("live_tools") or [])
