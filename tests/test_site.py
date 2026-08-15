"""Tests for the arc.ai web build.

Two things are worth pinning here. The first is that the site stays *static* — what gets
deployed to GitHub Pages is the contents of ``web/``, and a stray absolute path or an
external CDN link is the kind of thing that works locally and 404s in production.

The second is that this build stays separate from the ARC application. The whole point
of the new front end is that `ARC`, `arc serve`, and ``arc/interface/webui/`` are
untouched, and a test is a cheaper way to keep that true than remembering it.

Nothing here binds a port or loads a model.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "web"
INDEX = SITE / "index.html"


def test_the_site_exists() -> None:
    assert INDEX.is_file(), "web/index.html is missing"
    assert (SITE / "styles.css").is_file()
    assert (SITE / "js" / "app.js").is_file()


def test_the_site_has_no_build_step() -> None:
    """Hand-written ES modules, like the rest of the repo. No node toolchain."""
    for artefact in ("package.json", "node_modules", "vite.config.js", "webpack.config.js"):
        assert not (SITE / artefact).exists(), f"{artefact} would introduce a build step"


def test_every_asset_reference_is_relative() -> None:
    """GitHub Pages serves from a subpath, where a leading `/` resolves to the wrong root.

    `/api` is exempt: it is a request to the backend at runtime, not an asset fetched at
    load time, and it is configurable precisely because the deployed page needs a
    different value.
    """
    html = INDEX.read_text(encoding="utf-8")
    for match in re.finditer(r'(?:src|href)="([^"]+)"', html):
        ref = match.group(1)
        if ref.startswith(("#", "data:", "http://", "https://", "mailto:")):
            continue
        assert not ref.startswith("/"), f"absolute asset path breaks on Pages: {ref}"


def test_nothing_is_loaded_from_a_third_party() -> None:
    """The whole point is local-first; a CDN font would phone home on every load."""
    html = INDEX.read_text(encoding="utf-8")
    for match in re.finditer(r'(?:src|href)="(https?://[^"]+)"', html):
        pytest.fail(f"external resource: {match.group(1)}")


def test_chat_does_not_ask_arc_to_speak() -> None:
    """ARC synthesises speech by default; this build has no voice.

    Without `speak: false` every reply would be spoken aloud by the machine running the
    server, which is both wrong and hard to trace back to a web page.
    """
    api = (SITE / "js" / "api.js").read_text(encoding="utf-8")
    assert "speak: false" in api


def test_rendering_never_uses_innerhtml_for_model_output() -> None:
    """Replies and stored memories are untrusted text and are inserted as text nodes.

    Matched as property access (``.innerHTML``) rather than the bare word, so the
    comments explaining this rule do not trip the rule.
    """
    banned = (".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write")
    for path in sorted((SITE / "js").glob("*.js")):
        body = path.read_text(encoding="utf-8")
        for pattern in banned:
            assert pattern not in body, f"{path.name} uses {pattern} on untrusted text"


def test_links_in_replies_are_restricted_to_http() -> None:
    """A `javascript:` href in a model reply must never become a live link."""
    body = (SITE / "js" / "markdown.js").read_text(encoding="utf-8")
    assert "^https?:\\/\\//i" in body or "/^https?:\\/\\//i" in body


def test_there_is_a_stop_control() -> None:
    """Generation runs at ~14 tok/s; being unable to interrupt it is the difference
    between a conversation and waiting."""
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="stop"' in html
    chat = (SITE / "js" / "chat.js").read_text(encoding="utf-8")
    assert "AbortController" in chat
    assert "abort()" in chat


def test_the_system_prompt_keeps_arcs_provenance_guidance() -> None:
    """Sending `system` replaces ARC's default, including a load-bearing instruction.

    Memories arrive with markers like `[episodic, 2026-07-30]`, and without the
    instruction the model copies them into its replies — which are then stored and
    recalled, compounding each turn. Overriding `system` without carrying this forward
    silently reintroduces a bug that took a format change to fix the first time.
    """
    chat = (SITE / "js" / "chat.js").read_text(encoding="utf-8")
    assert "never copy their bracketed" in chat
    assert "provenance markers into your reply" in chat


def test_conversations_are_persisted() -> None:
    """A reload must not lose the thread you are in the middle of."""
    store = (SITE / "js" / "store.js").read_text(encoding="utf-8")
    assert "localStorage" in store
    for fn in ("export function create", "export function remove", "export function rename"):
        assert fn in store, f"store.js is missing {fn}"


def test_editing_a_message_branches_rather_than_overwrites() -> None:
    """The reply you already had is the thing you are usually comparing against.

    Overwriting it makes editing destructive, which is the opposite of why anyone edits.
    """
    store = (SITE / "js" / "store.js").read_text(encoding="utf-8")
    for fn in (
        "export function editTurn",
        "export function versionInfo",
        "export function useVersion",
    ):
        assert fn in store, f"store.js is missing {fn}"
    chat = (SITE / "js" / "chat.js").read_text(encoding="utf-8")
    assert "startEditing" in chat
    assert "addVersionSwitcher" in chat


def test_search_covers_inactive_branches() -> None:
    """A message you edited away from is still something you might go looking for."""
    store = (SITE / "js" / "store.js").read_text(encoding="utf-8")
    assert "export function search" in store
    body = store[store.index("export function search") :]
    assert "versions" in body, "search ignores branched-away turns"


def test_conversations_can_be_exported() -> None:
    store = (SITE / "js" / "store.js").read_text(encoding="utf-8")
    assert "export function toMarkdown" in store
    assert "export function toJSON" in store
    app = (SITE / "js" / "app.js").read_text(encoding="utf-8")
    assert "createObjectURL" in app
    assert "revokeObjectURL" in app, "object URLs must be released or the page leaks them"


def test_code_blocks_are_highlighted_without_a_library() -> None:
    """§7 treats dependencies as a liability; highlight.js is ~120 KB for a chat window."""
    assert (SITE / "js" / "highlight.js").is_file()
    markdown = (SITE / "js" / "markdown.js").read_text(encoding="utf-8")
    assert "from './highlight.js'" in markdown
    assert "highlight(code" in markdown

    highlight = (SITE / "js" / "highlight.js").read_text(encoding="utf-8")
    # Strings and comments are consumed whole, so a keyword inside a string is not
    # highlighted as one — the failure that makes naive highlighters look broken.
    assert "span('string'" in highlight
    assert "span('comment'" in highlight


# --- separation from the ARC application ------------------------------------------


def test_the_web_build_does_not_import_arc_at_module_scope() -> None:
    """`web/serve.py` must run from a plain checkout with no ARC installed.

    It reads ARC's endpoint file when it can, but that import is inside a function and
    guarded, so the dev server still starts when ARC is not importable.
    """
    source = (SITE / "serve.py").read_text(encoding="utf-8")
    module_level = [
        line for line in source.splitlines() if re.match(r"^(import|from)\s+arc\b", line)
    ]
    assert not module_level, f"module-level ARC import: {module_level}"


def test_the_dev_server_is_loopback_only() -> None:
    """It proxies to ARC, which can run shell commands. Same reasoning as the main server."""
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("arc_site_serve", SITE / "serve.py")
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules["arc_site_serve"] = module
    spec.loader.exec_module(module)

    assert module.BIND_HOST == "127.0.0.1"

    # The string literal, not the bare text: the module docstring explains *why* it does
    # not bind 0.0.0.0, and that explanation is worth keeping.
    source = (SITE / "serve.py").read_text(encoding="utf-8")
    assert '"0.0.0.0"' not in source
    assert "'0.0.0.0'" not in source


def test_the_arc_application_is_untouched() -> None:
    """The old UI, orb and all, still ships inside the package."""
    webui = ROOT / "arc" / "interface" / "webui"
    for name in ("index.html", "orb.js", "app.js", "state.js"):
        assert (webui / name).is_file(), f"arc/interface/webui/{name} went missing"


def test_the_launcher_still_starts_the_application_not_the_site() -> None:
    """`ARC` in a terminal must keep opening the app window, not the new web build."""
    launcher = (ROOT / "bin" / "ARC").read_text(encoding="utf-8")
    assert "-m arc ui" in launcher
    assert "web/serve.py" not in launcher
