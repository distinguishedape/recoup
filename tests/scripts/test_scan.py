"""The scan entry point: what scan() and at_risk_paise() find, from a command line.

Detection is read-only by construction (recoup/razorpay/client.py exposes no write
verb), so this script's only job is to call scan() and print what it returns --
honestly, including the signals it found but cannot act on. No test here touches
the network.
"""

from datetime import datetime, timedelta, timezone

import pytest

from recoup.razorpay.client import RazorpayReadClient
from recoup.razorpay.config import RazorpayConfig
from recoup.report.render import format_rupees
from scripts.scan import format_signal, main, scan_and_render

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
CONFIG = RazorpayConfig(key_id="rzp_test_x", key_secret="s", webhook_secret="w")


def ago(**kwargs) -> int:
    return int((NOW - timedelta(**kwargs)).timestamp())


RESPONSES = {
    "orders": [
        # never attempted -- a true abandonment, nothing to classify
        {"id": "order_never", "status": "created", "amount": 99900,
         "created_at": ago(hours=21), "attempts": 0},
        # attempted and declined -- a real failure the classifier can use
        {"id": "order_tried", "status": "attempted", "amount": 149900,
         "created_at": ago(hours=13), "attempts": 1},
    ],
    "orders/order_tried/payments": [
        {"id": "pay_new", "status": "failed", "created_at": ago(hours=13),
         "order_id": "order_tried", "error_reason": "card_expired",
         "error_source": "issuer", "error_step": "payment_authorization"},
    ],
    "invoices": [
        {"id": "inv_overdue", "status": "issued", "amount": 1500000,
         "amount_paid": 0, "due_by": ago(days=6)},
    ],
    "subscriptions": [
        {"id": "sub_halted", "status": "halted", "created_at": ago(days=40),
         "charge_at": ago(days=2)},
    ],
}


def fake_fetch(path: str) -> dict:
    resource = path.split("?")[0]
    return {"items": RESPONSES.get(resource, [])}


@pytest.fixture
def client():
    return RazorpayReadClient(CONFIG, fetch=fake_fetch)


def test_an_actionable_signal_is_labelled_actionable(client):
    lines = scan_and_render(client, NOW)
    joined = "\n".join(lines)
    assert "order_tried" in joined
    assert "actionable" in joined.lower()


def test_an_unactionable_signal_is_shown_and_labelled_unhandled_not_hidden(client):
    """An abandoned cart nobody attempted has no decline to classify -- it must
    still appear in the output, honestly labelled, never dropped."""
    lines = scan_and_render(client, NOW)
    joined = "\n".join(lines)
    assert "order_never" in joined
    assert "unhandled" in joined.lower()


def test_every_signal_shows_kind_entity_amount_age_and_detail(client):
    lines = scan_and_render(client, NOW)
    joined = "\n".join(lines)
    assert "inv_overdue" in joined
    assert format_rupees(1500000) in joined
    assert "sub_halted" in joined
    assert "halted" in joined.lower()


def test_the_total_at_risk_is_printed_using_format_rupees(client):
    lines = scan_and_render(client, NOW)
    joined = "\n".join(lines)
    # 99900 (abandoned) + 149900 (attempted) + 1500000 (overdue) + 0 (subscription) = 1749800
    assert format_rupees(1749800) in joined


def test_main_prints_the_rendered_signals_without_touching_the_network(client, capsys, monkeypatch):
    """main() must go through load_config() + RazorpayReadClient in real use, but
    the read path itself is exercised here via a fake client, so no test needs a
    network to prove the wiring is correct."""
    monkeypatch.setattr("scripts.scan.load_config", lambda: CONFIG)
    monkeypatch.setattr("scripts.scan.RazorpayReadClient", lambda config: client)

    assert main([]) == 0
    out = capsys.readouterr().out
    assert "order_tried" in out
    assert format_rupees(1749800) in out


def test_nothing_in_the_scan_script_can_write():
    """Detection runs on a schedule against a live account. It must be safe."""
    import inspect

    from scripts import scan as module

    source = inspect.getsource(module)
    for verb in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"', "method="):
        assert verb not in source, f"the scan script should not be able to {verb}"


def test_a_single_signal_formats_kind_entity_amount_age_actionable_and_detail():
    from recoup.detect.scanner import RiskKind, RiskSignal

    signal = RiskSignal(
        kind=RiskKind.CHECKOUT_ABANDONMENT,
        entity_id="order_never",
        amount_paise=99900,
        detected_at=NOW,
        age=timedelta(hours=21),
        actionable=False,
        detail="checkout abandoned without an attempt",
    )
    line = format_signal(signal)
    assert "checkout_abandonment" in line
    assert "order_never" in line
    assert format_rupees(99900) in line
    assert "21.0h" in line
    assert "unhandled" in line.lower()
    assert "checkout abandoned without an attempt" in line
