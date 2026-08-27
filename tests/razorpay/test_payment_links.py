from datetime import datetime, timezone

import pytest

from recoup.audit.log import AuditLog
from recoup.execute.razorpay_rail import RazorpayTestRail
from recoup.razorpay.config import LiveModeRefused, RazorpayConfig
from recoup.razorpay.payment_links import PaymentLink, PaymentLinkWriter

CONFIG = RazorpayConfig(key_id="rzp_test_x", key_secret="s", webhook_secret="w")
NOW = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)

CUSTOMER = {"name": "A", "email": "a@example.com", "contact": "+919876543210"}


# ---------------------------------------------------------------------------
# Small, explicit fakes -- not mocks of the SDK.
# ---------------------------------------------------------------------------


class FakeSubscriptionResource:
    def __init__(self, entities: dict[str, dict]) -> None:
        self._entities = entities

    def fetch(self, subscription_id: str) -> dict:
        if subscription_id not in self._entities:
            raise KeyError(subscription_id)
        return self._entities[subscription_id]


class FakeClient:
    """Satisfies the ``RazorpayClient`` protocol: ``.subscription.fetch(id)``."""

    def __init__(self, entities: dict[str, dict]) -> None:
        self.subscription = FakeSubscriptionResource(entities)


def fake_client(**entities: dict) -> FakeClient:
    """A client whose one subscription has an amount and a full contact."""
    entities.setdefault(
        "sub_1",
        {
            "id": "sub_1",
            "status": "halted",
            "amount": 99900,
            "customer": dict(CUSTOMER),
        },
    )
    return FakeClient(entities)


def client_without_customer() -> FakeClient:
    """A subscription entity with an amount but no email or phone at all."""
    return FakeClient(
        {"sub_1": {"id": "sub_1", "status": "halted", "amount": 99900, "customer": {}}}
    )


