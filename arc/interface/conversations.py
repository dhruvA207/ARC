"""Conversation threads, shared by every ARC front end.

ARC's memory stores *facts* — every turn is written to it and is searchable — but it has
no notion of a thread you can reopen and continue. The web UI kept threads in the
browser's ``localStorage``, which works until there is a second front end: the desktop
panel cannot read a browser's storage, so the two would show different histories of the
same conversations.

So threads live here instead, under ``~/.arc/conversations/``, and every client reads and
writes them over HTTP. One file per conversation rather than one index file: two clients
saving different threads at the same moment then touch different files, and a corrupted
write costs one conversation instead of all of them.

Deliberately *not* in ``memory.db``. That database is the one irreplaceable thing ARC
owns (``docs/BACKUP.md``), its schema is about recall rather than transcripts, and a
chat client writing to it on every keystroke is a good way to find out what happens when
two processes hold a WAL-mode SQLite file open for writing.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from arc.errors import ArcError
from arc.log import get_logger
from arc.paths import arc_home

_log = get_logger(__name__)

#: Ids come from clients, so they are validated before ever touching a path. Without this
#: a POST with id "../../memory" writes wherever it likes.
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: A conversation is a transcript, not a database. Past this it is being misused, and the
#: limit is what stops one runaway client from filling the disk.
MAX_TURNS = 2000
MAX_BYTES = 2_000_000


def directory() -> Path:
    """Return the conversations directory, creating it on first use."""
    target = arc_home() / "conversations"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _path(cid: str) -> Path:
    if not _ID.match(cid):
        raise ArcError(f"invalid conversation id: {cid!r}")
    return directory() / f"{cid}.json"


def load(cid: str) -> dict[str, Any] | None:
    """Return one conversation, or None if it is not there."""
    target = _path(cid)
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # A half-written file should not take out the whole list.
        _log.warning("unreadable conversation", extra={"id": cid, "error": str(exc)})
        return None


def save(record: dict[str, Any]) -> dict[str, Any]:
    """Write a conversation, filling in what the client did not send."""
    cid = str(record.get("id") or "").strip()
    if not cid:
        raise ArcError("conversation id is required")

    turns = record.get("turns")
    if not isinstance(turns, list):
        raise ArcError("conversation turns must be a list")
    if len(turns) > MAX_TURNS:
        raise ArcError(f"conversation has more than {MAX_TURNS} turns")

    payload = {
        "id": cid,
        "title": str(record.get("title") or "New conversation")[:200],
        "turns": turns,
        "versions": record.get("versions") or {},
        "updated": int(record.get("updated") or time.time() * 1000),
        "origin": str(record.get("origin") or "web")[:32],
    }

    body = json.dumps(payload, ensure_ascii=False)
    if len(body.encode("utf-8")) > MAX_BYTES:
        raise ArcError("conversation is too large to store")

    target = _path(cid)
    # Written to a temporary file and moved into place: a reader that arrives mid-write
    # sees either the old file or the new one, never a truncated one. rename is atomic
    # within a filesystem, which ~/.arc always is.
    scratch = target.with_suffix(".json.tmp")
    scratch.write_text(body, encoding="utf-8")
    scratch.replace(target)
    return payload


def delete(cid: str) -> bool:
    """Remove a conversation. Returns whether it existed."""
    target = _path(cid)
    if not target.is_file():
        return False
    target.unlink()
    return True


def listing(limit: int = 200) -> list[dict[str, Any]]:
    """Return conversation summaries, newest first.

    Summaries rather than whole transcripts: a sidebar needs titles and timestamps, and
    sending every turn of every thread to draw a list is how that list gets slow.
    """
    items: list[dict[str, Any]] = []
    for target in directory().glob("*.json"):
        record = load(target.stem)
        if record is None:
            continue
        items.append(
            {
                "id": record["id"],
                "title": record.get("title", "New conversation"),
                "updated": record.get("updated", 0),
                "origin": record.get("origin", "web"),
                "turns": len(record.get("turns", [])),
            }
        )
    items.sort(key=lambda item: item["updated"], reverse=True)
    return items[:limit]
