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
    for name in ("chat.js", "memory.js", "app.js", "api.js"):
        body = (SITE / "js" / name).read_text(encoding="utf-8")
        for pattern in banned:
            assert pattern not in body, f"{name} uses {pattern} on untrusted text"


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
