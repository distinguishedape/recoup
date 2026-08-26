"""Anthropic client wrapper with an on-disk, hash-keyed response cache.

Two requirements meet here. The experiment must be reproducible -- the same
seed and the same configuration must produce the same report, which is
impossible if a model call can drift between runs. And the demo must not
depend on the network being up.

The cache solves both: the first run records every response keyed by
SHA-256 of ``model|system|user|max_tokens``, and every later run replays
them without touching the API. Temperature is pinned to 0.

Failures raise ``LLMUnavailable`` and are never cached, so a network blip
does not poison the cache with an error. Every call site catches it and
falls back to deterministic behaviour.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Callable

DEFAULT_MODEL = "claude-sonnet-5"
TEMPERATURE = 0.0

Transport = Callable[[str, str, str, int], str]


class LLMUnavailable(RuntimeError):
    """The model could not be reached, or answered with something unusable."""


def _anthropic_transport(api_key: str) -> Transport:
    def call(model: str, system: str, user: str, max_tokens: int) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=TEMPERATURE,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    return call


class LLMClient:
    def __init__(
        self,
        cache_path: Path,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.model = model
        self.calls = 0
        self._cache_path = Path(cache_path)
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, str] = {}
        if self._cache_path.exists():
            self._cache = json.loads(self._cache_path.read_text(encoding="utf-8"))
        if transport is not None:
            self._transport: Transport | None = transport
        else:
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            self._transport = _anthropic_transport(key) if key else None

    def cache_key(self, system: str, user: str, max_tokens: int) -> str:
        material = "\x00".join([self.model, system, user, str(max_tokens)])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _persist(self) -> None:
        self._cache_path.write_text(
            json.dumps(self._cache, indent=2, sort_keys=True), encoding="utf-8"
        )

    def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        key = self.cache_key(system, user, max_tokens)
        if key in self._cache:
            return self._cache[key]
        if self._transport is None:
            raise LLMUnavailable(
                "no ANTHROPIC_API_KEY and no cached response for this prompt"
            )
        try:
            text = self._transport(self.model, system, user, max_tokens)
        except LLMUnavailable:
            raise
        except Exception as exc:
            raise LLMUnavailable(f"model call failed: {exc}") from exc
        self.calls += 1
        self._cache[key] = text
        self._persist()
        return text
