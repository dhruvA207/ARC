"""Ollama backend — a locally-served model, weights managed outside ARC.

Talks to the Ollama daemon (``localhost:11434`` by default) rather than loading weights
in-process, which is what lets ``arc/model/manager.py`` treat pulling and removing an
Ollama model as ``ollama pull``/``ollama rm`` instead of a Hugging Face download.

``ollama`` (the Python client) is MIT (docs/DEPENDENCIES.md). It is an optional
dependency — importing this module without it installed raises a clear ``ModelError``
rather than an ``ImportError`` from three frames down.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
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


class OllamaModel(LanguageModel):
    """A model served by a local Ollama daemon."""

    def __init__(
        self,
        tag: str,
        *,
        context_length: int,
        capabilities: ModelCapabilities,
        name: str | None = None,
        host: str | None = None,
    ) -> None:
        """Point at an Ollama model tag.

        The import is deferred into the constructor so the router can reason about
        this backend on a machine where the ``ollama`` package is not installed. This
        does not touch the network — the tag is not checked against the daemon until
        the first ``generate``/``stream`` call, so a daemon that is not running yet
        (or a model not yet pulled) fails there with an actionable message instead of
        at construction time.
        """
        try:
            from ollama import Client
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise ModelError(
                "the ollama package is not installed. Install it with: pip install 'arc[ollama]'"
            ) from exc

        self._tag = tag
        self._name = name or tag
        self._context_length = context_length
        self._capabilities = capabilities
        self._client = Client(host=host) if host else Client()

        _log.info("configured Ollama model", extra={"tag": tag, "context": context_length})

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
        """Approximate, because Ollama exposes no tokenize endpoint.

        Every other backend here uses the model's real tokenizer, per ``base.py``'s
        contract. Ollama does not hand one out short of loading the GGUF a second time
        through llama.cpp just to tokenize, which would double the resident memory for
        a number the working-memory budget only needs to be roughly right about. This
        is a plain-English heuristic (~4 characters per token, English-text average)
        rather than that cost — good enough for budgeting, not for anything exact.
        """
        return max(1, len(text) // 4)

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
        """Build and issue a chat call.

        Tools are only forwarded when the model declares native tool calling, same
        rule as ``llamacpp.py`` — a model never trained on a tool-call template
        produces worse output than leaving the prompted ReAct fallback to handle it.
        """
        options: dict[str, Any] = {"temperature": temperature, "num_predict": max_tokens}
        if stop:
            options["stop"] = stop

        kwargs: dict[str, Any] = {
            "model": self._tag,
            "messages": [m.to_dict() for m in messages],
            "options": options,
            "stream": stream,
        }
        if tools and self._capabilities.native_tool_calling:
            kwargs["tools"] = [t.to_dict() for t in tools]

        try:
            return self._client.chat(**kwargs)
        except Exception as exc:
            raise ModelError(f"generation failed on {self._name!r}: {exc}") from exc

    def generate(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        stop: list[str] | None = None,
    ) -> Completion:
        """Generate a single completion."""
        raw = self._request(
            messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            stream=False,
        )

        message = raw.get("message", {}) if isinstance(raw, dict) else raw.message
        content = message.get("content") if isinstance(message, dict) else message.content
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else message.tool_calls
        done_reason = raw.get("done_reason") if isinstance(raw, dict) else raw.done_reason
        prompt_eval = raw.get("prompt_eval_count") if isinstance(raw, dict) else raw.prompt_eval_count
        eval_count = raw.get("eval_count") if isinstance(raw, dict) else raw.eval_count

        return Completion(
            text=content or "",
            finish_reason=_map_finish(done_reason, bool(tool_calls)),
            usage=Usage(
                prompt_tokens=int(prompt_eval or 0),
                completion_tokens=int(eval_count or 0),
            ),
            tool_calls=_parse_tool_calls(tool_calls),
        )

    def stream(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        stop: list[str] | None = None,
    ) -> Iterator[Token]:
        """Stream tokens as they are produced."""
        raw = self._request(
            messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            stream=True,
        )

        for chunk in raw:
            message = chunk.get("message", {}) if isinstance(chunk, dict) else chunk.message
            text = message.get("content") if isinstance(message, dict) else message.content
            done = chunk.get("done") if isinstance(chunk, dict) else chunk.done
            done_reason = chunk.get("done_reason") if isinstance(chunk, dict) else chunk.done_reason
            if not text and not done:
                continue
            yield Token(
                text=text or "",
                finish_reason=_map_finish(done_reason, False) if done else None,
            )


def _map_finish(reason: str | None, has_tool_calls: bool) -> FinishReason:
    """Translate Ollama's done reason into ours."""
    if has_tool_calls:
        return "tool_call"
    if reason == "length":
        return "length"
    return "stop"


def _parse_tool_calls(raw: list[Any] | None) -> list[ToolCall]:
    """Convert native tool calls into our shape.

    Ollama hands back already-parsed argument objects (unlike llama.cpp's JSON-string
    arguments), so there is no repair path to run here — a malformed entry just has no
    name or a non-dict payload, and is dropped with a warning rather than raising.
    """
    if not raw:
        return []

    calls: list[ToolCall] = []
    for entry in raw:
        function = entry.get("function", {}) if isinstance(entry, dict) else entry.function
        name = function.get("name") if isinstance(function, dict) else function.name
        arguments = function.get("arguments") if isinstance(function, dict) else function.arguments
        if not name:
            continue
        if not isinstance(arguments, dict):
            _log.warning("dropping tool call with non-object arguments", extra={"tool": name})
            continue
        call_id = entry.get("id") if isinstance(entry, dict) else getattr(entry, "id", None)
        calls.append(ToolCall(id=str(call_id or uuid.uuid4().hex[:12]), name=name, arguments=arguments))
    return calls
