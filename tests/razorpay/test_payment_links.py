from datetime import datetime, timezone

import pytest

from recoup.audit.log import AuditLog
from recoup.execute.razorpay_rail import RazorpayTestRail
from recoup.razorpay.config import LiveModeRefused, RazorpayConfig
from recoup.razorpay.payment_links import PaymentLink, PaymentLinkWriter

CONFIG = RazorpayConfig(key_id="rzp_test_x", key_secret="s", webhook_secret="w")
NOW = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)

CUSTOMER = {"name": "A", "email": "a@example.com", "contact": "+919876543210"}

# The REAL shape, verified against the live account (this is the whole point
# of these fixtures -- a fake that encodes the wrong shape tests the wrong
# assumption, not the API). A subscription entity has no ``amount`` and no
# nested ``customer``/``plan`` object: the amount lives on the plan resource
# (``GET plans/{id}`` -> ``item.amount``) and the contact fields are flat
# top-level keys (``customer_email``, ``customer_contact``).
DEFAULT_PLAN_ID = "plan_1"
DEFAULT_PLAN_AMOUNT = 99900


def real_subscription(**overrides: object) -> dict:
    entity = {
        "id": "sub_1",
        "status": "halted",
        "plan_id": DEFAULT_PLAN_ID,
        "customer_id": "cust_1",
        "customer_email": CUSTOMER["email"],
        "customer_contact": CUSTOMER["contact"],
        "short_url": "https://rzp.io/i/TESTLINK",
    }
    entity.update(overrides)
    return entity


def real_plan(plan_id: str = DEFAULT_PLAN_ID, amount: int = DEFAULT_PLAN_AMOUNT) -> dict:
    return {"id": plan_id, "item": {"amount": amount, "currency": "INR"}}


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


class FakePlanResource:
    def __init__(self, plans: dict[str, dict]) -> None:
        self._plans = plans

    def fetch(self, plan_id: str) -> dict:
        if plan_id not in self._plans:
            raise KeyError(plan_id)
        return self._plans[plan_id]


class FakeClient:
    """Satisfies the ``RazorpayClient`` protocol: ``.subscription.fetch(id)``
    and ``.plan.fetch(id)``."""

    def __init__(self, entities: dict[str, dict], plans: dict[str, dict] | None = None) -> None:
        self.subscription = FakeSubscriptionResource(entities)
        self.plan = FakePlanResource(plans if plans is not None else {DEFAULT_PLAN_ID: real_plan()})


def fake_client(**entities: dict) -> FakeClient:
    """A client whose one subscription has a plan (amount) and a full contact."""
    entities.setdefault("sub_1", real_subscription())
    return FakeClient(entities)


