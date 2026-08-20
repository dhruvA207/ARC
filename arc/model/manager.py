"""Model management — download, inspect, and select.

Separate from the router because these operations are about *files and configuration*,
not inference. None of this requires a backend to be installed, which is what lets
``arc model list`` work on a fresh machine with nothing downloaded.

Weights live in ``~/.arc/models/<key>/`` rather than the Hugging Face cache. The brief
wants ``~/.arc`` to be one portable, backup-able artifact (§4.2), and a model tucked
away in ``~/.cache/huggingface`` would not move with it.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from arc.config import Config
from arc.errors import ConfigError, ModelError
from arc.log import get_logger
from arc.model.registry import ModelEntry, active_key, load_registry
from arc.paths import arc_home, models_dir

_log = get_logger(__name__)

#: Roles a model can be selected for. Only `chat` is usable in Phase 2.
VALID_ROLES = ("chat", "vision", "embedding")


@dataclass(frozen=True, slots=True)
class ModelStatus:
    """A registry entry plus what is true about it on this machine."""

    entry: ModelEntry
    downloaded: bool
    active_for: list[str]
    size_on_disk_gb: float | None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable view."""
        return {
            **self.entry.to_dict(),
            "downloaded": self.downloaded,
            "active_for": self.active_for,
            "size_on_disk_gb": self.size_on_disk_gb,
        }


def local_path(key: str) -> Path:
    """Return where an entry's weights live locally."""
    return models_dir() / key


def _directory_size_gb(path: Path) -> float | None:
    """Sum a directory's file sizes, or None if it does not exist."""
    if not path.is_dir():
        return None
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / 1024**3, 2)


#: Where the ``ollama`` CLI keeps its models, absent an ``OLLAMA_MODELS`` override.
#: ARC does not manage this directory — it is informational only, for `arc model list`
#: and `arc model pull` to point at something real instead of a path that doesn't
#: exist.
def _ollama_models_dir() -> Path:
    import os

    override = os.environ.get("OLLAMA_MODELS")
    if override:
        return Path(override)
    return Path.home() / ".ollama" / "models"


def _ollama_has_tag(tag: str) -> bool:
    """Whether ``tag`` shows up in ``ollama list``. False if ollama isn't installed."""
    if shutil.which("ollama") is None:
        return False
    try:
        result = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10, check=False
        )
    except OSError:
        return False
    if result.returncode != 0:
        return False
    lines = result.stdout.splitlines()[1:]  # drop the header row
    return any(line.split()[0] == tag for line in lines if line.strip())


def is_downloaded(entry: ModelEntry) -> bool:
    """Whether the model is ready to load, in whatever sense applies to its backend.

    Checks for actual weight files rather than just the directory: an interrupted
    download leaves a directory behind, and reporting that as "downloaded" would send
    the user to a confusing load error instead of telling them to pull again.

    Neither Ollama nor Anthropic entries keep weights under ARC's management, so
    "downloaded" means something different for each: whether the tag is already
    pulled into Ollama's own store, and whether an API key is configured, respectively.
    """
    if entry.backend == "anthropic":
        from arc.model.anthropic_backend import load_api_key

        return load_api_key() is not None

    if entry.backend == "ollama":
        return _ollama_has_tag(entry.repo)

    path = local_path(entry.key)
    if not path.is_dir():
        return False
    patterns = ("*.safetensors", "*.gguf", "*.npz", "*.bin")
    return any(any(path.rglob(pattern)) for pattern in patterns)


def status_for(config: Config) -> list[ModelStatus]:
    """Return every registry entry with its local state, sorted by key."""
    registry = load_registry(config)
    active = {role: active_key(config, role) for role in VALID_ROLES}

    statuses: list[ModelStatus] = []
    for key in sorted(registry):
        entry = registry[key]
        statuses.append(
            ModelStatus(
                entry=entry,
                downloaded=is_downloaded(entry),
                active_for=[role for role, k in active.items() if k == key],
                size_on_disk_gb=_directory_size_gb(local_path(key)),
            )
        )
    return statuses


