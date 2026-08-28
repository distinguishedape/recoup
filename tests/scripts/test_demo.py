"""The demo has to be honest at the speed a judge watches it.

Every line it prints is read back from the audit log or returned by the live
API. Nothing is asserted by the narration that was not recorded, because a demo
that says more than the log did is the fabrication this whole project refuses.
"""

from datetime import datetime, timezone

import pytest

from recoup.audit.log import AuditLog, new_record
from recoup.execute.razorpay_rail import ManualRetryUnsupported
from recoup.razorpay.config import RazorpayConfig
from scripts.demo import (
    AdvancingClock,
    find_failed_payment,
    narrate,
    narrate_stages,
    run_demo,
)

NOW = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)
CONFIG = RazorpayConfig(key_id="rzp_test_x", key_secret="s", webhook_secret="w")

FAILED_PAYMENT = {
    "id": "pay_DEMO1",
    "status": "failed",
    "amount": 99900,
    "order_id": "order_DEMO1",
    "created_at": 1787000000,
    # INSUFFICIENT_FUNDS, not an instrument cause: this is one of the two
    # classes whose plan ever schedules a PAY_NOW_LINK action at all (see
    # recoup/plan/fallback.py). run_demo advances the clock to that action's
    # own recorded time, so the fixture has to be a class that has one.
    "error_reason": "insufficient_funds",
    "error_source": "issuer",
    "error_step": "payment_authentication",
}

SUBSCRIPTION = {
    "id": "sub_DEMO1",
    "status": "halted",
    "plan_id": "plan_DEMO1",
    "customer_email": "demo@example.com",
    "customer_contact": "+919876543210",
    "short_url": "https://rzp.io/i/DEMOLINK",
}


class FakeReadClient:
    def __init__(self, payments, subscriptions):
        self._payments = payments
        self._subscriptions = subscriptions

    def payments(self, **params):
        return list(self._payments)

    def subscriptions(self, **params):
        return list(self._subscriptions)


def test_the_demo_picks_the_most_recent_failed_payment():
    client = FakeReadClient([FAILED_PAYMENT], [SUBSCRIPTION])
    payment, subscription_id = find_failed_payment(client, None)
    assert payment["id"] == "pay_DEMO1"
    assert subscription_id == "sub_DEMO1"


def test_an_account_with_no_failed_payment_says_so_instead_of_inventing_one():
    client = FakeReadClient([], [SUBSCRIPTION])
    with pytest.raises(SystemExit) as exit_info:
        find_failed_payment(client, None)
    assert "no failed payment" in str(exit_info.value).lower()


def test_the_narration_names_the_cause_the_plan_and_the_rule(tmp_path):
    """The three beats a watcher needs, in the order the pipeline produced them.

    INSUFFICIENT_FUNDS schedules its PAY_NOW_LINK action 25h after the
    initial notify -- run_demo reads that time off the subject's own `plan`
    audit record and advances LiveAgent.due() to it, so the link that comes
    back is the one this specific plan actually scheduled, not a delay-0
    action the fallback planner never produces.
    """
    audit = AuditLog(tmp_path / "demo.db")
    lines = run_demo(
        read_client=FakeReadClient([FAILED_PAYMENT], [SUBSCRIPTION]),
        rail=_StubRail(audit),
        audit=audit,
        subscription_id=None,
        now=NOW,
        clock=AdvancingClock(MIDDAY_IST),
    )
    joined = "\n".join(lines)
    assert "INSUFFICIENT_FUNDS" in joined
    assert "https://example.invalid/pay/sub_DEMO1" in joined
    assert any("rule" in line.lower() for line in lines)


def test_the_narration_reads_only_what_the_log_recorded(tmp_path):
    """No line may claim a stage the audit log does not contain."""
    audit = AuditLog(tmp_path / "demo.db")
    run_demo(
        read_client=FakeReadClient([FAILED_PAYMENT], [SUBSCRIPTION]),
        rail=_StubRail(audit),
        audit=audit,
        subscription_id=None,
        now=NOW,
        clock=AdvancingClock(MIDDAY_IST),
    )
    recorded = {r.stage for r in audit.reconstruct("sub_DEMO1")}
    lines = narrate_stages(audit, "sub_DEMO1")
    assert set(lines) <= recorded


@pytest.mark.xfail(reason="transcript recorded in Step 8", strict=False)
def test_a_dry_run_replays_the_recorded_transcript_without_a_network(capsys):
    """Judging wifi is not a dependency this demo is allowed to have."""
    from scripts.demo import main

    assert main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "recorded" in out.lower()
    assert "https://" in out



MIDDAY_IST = datetime(2026, 8, 27, 6, 30, tzinfo=timezone.utc)   # 12:00 IST, window open
EVENING_IST = datetime(2026, 8, 27, 13, 40, tzinfo=timezone.utc)  # 19:10 IST, window shut


class _StubRail:
    """Implements PaymentRail; returns a link that can never resolve.

    ``charge`` mirrors ``RazorpayTestRail.charge`` exactly: it raises
    ``ManualRetryUnsupported``, which LiveAgent catches and records as
    ``execute_unsupported`` rather than faking a result. The plan under test
    schedules a retry before the pay-now link (recoup/plan/fallback.py), so
    advancing the clock to the link's own time legitimately matures that
    retry too -- the stub has to answer the way the real rail would, not
    assert the retry away.

    ``create_pay_now_link`` also mirrors ``RazorpayTestRail``: it writes the
    created link to the audit log itself (spec R-A2 -- a created link is
    evidence and must be recoverable from the log, not only from the
    Razorpay dashboard). Without that, narrate() would have no recorded
    source for the URL to read back, since it never reads a rail's return
    value directly -- only what the log holds.
    """

    def __init__(self, audit):
        self._audit = audit

    def charge(self, subscription_id, now):
        raise ManualRetryUnsupported(
            "Razorpay exposes no manual-retry API for subscription invoices "
            "(spike finding F2)."
        )

    def deliver_update_request(self, subscription_id, now):
        return False

    def create_pay_now_link(self, subscription_id, now):
        url = "https://example.invalid/pay/sub_DEMO1"
        self._audit.append(
            new_record(
                subscription_id,
                now,
                "pay_now_link_created",
                {
                    "link_id": "plink_stub",
                    "short_url": url,
                    "status": "created",
                    "notified": False,
                },
            )
        )
        return url

    def deliver_pay_now_link(self, subscription_id, now):
        return False


def test_the_link_is_still_created_when_the_demo_runs_after_hours(tmp_path):
    """19:10 IST, contact window shut. This is the case that matters.

    Judging happens in the evening. The first contact is denied and rescheduled
    to 08:00, and the pay-now link sits behind it in the ladder -- so a demo
    that only followed reschedules of the link itself gave up here and produced
    no link at all, at exactly the hour someone is most likely to be watching.
    It follows any reschedule now, and the trace is better for it: the judge
    sees the compliance rule fire and the money still arrive.
    """
    audit = AuditLog(tmp_path / "evening.db")
    try:
        lines = run_demo(
            read_client=FakeReadClient([FAILED_PAYMENT], [SUBSCRIPTION]),
            rail=_StubRail(audit),
            audit=audit,
            subscription_id=None,
            now=NOW,
            clock=AdvancingClock(EVENING_IST),
        )
    finally:
        audit.close()
    joined = "\n".join(lines)
    assert "rescheduled" in joined, "the window should have denied the first contact"
    assert "https://example.invalid/pay/sub_DEMO1" in joined, (
        "the link must still be created after following the reschedule"
    )
