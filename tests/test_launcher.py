"""Tests for the `ARC` terminal launcher.

The script itself is shell, so what is checked here is the contract around it: that it
exists, that it is runnable, and that bare `ARC` opens the app rather than printing
argparse usage — the failure that would make typing `ARC` feel broken.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "bin" / "ARC"
SETUP = ROOT / "bin" / "arc-setup"


def test_the_launcher_exists_and_is_executable() -> None:
    assert LAUNCHER.is_file(), "bin/ARC is missing"
    assert LAUNCHER.stat().st_mode & stat.S_IXUSR, "bin/ARC is not executable"


def test_bare_arc_opens_the_app() -> None:
    """No arguments must mean "launch", not "show me the usage message"."""
    body = LAUNCHER.read_text(encoding="utf-8")
    assert "-m arc ui" in body


def test_it_does_nothing_but_launch() -> None:
    """It is a launcher, not a second front door to the CLI."""
    assert '-m arc "$@"' not in LAUNCHER.read_text(encoding="utf-8")


def test_it_launches_detached_so_the_terminal_stays_usable() -> None:
    """Typing `ARC` must give the prompt straight back, the way `code` does.

    A foreground `exec` would hold the terminal until ARC exits, which is the thing
    that made the launcher feel like it had taken the window hostage.
    """
    body = LAUNCHER.read_text(encoding="utf-8")
    assert "nohup" in body
    assert "exec " not in body
    assert 'nohup "$PY" -u -m arc ui' in body


def test_it_does_not_start_a_second_copy() -> None:
    """Two windows would fight over the port, the microphone, and input control."""
    assert "pgrep" in LAUNCHER.read_text(encoding="utf-8")


def test_it_resolves_its_own_repo_through_a_symlink() -> None:
    """It is invoked via ~/.local/bin/ARC, so a relative path would find nothing."""
    body = LAUNCHER.read_text(encoding="utf-8")
    assert "BASH_SOURCE" in body
    assert "readlink" in body


@pytest.mark.skipif(sys.platform != "darwin", reason="the launcher is macOS-only")
def test_arguments_are_refused_rather_than_ignored(tmp_path: Path) -> None:
    """macOS filesystems are case-insensitive, so `arc doctor` reaches this script too.

    Silently opening the window instead would look like the CLI had broken, so it says
    what it is and points at where the CLI actually lives.
    """
    result = subprocess.run(
        [str(LAUNCHER), "doctor"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
        env={**os.environ, "ARC_HOME": str(tmp_path / "home")},
    )
    assert result.returncode != 0
    assert "only launches ARC" in result.stderr
    assert ".venv/bin/arc doctor" in result.stderr


# --- bin/arc-setup ---------------------------------------------------------------
#
# The setup script is the contract between the two Macs: development happens on one and
# ARC runs on the other, so `git pull && bin/arc-setup` has to be the whole story. What
# is checked here is that the contract stays intact, not that pip works.


def test_the_setup_script_exists_and_is_executable() -> None:
    assert SETUP.is_file(), "bin/arc-setup is missing"
    assert SETUP.stat().st_mode & stat.S_IXUSR, "bin/arc-setup is not executable"


def test_setup_installs_the_all_extra() -> None:
    """`[all]` composes every other extra, so it is the one name setup must use.

    Installing a hand-written subset here is how the two machines drift apart.
    """
    assert "[all]" in SETUP.read_text(encoding="utf-8")


def test_setup_applies_the_activate_prompt_patch() -> None:
    """Homebrew's activate template doubles the prompt parens; the patch lives in .venv.

    `.venv` is gitignored, so the fix is lost on every recreate. It has to be applied by
    the script rather than documented, or it silently stops happening.
    """
    body = SETUP.read_text(encoding="utf-8")
    assert 'PS1="("' in body, "no longer matches the broken line it is meant to rewrite"
    assert "VIRTUAL_ENV_PROMPT" in body
    assert "--prompt ARC" in body


def test_setup_pins_python_312() -> None:
    """pyproject requires >=3.12; the venv must not drift to whatever `python3` means."""
    assert "python3.12" in SETUP.read_text(encoding="utf-8")


def test_setup_rejects_unknown_arguments() -> None:
    result = subprocess.run([str(SETUP), "--wat"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_every_declared_extra_is_reachable_from_all() -> None:
    """`all` is what `arc-setup` installs, so an extra missing from it never ships.

    `llamacpp` is deliberately excluded — MLX is the backend on Apple Silicon.
    """
    import tomllib

    with (ROOT / "pyproject.toml").open("rb") as handle:
        extras = tomllib.load(handle)["project"]["optional-dependencies"]

    assert len(extras["all"]) == 1, "`all` should be a single self-referential entry"
    composed = extras["all"][0]
    for name in extras:
        if name in ("all", "llamacpp"):
            continue
        assert name in composed, f"extra {name!r} is not reachable from [all]"