class FakePaymentLinkWriter:
    """Stands in for ``PaymentLinkWriter`` without touching the network."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.cancelled: list[str] = []
        self._counter = 0

    def create(
        self,
        amount_paise: int,
        reference_id: str,
        description: str,
        customer: dict[str, str],
        notify: bool = False,
        reminder_enable: bool = False,
    ) -> PaymentLink:
        self._counter += 1
        self.created.append(
            {
                "amount_paise": amount_paise,
                "reference_id": reference_id,
                "description": description,
                "customer": customer,
                "notify": notify,
            }
        )
        return PaymentLink(
            id=f"plink_{self._counter}",
            short_url=f"https://rzp.io/i/fake{self._counter}",
            status="created",
        )

    def cancel(self, link_id: str) -> bool:
        self.cancelled.append(link_id)
        return True


def fake_writer() -> FakePaymentLinkWriter:
    return FakePaymentLinkWriter()


# ---------------------------------------------------------------------------
# PaymentLinkWriter
# ---------------------------------------------------------------------------


def test_a_link_is_created_with_delivery_off_by_default():
    seen = {}

    def fake_post(path, body):
        seen["path"] = path
        seen["body"] = body
        return {"id": "plink_1", "short_url": "https://rzp.io/i/abc", "status": "created"}

    writer = PaymentLinkWriter(CONFIG, post=fake_post)
    link = writer.create(
        amount_paise=99900,
        reference_id="sub_1",
        description="d",
        customer=dict(CUSTOMER),
    )
    assert link.short_url == "https://rzp.io/i/abc"
    assert seen["body"]["notify"] == {"sms": False, "email": False}


def test_delivery_must_be_asked_for_explicitly():
    seen = {}

    def fake_post(path, body):
        seen["body"] = body
        return {"id": "plink_2", "short_url": "u", "status": "created"}

    PaymentLinkWriter(CONFIG, post=fake_post).create(
        amount_paise=1,
        reference_id="r",
        description="d",
        customer=dict(CUSTOMER),
        notify=True,
    )
    assert seen["body"]["notify"] == {"sms": True, "email": True}


def test_the_writer_refuses_live_credentials():
    live = RazorpayConfig(key_id="rzp_live_x", key_secret="s", webhook_secret="w")
    with pytest.raises(LiveModeRefused):
        PaymentLinkWriter(live)


def test_amounts_are_paise_and_never_rounded():
    seen = {}

    def fake_post(path, body):
        seen["body"] = body
        return {"id": "p", "short_url": "u", "status": "created"}

    PaymentLinkWriter(CONFIG, post=fake_post).create(
        amount_paise=99901,
        reference_id="r",
        description="d",
        customer=dict(CUSTOMER),
    )
    assert seen["body"]["amount"] == 99901


def test_cancel_reports_whether_the_link_actually_cancelled():
    def fake_post(path, body):
        assert path == "payment_links/plink_9/cancel"
        return {"id": "plink_9", "status": "cancelled"}

    assert PaymentLinkWriter(CONFIG, post=fake_post).cancel("plink_9") is True


# ---------------------------------------------------------------------------
# RazorpayTestRail wiring
# ---------------------------------------------------------------------------


def test_a_created_link_is_recorded_in_the_audit_log(tmp_path):
    audit = AuditLog(tmp_path / "a.db")
    rail = RazorpayTestRail(fake_client(), CONFIG, links=fake_writer(), audit=audit)
    url = rail.create_pay_now_link("sub_1", NOW)
    assert url and url.startswith("http")
    records = [r for r in audit.reconstruct("sub_1") if r.stage == "pay_now_link_created"]
    assert records, "the link exists in Razorpay and nowhere in our evidence"
    assert records[0].payload["short_url"] == url
    assert records[0].payload["notified"] is False


def test_the_real_rail_never_claims_a_link_was_paid():
    """Conversion arrives via reconciliation, never from the sending side."""
    rail = RazorpayTestRail(fake_client(), CONFIG, links=fake_writer())
    assert rail.deliver_pay_now_link("sub_1", NOW) is False


def test_a_subscription_with_no_contact_gets_no_link_and_no_guess(tmp_path):
    audit = AuditLog(tmp_path / "a.db")
    rail = RazorpayTestRail(client_without_customer(), CONFIG, links=fake_writer(), audit=audit)
    assert rail.create_pay_now_link("sub_1", NOW) is None
    stages = [r.stage for r in audit.reconstruct("sub_1")]
    assert "pay_now_link_unavailable" in stages


def test_no_links_writer_means_no_link_is_attempted():
    rail = RazorpayTestRail(fake_client(), CONFIG)
    assert rail.create_pay_now_link("sub_1", NOW) is None


def test_a_missing_amount_is_never_guessed(tmp_path):
    audit = AuditLog(tmp_path / "a.db")
    client = FakeClient({"sub_1": {"id": "sub_1", "status": "halted", "customer": dict(CUSTOMER)}})
    rail = RazorpayTestRail(client, CONFIG, links=fake_writer(), audit=audit)
    assert rail.create_pay_now_link("sub_1", NOW) is None
    records = [r for r in audit.reconstruct("sub_1") if r.stage == "pay_now_link_unavailable"]
    assert records[0].payload["missing"] == "amount"


def test_the_writer_sees_the_real_amount_and_reference():
    writer = fake_writer()
    rail = RazorpayTestRail(fake_client(), CONFIG, links=writer)
    rail.create_pay_now_link("sub_1", NOW)
    assert writer.created[0]["amount_paise"] == 99900
    assert writer.created[0]["reference_id"] == "sub_1"
    assert writer.created[0]["notify"] is False


def test_the_rail_still_refuses_to_be_built_on_a_live_config():
    live = RazorpayConfig(key_id="rzp_live_x", key_secret="s", webhook_secret="w")
    with pytest.raises(LiveModeRefused):
        RazorpayTestRail(fake_client(), live, links=fake_writer())
