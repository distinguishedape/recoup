"""Transports for the model providers Recoup can talk to.

The client only ever needs a ``Transport`` -- a callable taking
``(model, system, user, max_tokens)`` and returning text -- so a provider is
one entry here and nothing else in the pipeline changes.

That seam matters more than it looks. The model does two narrow jobs in this
system: resolve two genuinely ambiguous decline reasons, and draft an
intervention plan. Both are validated against closed enums and a template
allowlist before anything acts on them, and both fall back to deterministic
behaviour when the answer is unusable. A cheaper or weaker model therefore
degrades the *quality* of a suggestion, never the *safety* of an action --
which is why running this on a free tier is a reasonable engineering choice
rather than a compromise.

Everything uses ``urllib`` from the standard library rather than a vendor SDK,
so adding a provider adds no dependency. Temperature is pinned to zero
everywhere, because the experiment has to be reproducible.
"""

import json
import urllib.error
import urllib.request
from typing import Callable

Transport = Callable[[str, str, str, int], str]

TEMPERATURE = 0.0
TIMEOUT_SECONDS = 60

ANTHROPIC = "anthropic"
GEMINI = "gemini"
GROQ = "groq"
XAI = "xai"
OPENROUTER = "openrouter"
TOGETHER = "together"
OPENAI_COMPATIBLE = "openai_compatible"

#: Providers that speak the OpenAI chat-completions shape. One transport
#: serves all of them; only the base URL differs.
OPENAI_SHAPED: dict[str, str] = {
    GROQ: "https://api.groq.com/openai/v1",
    XAI: "https://api.x.ai/v1",
    OPENROUTER: "https://openrouter.ai/api/v1",
    TOGETHER: "https://api.together.xyz/v1",
    OPENAI_COMPATIBLE: "",  # supply OPENAI_BASE_URL yourself
}

KEY_ENV_VARS: dict[str, str] = {
    ANTHROPIC: "ANTHROPIC_API_KEY",
    GEMINI: "GEMINI_API_KEY",
    GROQ: "GROQ_API_KEY",
    XAI: "XAI_API_KEY",
    OPENROUTER: "OPENROUTER_API_KEY",
    TOGETHER: "TOGETHER_API_KEY",
    OPENAI_COMPATIBLE: "OPENAI_API_KEY",
}

#: Provider line-ups rotate. These are defaults, not guarantees -- if a model
#: is retired the API answers 404 and ``RECOUP_LLM_MODEL`` overrides it without
#: a code change. Ask any OpenAI-shaped provider for ``/models`` to see what a
#: given key can actually reach.
DEFAULT_MODELS: dict[str, str] = {
    ANTHROPIC: "claude-sonnet-5",
    GEMINI: "gemini-2.0-flash",
    GROQ: "openai/gpt-oss-120b",
    XAI: "grok-2-latest",
    OPENROUTER: "meta-llama/llama-3.3-70b-instruct:free",
    TOGETHER: "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
    OPENAI_COMPATIBLE: "gpt-4o-mini",
}

#: Order used when no provider is named explicitly: whichever key is present
#: first wins. Free tiers come before paid ones deliberately.
DETECTION_ORDER: tuple[str, ...] = (
    GROQ,
    GEMINI,
    OPENROUTER,
    TOGETHER,
    XAI,
    ANTHROPIC,
    OPENAI_COMPATIBLE,
)

BASE_URL_ENV_VAR = "OPENAI_BASE_URL"
PROVIDER_ENV_VAR = "RECOUP_LLM_PROVIDER"
MODEL_ENV_VAR = "RECOUP_LLM_MODEL"


USER_AGENT = "recoup/0.1 (+https://github.com/distinguishedape/recoup)"
"""Several providers sit behind a WAF that rejects urllib's default
``Python-urllib/3.13`` agent outright -- Groq answers such requests with a
403 and Cloudflare error 1010, which reads exactly like a bad key and is not
one. Identifying the client honestly avoids that."""


def _post_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            **headers,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def anthropic_transport(api_key: str) -> Transport:
    def call(model: str, system: str, user: str, max_tokens: int) -> str:
        body = _post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": TEMPERATURE,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
        return "".join(
            block.get("text", "")
            for block in body.get("content", [])
            if isinstance(block, dict)
        )

    return call


def gemini_transport(api_key: str) -> Transport:
    """Google AI Studio: a free tier that needs no card."""

    def call(model: str, system: str, user: str, max_tokens: int) -> str:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        body = _post_json(
            url,
            {
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "temperature": TEMPERATURE,
                    "maxOutputTokens": max_tokens,
                },
            },
            {},
        )
        candidates = body.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"no candidates in Gemini response: {str(body)[:300]}")
        parts = candidates[0].get("content", {}).get("parts", []) or []
        return "".join(part.get("text", "") for part in parts if isinstance(part, dict))

    return call


def openai_compatible_transport(api_key: str, base_url: str) -> Transport:
    """Groq, xAI, OpenRouter, Together, a local server -- anything speaking the
    OpenAI chat-completions shape."""

    if not base_url:
        raise ValueError(
            f"no base URL for this provider; set {BASE_URL_ENV_VAR} or name a known provider"
        )

    def call(model: str, system: str, user: str, max_tokens: int) -> str:
        body = _post_json(
            f"{base_url.rstrip('/')}/chat/completions",
            {
                "model": model,
                "temperature": TEMPERATURE,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            {"Authorization": f"Bearer {api_key}"},
        )
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError(f"no choices in response: {str(body)[:300]}")
        return choices[0].get("message", {}).get("content", "") or ""

    return call


def detect_provider(env: dict[str, str]) -> str | None:
    """Pick a provider from whichever key is present.

    An explicit ``RECOUP_LLM_PROVIDER`` wins. Otherwise the first key found in
    ``DETECTION_ORDER`` decides, so dropping one key into the environment is
    enough to switch providers with no other change.
    """
    explicit = env.get(PROVIDER_ENV_VAR, "").strip().lower()
    if explicit:
        if explicit not in KEY_ENV_VARS:
            raise ValueError(
                f"unknown provider {explicit!r}; expected one of {sorted(KEY_ENV_VARS)}"
            )
        return explicit
    for provider in DETECTION_ORDER:
        if env.get(KEY_ENV_VARS[provider]):
            return provider
    return None


def build_transport(provider: str, env: dict[str, str]) -> Transport:
    if provider not in KEY_ENV_VARS:
        raise ValueError(f"unknown provider {provider!r}")
    api_key = env.get(KEY_ENV_VARS[provider], "")
    if not api_key:
        raise ValueError(f"{KEY_ENV_VARS[provider]} is not set")
    if provider == ANTHROPIC:
        return anthropic_transport(api_key)
    if provider == GEMINI:
        return gemini_transport(api_key)
    base_url = env.get(BASE_URL_ENV_VAR) or OPENAI_SHAPED[provider]
    return openai_compatible_transport(api_key, base_url)


def model_for(provider: str, env: dict[str, str]) -> str:
    return env.get(MODEL_ENV_VAR) or DEFAULT_MODELS[provider]
