"""Anthropic backend — the opt-in cloud model.

Everything else in ``arc/model/`` runs on this machine; this is the one exception,
by design (docs/DECISIONS.md ADR-025). It exists for the tasks where the local model's
size genuinely isn't enough — heavy analysis, research, hard multi-step reasoning — and
the user switches to it on purpose with ``arc model use claude chat``, not as a
silent fallback.

``anthropic`` (the Python SDK) is MIT (docs/DEPENDENCIES.md). It is an optional
dependency — importing this module without it installed raises a clear ``ModelError``
rather than an ``ImportError`` from three frames down.

The API key is never read through :class:`~arc.config.Config` — see ``load_api_key``
below, which mirrors ``arc/voice/gemini.py``'s reasoning exactly.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from arc.errors import ModelError
from arc.log import get_logger
from arc.model.base import (
    Completion,
    FinishReason,
    LanguageModel,
    Message,
    ModelCapabilities,
    Token,
    ToolCall,
    ToolSchema,
    Usage,
)

_log = get_logger(__name__)

#: Anthropic requires ``max_tokens``; there is no server-side default the way llama.cpp
#: and Ollama have one, so callers that omit it still get something sane.
_DEFAULT_MAX_TOKENS = 2048


def load_api_key(config_dir: Path | None = None) -> str | None:
    """Find the Anthropic key, without it ever going near the general config.

    Same reasoning as ``arc/voice/gemini.py.load_api_key``: ``Config.load`` merges
    every ``*.yaml`` in the directory, so a key in ``config/secrets.yaml`` would end up
    inside the same object ``--json`` output and log lines are built from. Kept
    separate, it can only be read by the code that needs it.

    Order: ``ANTHROPIC_API_KEY`` in the environment, then ``config/secrets.yaml``, then
    ``~/.arc/secrets.yaml``.
    """
    from arc.paths import arc_home
    from arc.paths import config_dir as default_config_dir

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key

    candidates = [
        (config_dir or default_config_dir()) / "secrets.yaml",
        arc_home() / "secrets.yaml",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            _log.warning("could not parse %s", path)
            continue
        if isinstance(data, dict):
            value = str(data.get("anthropic_api_key", "") or "").strip()
            if value:
                return value
    return None


def save_api_key(key: str, config_dir: Path | None = None) -> Path:
    """Write the key to ``config/secrets.yaml``, preserving any other keys already there.

    Same file ``gemini_api_key`` already lives in, and already gitignored
    (``config/secrets.yaml`` in ``.gitignore``) — one place, one convention.
    """
    import yaml

    from arc.paths import config_dir as default_config_dir

    target = (config_dir or default_config_dir()) / "secrets.yaml"
    existing: dict[str, Any] = {}
    if target.is_file():
        try:
            loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            loaded = None
        if isinstance(loaded, dict):
            existing = loaded

    existing["anthropic_api_key"] = key
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(existing, sort_keys=False), encoding="utf-8")
    tmp.replace(target)
    return target


class ClaudeModel(LanguageModel):
    """A model served by the Anthropic API."""

    def __init__(
        self,
        model_id: str,
        *,
        context_length: int,
        capabilities: ModelCapabilities,
        name: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """Configure a client for ``model_id``.

        The import is deferred into the constructor so the router can reason about
        this backend on a machine where the ``anthropic`` package is not installed.
        Missing credentials fail here rather than three calls later, with a message
        that names the fix (``arc model auth claude``) instead of a bare 401.
        """
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise ModelError(
                "the anthropic package is not installed. "
                "Install it with: pip install 'arc[anthropic]'"
            ) from exc

        key = api_key or load_api_key()
        if not key:
            raise ModelError(
                "no Anthropic API key. Run `arc model auth claude`, or set "
                "ANTHROPIC_API_KEY, or add `anthropic_api_key: ...` to config/secrets.yaml. "
                "Get a key at https://console.anthropic.com/settings/keys"
            )

        self._model_id = model_id
        self._name = name or model_id
        self._context_length = context_length
        self._capabilities = capabilities
        self._client = anthropic.Anthropic(api_key=key)

        _log.info("configured Anthropic model", extra={"model": model_id})

    @property
    def name(self) -> str:
        return self._name

    @property
    def context_length(self) -> int:
        return self._context_length

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    def count_tokens(self, text: str) -> int:
        """Count with Anthropic's own token-counting endpoint — the real tokenizer,
        just server-side rather than local, since the SDK does not ship one.
        """
        try:
            result = self._client.messages.count_tokens(
                model=self._model_id,
                messages=[{"role": "user", "content": text}],
            )
            return int(result.input_tokens)
        except Exception as exc:
            raise ModelError(f"token count failed on {self._name!r}: {exc}") from exc

    def _split_system(self, messages: list[Message]) -> tuple[str | None, list[Message]]:
        """Pull system-role messages out, since Anthropic takes ``system`` separately."""
        system_parts = [m.content for m in messages if m.role == "system"]
        rest = [m for m in messages if m.role != "system"]
        return ("\n\n".join(system_parts) if system_parts else None), rest

    def _to_api_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Convert our messages into Anthropic's shape.

        A ``tool`` role has no direct equivalent — Anthropic represents a tool result
        as a ``user``-role message containing a ``tool_result`` content block. Plain
        ``user``/``assistant`` messages pass through as simple text content.
        """
        converted: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id or "",
                                "content": m.content,
                            }
                        ],
                    }
                )
                continue
            converted.append({"role": m.role, "content": m.content})
        return converted

    def _request(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None,
        max_tokens: int,
        temperature: float,
        stop: list[str] | None,
        stream: bool,
    ) -> Any:
        system, rest = self._split_system(messages)

        kwargs: dict[str, Any] = {
            "model": self._model_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": self._to_api_messages(rest),
            "stream": stream,
        }
        if system:
            kwargs["system"] = system
        if stop:
            kwargs["stop_sequences"] = stop
        if tools and self._capabilities.native_tool_calling:
            kwargs["tools"] = [_to_anthropic_tool(t) for t in tools]

        try:
            return self._client.messages.create(**kwargs)
        except Exception as exc:
            raise ModelError(f"generation failed on {self._name!r}: {exc}") from exc

    def generate(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
        stop: list[str] | None = None,
    ) -> Completion:
        """Generate a single completion."""
        response = self._request(
            messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            stream=False,
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=block.input or {})
                )

        return Completion(
            text="".join(text_parts),
            finish_reason=_map_finish(response.stop_reason),
            usage=Usage(
                prompt_tokens=int(response.usage.input_tokens),
                completion_tokens=int(response.usage.output_tokens),
            ),
            tool_calls=tool_calls,
        )

    def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float = 0.7,
        stop: list[str] | None = None,
    ) -> Iterator[Token]:
        """Stream tokens as they are produced.

        Tool-call arguments arrive as incremental JSON fragments (``input_json_delta``)
        rather than text, so they are accumulated silently and only surfaced — as a
        single synthetic ``Token`` carrying no text but a ``tool_call`` finish reason —
        once the block closes. A caller that only cares about printable text sees
        nothing odd; one that inspects ``finish_reason`` learns a call happened.
        """
        response = self._request(
            messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            stream=True,
        )

        saw_tool_call = False
        try:
            for event in response:
                if event.type == "content_block_delta":
                    delta = event.delta
                    if getattr(delta, "type", None) == "text_delta":
                        yield Token(text=delta.text)
                elif event.type == "content_block_start":
                    if getattr(event.content_block, "type", None) == "tool_use":
                        saw_tool_call = True
                elif event.type == "message_delta":
                    reason = getattr(event.delta, "stop_reason", None)
                    if reason:
                        yield Token(text="", finish_reason=_map_finish(reason, saw_tool_call))
        except Exception as exc:
            raise ModelError(f"generation failed on {self._name!r}: {exc}") from exc


def _to_anthropic_tool(tool: ToolSchema) -> dict[str, Any]:
    """Anthropic's tool shape: flat, not nested under ``function`` like OpenAI's."""
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,
    }


def _map_finish(reason: str | None, saw_tool_call: bool = False) -> FinishReason:
    """Translate Anthropic's stop reason into ours."""
    if reason == "tool_use" or saw_tool_call:
        return "tool_call"
    if reason == "max_tokens":
        return "length"
    return "stop"
