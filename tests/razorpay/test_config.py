import pytest

from recoup.razorpay.config import (
    LiveModeRefused, MissingCredentials, load_config,
)

ENV = {
    "RAZORPAY_KEY_ID": "rzp_test_abc123",
    "RAZORPAY_KEY_SECRET": "secret_value",
    "RAZORPAY_WEBHOOK_SECRET": "webhook_value",
}


def test_config_loads_from_the_environment():
    c = load_config(ENV)
    assert (c.key_id, c.key_secret, c.webhook_secret) == (
        "rzp_test_abc123", "secret_value", "webhook_value")


@pytest.mark.parametrize("missing", sorted(ENV))
def test_a_missing_credential_is_named_in_the_error(missing):
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(MissingCredentials) as excinfo:
        load_config(env)
    assert missing in str(excinfo.value)


def test_an_empty_credential_counts_as_missing():
    with pytest.raises(MissingCredentials):
        load_config({**ENV, "RAZORPAY_KEY_SECRET": ""})


def test_a_live_key_is_refused():
    with pytest.raises(LiveModeRefused):
        load_config({**ENV, "RAZORPAY_KEY_ID": "rzp_live_abc123"})


def test_test_mode_is_detected_from_the_key_prefix():
    assert load_config(ENV).is_test_mode is True


def test_secrets_never_appear_in_the_repr():
    rendered = repr(load_config(ENV))
    assert "secret_value" not in rendered
    assert "webhook_value" not in rendered
    assert "rzp_test_abc123" in rendered


def test_secrets_never_appear_in_the_string_form():
    assert "secret_value" not in str(load_config(ENV))
