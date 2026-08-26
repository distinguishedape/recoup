"""Provider selection and response parsing, exercised without a network.

``_post_json`` is the only thing that talks to the outside world, so these
tests replace it and assert on the request each provider would have built and
on how it reads the reply back. That covers the part that actually breaks --
payload shape and response shape differ per vendor -- without needing a key.
"""

import pytest

from recoup.llm import providers
from recoup.llm.client import LLMClient, LLMUnavailable


@pytest.fixture()
def captured(monkeypatch):
    """Intercept the HTTP call and hand back a canned body."""
    sent: dict = {}
    reply: dict = {}

    def fake_post(url, payload, headers):
        sent["url"] = url
        sent["payload"] = payload
        sent["headers"] = headers
        return reply

    monkeypatch.setattr(providers, "_post_json", fake_post)
    return sent, reply


def test_a_named_provider_wins_over_detection():
    env = {"RECOUP_LLM_PROVIDER": "groq", "ANTHROPIC_API_KEY": "x", "GROQ_API_KEY": "y"}
    assert providers.detect_provider(env) == providers.GROQ


def test_an_unknown_named_provider_is_refused():
    with pytest.raises(ValueError):
        providers.detect_provider({"RECOUP_LLM_PROVIDER": "definitely-not-a-provider"})


def test_detection_falls_back_to_whichever_key_is_present():
    assert providers.detect_provider({"GEMINI_API_KEY": "k"}) == providers.GEMINI
    assert providers.detect_provider({"XAI_API_KEY": "k"}) == providers.XAI
    assert providers.detect_provider({"ANTHROPIC_API_KEY": "k"}) == providers.ANTHROPIC


def test_no_keys_at_all_detects_nothing():
    assert providers.detect_provider({}) is None


def test_free_tiers_are_preferred_when_several_keys_are_present():
    env = {"ANTHROPIC_API_KEY": "paid", "GROQ_API_KEY": "free"}
    assert providers.detect_provider(env) == providers.GROQ


def test_every_provider_declares_a_key_variable_and_a_default_model():
    for provider in providers.KEY_ENV_VARS:
        assert providers.DEFAULT_MODELS[provider]
    assert set(providers.DETECTION_ORDER) == set(providers.KEY_ENV_VARS)


def test_building_a_transport_without_its_key_is_refused():
    with pytest.raises(ValueError):
        providers.build_transport(providers.GROQ, {})


def test_a_real_user_agent_is_sent_because_wafs_reject_the_default(monkeypatch):
    # Groq answers urllib's default Python-urllib agent with a 403 and
    # Cloudflare error 1010, which reads exactly like a bad key and is not one.
    # This has to intercept urlopen rather than _post_json, because the header
    # construction under test lives inside _post_json itself.
    import io
    import json as _json

    seen: dict = {}

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        seen["headers"] = dict(request.headers)
        return _Response(_json.dumps({"choices": [{"message": {"content": "x"}}]}).encode())

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    providers.build_transport(providers.GROQ, {"GROQ_API_KEY": "k"})("m", "s", "u", 10)

    # urllib title-cases header names on the Request object.
    agent = seen["headers"]["User-agent"]
    assert "python-urllib" not in agent.lower()
    assert agent.startswith("recoup/")


