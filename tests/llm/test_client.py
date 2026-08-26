import pytest

from recoup.llm.client import DEFAULT_MODEL, LLMClient, LLMUnavailable


def test_the_default_model_is_the_one_the_spec_pins():
    assert DEFAULT_MODEL == "claude-sonnet-5"


def test_a_completion_reaches_the_transport(tmp_path):
    client = LLMClient(tmp_path / "cache.json", transport=lambda m, s, u, t: "hello")
    assert client.complete("sys", "user") == "hello"
    assert client.calls == 1


def test_an_identical_prompt_is_served_from_cache_without_calling_out(tmp_path):
    client = LLMClient(tmp_path / "cache.json", transport=lambda m, s, u, t: "hello")
    client.complete("sys", "user")
    client.complete("sys", "user")
    assert client.calls == 1


def test_the_cache_survives_a_new_client_on_the_same_file(tmp_path):
    first = LLMClient(tmp_path / "cache.json", transport=lambda m, s, u, t: "hello")
    first.complete("sys", "user")

    def explode(model, system, user, max_tokens):
        raise AssertionError("the cache should have answered this")

    second = LLMClient(tmp_path / "cache.json", transport=explode)
    assert second.complete("sys", "user") == "hello"
    assert second.calls == 0


def test_a_different_prompt_is_a_different_cache_entry(tmp_path):
    client = LLMClient(tmp_path / "cache.json", transport=lambda m, s, u, t: u.upper())
    assert client.complete("sys", "alpha") == "ALPHA"
    assert client.complete("sys", "beta") == "BETA"
    assert client.calls == 2


def test_the_cache_key_covers_model_system_user_and_max_tokens(tmp_path):
    client = LLMClient(tmp_path / "cache.json", transport=lambda m, s, u, t: "x")
    base = client.cache_key("sys", "user", 1024)
    assert base != client.cache_key("sys2", "user", 1024)
    assert base != client.cache_key("sys", "user2", 1024)
    assert base != client.cache_key("sys", "user", 2048)


def test_a_transport_failure_becomes_llm_unavailable(tmp_path):
    def explode(model, system, user, max_tokens):
        raise ConnectionError("no network")

    client = LLMClient(tmp_path / "cache.json", transport=explode)
    with pytest.raises(LLMUnavailable):
        client.complete("sys", "user")


def test_a_failed_call_is_not_cached(tmp_path):
    calls = {"n": 0}

    def flaky(model, system, user, max_tokens):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("transient")
        return "second time lucky"

    client = LLMClient(tmp_path / "cache.json", transport=flaky)
    with pytest.raises(LLMUnavailable):
        client.complete("sys", "user")
    assert client.complete("sys", "user") == "second time lucky"


def test_no_api_key_and_no_transport_means_unavailable_not_a_crash(tmp_path):
    # An explicit empty environment rather than deleting one variable: the
    # client now recognises several providers, so a test that unset only one of
    # them passed or failed depending on what happened to be exported in the
    # developer's shell.
    client = LLMClient(tmp_path / "cache.json", env={})
    with pytest.raises(LLMUnavailable):
        client.complete("sys", "user")
