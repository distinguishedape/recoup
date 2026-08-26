"""Razorpay credentials, loaded from the environment and nowhere else.

The key id is safe to show -- it identifies the account and its prefix is
how we prove we are in test mode. The two secrets are not, so they are
kept out of ``repr`` and ``str``: a config object that lands in a log line
or a traceback must not leak them.

Running this project against a live key would attempt real money movement
against real customers. ``load_config`` refuses to construct at all in
that case, which is a cheaper place to fail than anywhere downstream.
"""

import os
from pathlib import Path
from typing import Mapping

REQUIRED_KEYS = ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET")
TEST_KEY_PREFIX = "rzp_test_"


class MissingCredentials(RuntimeError):
    """One or more required environment variables is absent or empty."""


class LiveModeRefused(RuntimeError):
    """A live-mode key was supplied. Recoup runs against test mode only."""


class RazorpayConfig:
    def __init__(self, key_id: str, key_secret: str, webhook_secret: str) -> None:
        self.key_id = key_id
        self._key_secret = key_secret
        self._webhook_secret = webhook_secret

    @property
    def key_secret(self) -> str:
        return self._key_secret

    @property
    def webhook_secret(self) -> str:
        return self._webhook_secret

    @property
    def is_test_mode(self) -> bool:
        return self.key_id.startswith(TEST_KEY_PREFIX)

    def __repr__(self) -> str:
        return f"RazorpayConfig(key_id={self.key_id!r}, secrets=<redacted>)"

    __str__ = __repr__


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Read ``.env`` into a plain dict, without touching the environment.

    Deliberately tiny and dependency-free. The file holds secrets, so the
    parsing rules are the boring ones: ``KEY=value`` per line, ``#`` comments
    and blanks skipped, surrounding quotes stripped, and everything after the
    first ``=`` kept verbatim because secrets contain ``=``.

    Nothing is exported into ``os.environ``. A caller that wants these values
    asks for them, which keeps a credential file from silently changing the
    behaviour of unrelated code in the same process.
    """
    path = Path(path) if path else Path(".env")
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_config(env: Mapping[str, str] | None = None) -> RazorpayConfig:
    if env is None:
        # Real environment first, so an explicitly exported value beats a stale
        # line in a file somebody forgot about.
        merged = dict(load_dotenv())
        merged.update(os.environ)
        source: Mapping[str, str] = merged
    else:
        source = env
    missing = [key for key in REQUIRED_KEYS if not source.get(key)]
    if missing:
        raise MissingCredentials(
            "missing or empty Razorpay credentials: " + ", ".join(missing)
        )
    config = RazorpayConfig(
        key_id=source["RAZORPAY_KEY_ID"],
        key_secret=source["RAZORPAY_KEY_SECRET"],
        webhook_secret=source["RAZORPAY_WEBHOOK_SECRET"],
    )
    if not config.is_test_mode:
        raise LiveModeRefused(
            f"key id {config.key_id!r} is not a test-mode key; "
            "Recoup will not run against live credentials"
        )
    return config