def pull(config: Config, key: str, *, force: bool = False) -> Path:
    """Download an entry's weights into ``~/.arc/models/<key>/``.

    Returns the local path. Raises ``ModelError`` with something actionable rather than
    letting a network or auth failure surface as a bare exception from deep inside the
    hub client.
    """
    registry = load_registry(config)
    if key not in registry:
        raise ConfigError(
            f"unknown model {key!r}. Available: {', '.join(sorted(registry)) or 'none'}"
        )

    entry = registry[key]

    if entry.backend == "anthropic":
        raise ModelError(
            f"{key!r} is a hosted Anthropic model; there are no weights to pull. "
            "Run `arc model auth claude` to configure an API key instead."
        )

    if entry.backend == "ollama":
        target = _ollama_models_dir()
        if is_downloaded(entry) and not force:
            _log.info("model already present", extra={"model": key, "tag": entry.repo})
            return target
        if shutil.which("ollama") is None:
            raise ModelError(
                "ollama is not installed. Get it from https://ollama.com/download, then retry."
            )
        try:
            subprocess.run(["ollama", "pull", entry.repo], check=True)
        except subprocess.CalledProcessError as exc:
            raise ModelError(f"could not pull {entry.repo!r} via ollama: {exc}") from exc
        _log.info("pulled model via ollama", extra={"model": key, "tag": entry.repo})
        return target

    target = local_path(key)

    if is_downloaded(entry) and not force:
        _log.info("model already present", extra={"model": key, "path": str(target)})
        return target

    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise ModelError(
            "huggingface_hub is not installed; it is needed to download weights."
        ) from exc

    target.parent.mkdir(parents=True, exist_ok=True)

    # GGUF repos hold every quantization; fetching them all would be tens of GB.
    allow = [entry.filename] if entry.filename else None

    try:
        snapshot_download(entry.repo, local_dir=str(target), allow_patterns=allow)
    except Exception as exc:
        # Leave a partial download in place rather than deleting it — resuming is
        # cheaper than starting over, and snapshot_download resumes by default.
        raise ModelError(f"could not download {entry.repo!r}: {exc}") from exc

    _log.info("pulled model", extra={"model": key, "repo": entry.repo, "path": str(target)})
    return target


def remove(config: Config, key: str) -> Path:
    """Delete an entry's local weights. The registry entry itself is untouched."""
    registry = load_registry(config)
    if key not in registry:
        raise ConfigError(f"unknown model {key!r}")

    entry = registry[key]

    if entry.backend == "anthropic":
        raise ModelError(f"{key!r} is a hosted Anthropic model; there is nothing local to remove.")

    if entry.backend == "ollama":
        if shutil.which("ollama") is None:
            raise ModelError("ollama is not installed.")
        if not _ollama_has_tag(entry.repo):
            raise ModelError(f"model {key!r} is not downloaded")
        try:
            subprocess.run(["ollama", "rm", entry.repo], check=True)
        except subprocess.CalledProcessError as exc:
            raise ModelError(f"could not remove {entry.repo!r} via ollama: {exc}") from exc
        _log.info("removed model via ollama", extra={"model": key, "tag": entry.repo})
        return _ollama_models_dir()

    target = local_path(key)
    if not target.exists():
        raise ModelError(f"model {key!r} is not downloaded")

    shutil.rmtree(target)
    _log.info("removed model weights", extra={"model": key})
    return target


def use(config: Config, key: str, role: str = "chat") -> Path:
    """Select a model for a role, writing to ``~/.arc/config.yaml``.

    Writes the *machine-local* override rather than the committed ``models.yaml``, so
    which model you happen to be running never shows up as a repository diff. Existing
    keys in that file are preserved — it is a general override file, not ours alone.
    """
    if role not in VALID_ROLES:
        raise ConfigError(f"unknown role {role!r}; expected one of {', '.join(VALID_ROLES)}")

    registry = load_registry(config)
    if key not in registry:
        raise ConfigError(
            f"unknown model {key!r}. Available: {', '.join(sorted(registry)) or 'none'}"
        )

    target = arc_home() / "config.yaml"
    existing: dict[str, Any] = {}
    if target.is_file():
        try:
            loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"could not parse {target}: {exc}") from exc
        if isinstance(loaded, dict):
            existing = loaded

    models = existing.setdefault("models", {})
    if not isinstance(models, dict):
        raise ConfigError(f"{target}: 'models' is not a mapping")
    active = models.setdefault("active", {})
    if not isinstance(active, dict):
        raise ConfigError(f"{target}: 'models.active' is not a mapping")
    active[role] = key

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")
    tmp.replace(target)

    _log.info("selected model", extra={"model": key, "role": role})
    return target
