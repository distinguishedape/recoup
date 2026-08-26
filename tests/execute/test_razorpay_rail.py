from datetime import datetime, timezone

import pytest

from recoup.execute.razorpay_rail import (
    CARD_CHANGE_PARAM,
    ManualRetryUnsupported,
    RazorpayTestRail,
)
from recoup.razorpay.config import LiveModeRefused, RazorpayConfig

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


class FakeSubscriptionResource:
    def __init__(self, entities: dict[str, dict]) -> None:
        self._entities = entities
        self.fetched: list[str] = []

    def fetch(self, subscription_id: str) -> dict:
        self.fetched.append(subscription_id)
        if subscription_id not in self._entities:
            raise KeyError(subscription_id)
        return self._entities[subscription_id]


class FakeClient:
    def __init__(self, entities: dict[str, dict]) -> None:
        self.subscription = FakeSubscriptionResource(entities)


def config() -> RazorpayConfig:
    return RazorpayConfig(
        key_id="rzp_test_abc123", key_secret="secret_value", webhook_secret="webhook_value"
    )


def rail(**entities) -> RazorpayTestRail:
    return RazorpayTestRail(FakeClient(entities), config())


HALTED = {"id": "sub_TEST0001", "status": "halted", "short_url": "https://rzp.io/i/TESTLINK"}


def test_charging_is_refused_with_an_explanation_not_a_fabricated_result():
    with pytest.raises(ManualRetryUnsupported) as excinfo:
        rail(sub_TEST0001=HALTED).charge("sub_TEST0001", NOW)
    assert "manual" in str(excinfo.value).lower()


def test_the_rail_still_satisfies_the_payment_rail_protocol():
    r = rail(sub_TEST0001=HALTED)
    assert hasattr(r, "charge") and hasattr(r, "deliver_update_request")


def test_fetching_a_subscription_returns_the_entity():
    assert rail(sub_TEST0001=HALTED).fetch_subscription("sub_TEST0001")["status"] == "halted"


def test_the_subscription_state_is_read_from_the_api():
    assert rail(sub_TEST0001=HALTED).subscription_state("sub_TEST0001") == "halted"


def test_a_subscription_the_api_does_not_know_reports_unknown():
    assert rail().subscription_state("sub_MISSING") == "unknown"


def test_the_card_change_link_carries_the_card_change_parameter():
    link = rail(sub_TEST0001=HALTED).card_change_link("sub_TEST0001")
    assert link.startswith("https://rzp.io/i/TESTLINK")
    assert CARD_CHANGE_PARAM in link


def test_a_subscription_with_no_short_url_yields_an_empty_link():
    r = rail(sub_TEST0001={"id": "sub_TEST0001", "status": "halted"})
    assert r.card_change_link("sub_TEST0001") == ""


def test_delivering_an_update_request_reports_whether_a_link_exists():
    assert rail(sub_TEST0001=HALTED).deliver_update_request("sub_TEST0001", NOW) is True
    bare = rail(sub_TEST0001={"id": "x", "status": "halted"})
    assert bare.deliver_update_request("sub_TEST0001", NOW) is False


def test_an_api_error_is_reported_as_unknown_rather_than_crashing_the_batch():
    class ExplodingResource:
        def fetch(self, subscription_id: str) -> dict:
            raise RuntimeError("razorpay is having a day")

    class ExplodingClient:
        subscription = ExplodingResource()

    r = RazorpayTestRail(ExplodingClient(), config())
    assert r.subscription_state("sub_TEST0001") == "unknown"
    assert r.card_change_link("sub_TEST0001") == ""


def test_the_rail_refuses_to_be_built_on_a_live_config():
    live = RazorpayConfig(key_id="rzp_live_abc", key_secret="s", webhook_secret="w")
    with pytest.raises(LiveModeRefused):
        RazorpayTestRail(FakeClient({}), live)
