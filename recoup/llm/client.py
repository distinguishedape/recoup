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

from recoup.llm.providers import (
    ANTHROPIC,
    DEFAULT_MODELS,
    KEY_ENV_VARS,
    TEMPERATURE,
    Transport,
    build_transport,
    detect_provider,
    model_for,
)

DEFAULT_MODEL = DEFAULT_MODELS[ANTHROPIC]
"""Fallback when no provider can be detected. Which provider is actually used
is decided by whichever key is present; see ``recoup.llm.providers``."""


class LLMUnavailable(RuntimeError):
    """The model could not be reached, or answered with something unusable."""


class LLMClient:
    def __init__(
        self,
        cache_path: Path,
        model: str | None = None,
        api_key: str | None = None,
        transport: Transport | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        environment = dict(os.environ if env is None else env)
        if api_key:
            # An explicitly supplied key belongs to whichever provider the
            # environment names, or to Anthropic if it names none.
            provider_hint = detect_provider(environment) or ANTHROPIC
            environment.setdefault(KEY_ENV_VARS[provider_hint], api_key)
        self.provider = detect_provider(environment) if transport is None else None
        self.model = model or (
            model_for(self.provider, environment) if self.provider else DEFAULT_MODEL
        )
        self.calls = 0
        self._cache_path = Path(cache_path)
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, str] = {}
        if self._cache_path.exists():
            self._cache = json.loads(self._cache_path.read_text(encoding="utf-8"))
        if transport is not None:
            self._transport: Transport | None = transport
        elif self.provider is not None:
            self._transport = build_transport(self.provider, environment)
        else:
            self._transport = None

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
                "no model provider is configured and no cached response for this "
                "prompt. Set one of GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, "
                "TOGETHER_API_KEY, XAI_API_KEY or ANTHROPIC_API_KEY."
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
