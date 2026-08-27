"""Detection: does the agent find revenue at risk, or only get told about it?

The fixtures are shaped exactly like the live account's real responses, which
were captured from a read-only probe before this module was written. No test
here touches the network.
"""

from datetime import datetime, timedelta, timezone

import pytest

from recoup.detect.scanner import (
    RiskKind,
    at_risk_paise,
    scan,
    scan_invoices,
    scan_orders,
    scan_subscriptions,
)
from recoup.razorpay.client import RazorpayReadClient
from recoup.razorpay.config import RazorpayConfig

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)


def ago(**kwargs) -> int:
    return int((NOW - timedelta(**kwargs)).timestamp())


CONFIG = RazorpayConfig(key_id="rzp_test_x", key_secret="s", webhook_secret="w")

RESPONSES = {
    "orders": [
        # never attempted -- a true abandonment, nothing to classify
        {"id": "order_never", "status": "created", "amount": 99900,
         "created_at": ago(hours=21), "attempts": 0},
        # attempted and declined -- a real failure the classifier can use
        {"id": "order_tried", "status": "attempted", "amount": 149900,
         "created_at": ago(hours=13), "attempts": 1},
        # too fresh to chase
        {"id": "order_fresh", "status": "created", "amount": 50000,
         "created_at": ago(minutes=5), "attempts": 0},
        # healthy
        {"id": "order_paid", "status": "paid", "amount": 49900,
         "created_at": ago(hours=30), "attempts": 3},
    ],
    "orders/order_tried/payments": [
        {"id": "pay_old", "status": "failed", "created_at": ago(hours=14),
         "order_id": "order_tried", "error_reason": "payment_failed",
         "error_source": "gateway", "error_step": "payment_authorization"},
        {"id": "pay_new", "status": "failed", "created_at": ago(hours=13),
         "order_id": "order_tried", "error_reason": "card_expired",
         "error_source": "issuer", "error_step": "payment_authorization"},
    ],
    "invoices": [
        {"id": "inv_overdue", "status": "issued", "amount": 1500000,
         "amount_paid": 0, "due_by": ago(days=6)},
        {"id": "inv_part", "status": "partially_paid", "amount": 200000,
         "amount_paid": 50000, "due_by": ago(days=2)},
        {"id": "inv_paid", "status": "paid", "amount": 49900, "due_by": ago(days=9)},
        {"id": "inv_future", "status": "issued", "amount": 30000,
         "due_by": int((NOW + timedelta(days=3)).timestamp())},
    ],
    "subscriptions": [
        {"id": "sub_halted", "status": "halted", "created_at": ago(days=40),
         "charge_at": ago(days=2)},
        {"id": "sub_active", "status": "active", "created_at": ago(days=10)},
    ],
}


def fake_fetch(path: str) -> dict:
    resource = path.split("?")[0]
    return {"items": RESPONSES.get(resource, [])}


@pytest.fixture
def client():
    return RazorpayReadClient(CONFIG, fetch=fake_fetch)


def test_an_abandoned_checkout_with_no_attempt_is_found_but_not_classified(client):
    signals = {s.entity_id: s for s in scan_orders(client, NOW)}
    never = signals["order_never"]
    assert never.kind is RiskKind.CHECKOUT_ABANDONMENT
    assert never.actionable is False, "nobody declined anything; there is no cause to classify"
    assert never.failure_event is None


def test_an_attempted_checkout_becomes_a_classifiable_failure(client):
    tried = {s.entity_id: s for s in scan_orders(client, NOW)}["order_tried"]
    assert tried.actionable is True
    assert tried.failure_event is not None
    # the most recent attempt, not the first
    assert tried.failure_event.error_reason == "card_expired"
    assert tried.failure_event.error_source == "issuer"


def test_a_fresh_checkout_is_left_alone(client):
    found = {s.entity_id for s in scan_orders(client, NOW)}
    assert "order_fresh" not in found, "chasing someone mid-checkout is worse than useless"


def test_a_paid_order_is_not_at_risk(client):
    assert "order_paid" not in {s.entity_id for s in scan_orders(client, NOW)}


def test_overdue_invoices_are_found_and_unpaid_balance_is_what_is_at_risk(client):
    signals = {s.entity_id: s for s in scan_invoices(client, NOW)}
    assert signals["inv_overdue"].amount_paise == 1500000
    # partially paid: only the remainder is at risk
    assert signals["inv_part"].amount_paise == 150000


def test_a_paid_or_not_yet_due_invoice_is_not_at_risk(client):
    found = {s.entity_id for s in scan_invoices(client, NOW)}
    assert "inv_paid" not in found
    assert "inv_future" not in found


def test_a_halted_subscription_is_found_without_a_webhook(client):
    """The same revenue the push would report, found by asking instead."""
    signals = {s.entity_id: s for s in scan_subscriptions(client, NOW)}
    assert "sub_halted" in signals
    assert signals["sub_halted"].kind is RiskKind.PAYMENT_FAILURE
    assert "sub_active" not in signals


def test_one_pass_covers_all_three_surfaces_named_in_the_brief(client):
    kinds = {s.kind for s in scan(client, NOW)}
    assert kinds == {
        RiskKind.PAYMENT_FAILURE,
        RiskKind.CHECKOUT_ABANDONMENT,
        RiskKind.OVERDUE_RECEIVABLE,
    }


def test_the_total_at_risk_is_reported(client):
    assert at_risk_paise(scan(client, NOW)) > 0


def test_nothing_in_the_read_client_can_write():
    """Detection runs on a schedule against a live account. It must be safe."""
    import inspect

    from recoup.razorpay import client as module

    source = inspect.getsource(module)
    for verb in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"', "method="):
        assert verb not in source, f"the read client should not be able to {verb}"