def client_without_customer() -> FakeClient:
    """A subscription entity with a plan but no email or phone at all."""
    return FakeClient(
        {"sub_1": real_subscription(customer_email="", customer_contact="")}
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


def test_a_missing_plan_id_is_never_guessed(tmp_path):
    audit = AuditLog(tmp_path / "a.db")
    client = FakeClient({"sub_1": real_subscription(plan_id=None)})
    rail = RazorpayTestRail(client, CONFIG, links=fake_writer(), audit=audit)
    assert rail.create_pay_now_link("sub_1", NOW) is None
    records = [r for r in audit.reconstruct("sub_1") if r.stage == "pay_now_link_unavailable"]
    assert records[0].payload["missing"] == "amount"


def test_the_writer_sees_the_real_amount_and_reference():
    writer = fake_writer()
    rail = RazorpayTestRail(fake_client(), CONFIG, links=writer)
    rail.create_pay_now_link("sub_1", NOW)
    assert writer.created[0]["amount_paise"] == DEFAULT_PLAN_AMOUNT
    assert writer.created[0]["reference_id"] == "sub_1"
    assert writer.created[0]["notify"] is False


def test_the_rail_still_refuses_to_be_built_on_a_live_config():
    live = RazorpayConfig(key_id="rzp_live_x", key_secret="s", webhook_secret="w")
    with pytest.raises(LiveModeRefused):
        RazorpayTestRail(fake_client(), live, links=fake_writer())


# ---------------------------------------------------------------------------
# The real subscription entity shape, verified against the live account:
# no ``amount``, no nested ``customer``/``plan`` dict on the subscription
# itself. Amount comes from the plan resource; contact fields are flat.
# ---------------------------------------------------------------------------


def test_the_amount_is_read_through_the_plan_not_the_subscription():
    """A real subscription entity has no ``amount`` -- only a ``plan_id``.

    The amount must be read via ``GET plans/{id}`` -> ``item.amount``, which
    is what this asserts by using a plan amount that differs from anything
    that might accidentally be present on the subscription fixture.
    """
    client = FakeClient(
        {"sub_1": real_subscription(plan_id="plan_distinct")},
        plans={"plan_distinct": real_plan("plan_distinct", amount=45000)},
    )
    writer = fake_writer()
    rail = RazorpayTestRail(client, CONFIG, links=writer)
    rail.create_pay_now_link("sub_1", NOW)
    assert writer.created[0]["amount_paise"] == 45000


def test_the_customer_contact_is_read_from_the_flat_subscription_fields():
    """A real subscription entity has no nested ``customer`` object --
    ``customer_email``/``customer_contact`` sit directly on the entity."""
    client = FakeClient(
        {
            "sub_1": real_subscription(
                customer_email="flat@example.com", customer_contact="+919876500000"
            )
        }
    )
    writer = fake_writer()
    rail = RazorpayTestRail(client, CONFIG, links=writer)
    rail.create_pay_now_link("sub_1", NOW)
    customer = writer.created[0]["customer"]
    assert customer["email"] == "flat@example.com"
    assert customer["contact"] == "+919876500000"


def test_a_plan_that_cannot_be_fetched_yields_no_link_and_no_guess(tmp_path):
    """``plan_id`` present but the plan lookup fails (deleted plan, API
    hiccup, wrong id) must not fall back to a guessed amount."""
    audit = AuditLog(tmp_path / "a.db")
    client = FakeClient({"sub_1": real_subscription(plan_id="plan_missing")}, plans={})
    rail = RazorpayTestRail(client, CONFIG, links=fake_writer(), audit=audit)
    assert rail.create_pay_now_link("sub_1", NOW) is None
    records = [r for r in audit.reconstruct("sub_1") if r.stage == "pay_now_link_unavailable"]
    assert records, "a failed plan fetch must still be recorded, not silently dropped"
    assert records[0].payload["missing"] == "amount"


# ---------------------------------------------------------------------------
# What a missing contact field is allowed to look like on the wire
# ---------------------------------------------------------------------------


def test_a_missing_phone_is_omitted_not_sent_as_an_empty_string():
    """Razorpay validates ``customer.contact`` server-side -- the findings file
    records a live 400 on a number it did not like. An empty string is a value
    we are asserting we have, and we do not have it. Everywhere else this
    project refuses to send a placeholder rather than send a wrong one; the
    wire body is not an exception."""
    client = FakeClient(
        {"sub_1": real_subscription(customer_email="only@example.com", customer_contact="")}
    )
    writer = fake_writer()
    RazorpayTestRail(client, CONFIG, links=writer).create_pay_now_link("sub_1", NOW)
    customer = writer.created[0]["customer"]
    assert "contact" not in customer
    assert customer["email"] == "only@example.com"


def test_a_missing_email_is_omitted_not_sent_as_an_empty_string():
    client = FakeClient(
        {"sub_1": real_subscription(customer_email="", customer_contact="+919876500000")}
    )
    writer = fake_writer()
    RazorpayTestRail(client, CONFIG, links=writer).create_pay_now_link("sub_1", NOW)
    customer = writer.created[0]["customer"]
    assert "email" not in customer
    assert customer["contact"] == "+919876500000"


def test_a_customer_with_no_name_anywhere_sends_no_name_key():
    """``name`` falls back to the customer id and then to nothing. Nothing means
    the key is absent, not present and blank.

    The overrides here are the shape a *real* entity has, per
    ``tests/fixtures/entity_shapes.json``: ``customer_id`` comes back ``null``
    and ``notes`` comes back as a list, not a dict. Which means this is not an
    edge case -- it is the ordinary live path, and the name is always absent on
    it.
    """
    client = FakeClient(
        {
            "sub_1": real_subscription(
                customer_id=None, notes=[], customer_email="a@example.com"
            )
        }
    )
    writer = fake_writer()
    RazorpayTestRail(client, CONFIG, links=writer).create_pay_now_link("sub_1", NOW)
    assert "name" not in writer.created[0]["customer"]


def test_a_full_contact_still_sends_every_field():
    """The omission is for absent values only; nothing is dropped otherwise."""
    writer = fake_writer()
    RazorpayTestRail(fake_client(), CONFIG, links=writer).create_pay_now_link("sub_1", NOW)
    assert set(writer.created[0]["customer"]) == {"name", "email", "contact"}


def test_declining_to_assert_payment_is_itself_recorded(tmp_path):
    """The reconstruct must not go quiet at the one point it says nothing.

    ``create_pay_now_link`` audits both outcomes and Task 8's reconciliation
    audits the eventual payment. Between them sat a decision that recorded
    nothing: asked whether the customer paid, the live rail always answers no,
    deliberately, because conversion is not knowable at send time. A reader
    replaying the subject saw a link created and then silence, which reads like
    a gap in the evidence rather than the refusal it is. Every other deliberate
    refusal in this codebase names itself in the log.
    """
    audit = AuditLog(tmp_path / "a.db")
    rail = RazorpayTestRail(fake_client(), CONFIG, links=fake_writer(), audit=audit)
    assert rail.deliver_pay_now_link("sub_1", NOW) is False
    records = [
        r for r in audit.reconstruct("sub_1") if r.stage == "pay_now_link_payment_unknown"
    ]
    assert records, "the rail refused to assert payment and left no trace of doing so"
    assert "reconcil" in records[0].payload["detail"].lower()


def test_the_delivery_record_does_not_appear_without_an_audit_log():
    """A rail built with no audit handle still answers, it just cannot record."""
    rail = RazorpayTestRail(fake_client(), CONFIG, links=fake_writer())
    assert rail.deliver_pay_now_link("sub_1", NOW) is False
