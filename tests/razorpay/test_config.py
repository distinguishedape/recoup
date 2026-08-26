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


def test_a_dotenv_file_is_read_without_exporting_anything(tmp_path, monkeypatch):
    import os

    from recoup.razorpay.config import load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        "\n"
        'RAZORPAY_KEY_ID="rzp_test_fromfile"\n'
        "RAZORPAY_KEY_SECRET=secret=with=equals\n"
        "  RAZORPAY_WEBHOOK_SECRET = spaced  \n",
        encoding="utf-8",
    )
    values = load_dotenv(env_file)
    assert values["RAZORPAY_KEY_ID"] == "rzp_test_fromfile"
    # Secrets contain '=' and everything after the first one is the value.
    assert values["RAZORPAY_KEY_SECRET"] == "secret=with=equals"
    assert values["RAZORPAY_WEBHOOK_SECRET"] == "spaced"
    # A credentials file must not quietly change the behaviour of unrelated
    # code sharing the process.
    assert "RAZORPAY_KEY_ID" not in os.environ or os.environ.get(
        "RAZORPAY_KEY_ID"
    ) != "rzp_test_fromfile"


def test_a_missing_dotenv_is_not_an_error(tmp_path):
    from recoup.razorpay.config import load_dotenv

    assert load_dotenv(tmp_path / "nope.env") == {}


def test_an_exported_variable_beats_a_stale_line_in_the_file(tmp_path, monkeypatch):
    from recoup.razorpay.config import load_config

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "RAZORPAY_KEY_ID=rzp_test_fromfile\n"
        "RAZORPAY_KEY_SECRET=filesecret\n"
        "RAZORPAY_WEBHOOK_SECRET=filehook\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_exported")
    assert load_config().key_id == "rzp_test_exported"


def test_the_file_supplies_what_the_environment_lacks(tmp_path, monkeypatch):
    from recoup.razorpay.config import load_config

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "RAZORPAY_KEY_ID=rzp_test_fromfile\n"
        "RAZORPAY_KEY_SECRET=filesecret\n"
        "RAZORPAY_WEBHOOK_SECRET=filehook\n",
        encoding="utf-8",
    )
    for name in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET"):
        monkeypatch.delenv(name, raising=False)
    config = load_config()
    assert config.key_id == "rzp_test_fromfile"
    assert config.webhook_secret == "filehook"