def test_groq_sends_the_openai_chat_shape_to_the_groq_endpoint(captured):
    sent, reply = captured
    reply["choices"] = [{"message": {"content": "hello from groq"}}]
    transport = providers.build_transport(providers.GROQ, {"GROQ_API_KEY": "k"})

    assert transport("openai/gpt-oss-120b", "sys", "usr", 300) == "hello from groq"
    assert sent["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer k"
    assert sent["payload"]["temperature"] == 0.0
    assert sent["payload"]["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]


def test_xai_uses_the_same_shape_against_its_own_endpoint(captured):
    sent, reply = captured
    reply["choices"] = [{"message": {"content": "hello from grok"}}]
    transport = providers.build_transport(providers.XAI, {"XAI_API_KEY": "k"})

    assert transport("grok-2-latest", "sys", "usr", 300) == "hello from grok"
    assert sent["url"] == "https://api.x.ai/v1/chat/completions"


def test_a_custom_base_url_overrides_the_preset(captured):
    sent, reply = captured
    reply["choices"] = [{"message": {"content": "local"}}]
    transport = providers.build_transport(
        providers.GROQ, {"GROQ_API_KEY": "k", "OPENAI_BASE_URL": "http://localhost:1234/v1"}
    )
    transport("any-model", "sys", "usr", 10)
    assert sent["url"] == "http://localhost:1234/v1/chat/completions"


def test_an_openai_compatible_provider_with_no_base_url_is_refused():
    with pytest.raises(ValueError):
        providers.build_transport(providers.OPENAI_COMPATIBLE, {"OPENAI_API_KEY": "k"})


def test_gemini_sends_its_own_shape_and_carries_the_key_in_the_url(captured):
    sent, reply = captured
    reply["candidates"] = [{"content": {"parts": [{"text": "hello from gemini"}]}}]
    transport = providers.build_transport(providers.GEMINI, {"GEMINI_API_KEY": "k"})

    assert transport("gemini-2.0-flash", "sys", "usr", 300) == "hello from gemini"
    assert "generativelanguage.googleapis.com" in sent["url"]
    assert sent["url"].endswith("key=k")
    assert sent["payload"]["system_instruction"]["parts"][0]["text"] == "sys"
    assert sent["payload"]["generationConfig"]["temperature"] == 0.0


def test_anthropic_sends_its_own_shape_with_a_version_header(captured):
    sent, reply = captured
    reply["content"] = [{"type": "text", "text": "hello from claude"}]
    transport = providers.build_transport(providers.ANTHROPIC, {"ANTHROPIC_API_KEY": "k"})

    assert transport("claude-sonnet-5", "sys", "usr", 300) == "hello from claude"
    assert sent["headers"]["x-api-key"] == "k"
    assert sent["headers"]["anthropic-version"]
    assert sent["payload"]["system"] == "sys"


def test_an_empty_reply_is_an_error_rather_than_an_empty_string(captured):
    _, reply = captured
    reply["choices"] = []
    transport = providers.build_transport(providers.GROQ, {"GROQ_API_KEY": "k"})
    with pytest.raises(RuntimeError):
        transport("m", "sys", "usr", 10)


def test_a_gemini_reply_with_no_candidates_is_an_error(captured):
    _, reply = captured
    reply["candidates"] = []
    transport = providers.build_transport(providers.GEMINI, {"GEMINI_API_KEY": "k"})
    with pytest.raises(RuntimeError):
        transport("m", "sys", "usr", 10)


def test_the_model_defaults_per_provider_and_is_overridable():
    assert providers.model_for(providers.GROQ, {}) == "openai/gpt-oss-120b"
    assert providers.model_for(providers.GROQ, {"RECOUP_LLM_MODEL": "mine"}) == "mine"


def test_the_client_picks_up_a_provider_from_the_environment(tmp_path, captured):
    sent, reply = captured
    reply["choices"] = [{"message": {"content": "answered"}}]
    client = LLMClient(tmp_path / "cache.json", env={"GROQ_API_KEY": "k"})

    assert client.provider == providers.GROQ
    assert client.model == "openai/gpt-oss-120b"
    assert client.complete("sys", "usr") == "answered"
    assert "groq.com" in sent["url"]


def test_the_client_still_prefers_an_injected_transport_over_any_key(tmp_path):
    client = LLMClient(
        tmp_path / "cache.json",
        transport=lambda m, s, u, t: "injected",
        env={"GROQ_API_KEY": "k"},
    )
    assert client.complete("sys", "usr") == "injected"


def test_the_client_with_no_key_at_all_reports_what_to_set(tmp_path):
    client = LLMClient(tmp_path / "cache.json", env={})
    with pytest.raises(LLMUnavailable) as excinfo:
        client.complete("sys", "usr")
    assert "GROQ_API_KEY" in str(excinfo.value)


def test_switching_provider_changes_the_cache_key(tmp_path):
    groq = LLMClient(tmp_path / "a.json", env={"GROQ_API_KEY": "k"})
    gemini = LLMClient(tmp_path / "b.json", env={"GEMINI_API_KEY": "k"})
    # Different models must not share cached answers, or a provider switch
    # would silently replay another model's reasoning.
    assert groq.cache_key("sys", "usr", 100) != gemini.cache_key("sys", "usr", 100)
