"""Tests for the shared conversation store.

Threads are what every front end reads: the web UI and the desktop panel both go through
this, which is the only reason they show the same history. Nothing here binds a port.
"""

from __future__ import annotations

import json

import pytest

from arc.errors import ArcError
from arc.interface import conversations


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test gets its own ~/.arc so none of them touch the real one."""
    monkeypatch.setenv("ARC_HOME", str(tmp_path))
    return tmp_path


def _thread(cid="c-1", **extra):
    return {"id": cid, "title": "A thread", "turns": [{"role": "user", "content": "hi"}], **extra}


def test_a_saved_conversation_comes_back() -> None:
    conversations.save(_thread())
    record = conversations.load("c-1")
    assert record is not None
    assert record["title"] == "A thread"
    assert record["turns"][0]["content"] == "hi"


def test_missing_conversations_are_none_not_errors() -> None:
    assert conversations.load("nope") is None


def test_saving_fills_in_what_the_client_omitted() -> None:
    record = conversations.save({"id": "c-2", "turns": []})
    assert record["title"] == "New conversation"
    assert record["origin"] == "web"
    assert record["updated"] > 0


def test_origin_is_recorded_so_clients_can_be_told_apart() -> None:
    """The web UI needs this to show which threads came from the desktop panel."""
    conversations.save(_thread("c-3", origin="desktop"))
    assert conversations.load("c-3")["origin"] == "desktop"


def test_listing_is_newest_first_and_carries_no_transcripts() -> None:
    """A sidebar needs titles, not every turn of every thread."""
    conversations.save(_thread("c-old", updated=1000))
    conversations.save(_thread("c-new", updated=2000))

    items = conversations.listing()
    assert [item["id"] for item in items] == ["c-new", "c-old"]
    assert "turns" in items[0] and isinstance(items[0]["turns"], int)
    assert "content" not in json.dumps(items)


def test_deleting_reports_whether_it_existed() -> None:
    conversations.save(_thread("c-4"))
    assert conversations.delete("c-4") is True
    assert conversations.delete("c-4") is False


# --- the parts that stop a client doing damage -------------------------------------


@pytest.mark.parametrize("cid", ["../escape", "a/b", "", "x" * 65, "has space", "..", "a.b"])
def test_ids_that_could_escape_the_directory_are_refused(cid: str) -> None:
    """Ids come from clients, so an id like `../../memory` must never become a path."""
    with pytest.raises(ArcError):
        conversations.load(cid)


def test_an_escaping_id_cannot_be_written_either(_isolated_home) -> None:
    with pytest.raises(ArcError):
        conversations.save({"id": "../pwned", "turns": []})
    assert not (_isolated_home.parent / "pwned.json").exists()


def test_turns_must_be_a_list() -> None:
    with pytest.raises(ArcError):
        conversations.save({"id": "c-5", "turns": "not a list"})


def test_absurdly_long_conversations_are_refused() -> None:
    with pytest.raises(ArcError):
        conversations.save({"id": "c-6", "turns": [{}] * (conversations.MAX_TURNS + 1)})


def test_a_write_never_leaves_a_half_file_behind(_isolated_home) -> None:
    """Written to a scratch file and renamed, so a concurrent reader sees old or new.

    Two front ends now write to this directory; a reader arriving mid-write must not get
    a truncated file.
    """
    conversations.save(_thread("c-7"))
    leftovers = list((_isolated_home / "conversations").glob("*.tmp"))
    assert not leftovers, f"temporary files left behind: {leftovers}"


def test_an_unreadable_file_does_not_break_the_listing(_isolated_home) -> None:
    conversations.save(_thread("c-good"))
    (_isolated_home / "conversations" / "c-bad.json").write_text("{ truncated", encoding="utf-8")

    items = conversations.listing()
    assert [item["id"] for item in items] == ["c-good"]
