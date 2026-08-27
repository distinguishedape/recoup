"""The live agent: does the deployed artefact actually run the pipeline?

Before this existed, a real Razorpay webhook produced exactly one audit record
and nothing else. These tests are the ones that would have caught that: they
assert the stages a real event passes through, not just that it was received.
"""

from datetime import datetime, timedelta, timezone

import pytest

from recoup.audit.log import AuditLog
from recoup.execute.rail import ChargeResult
from recoup.execute.razorpay_rail import ManualRetryUnsupported
from recoup.live.agent import LiveAgent, UndeliveredDispatcher
from recoup.models.core import FailureEvent
from recoup.models.enums import FailureClass

NOON_IST = datetime(2026, 8, 26, 6, 30, tzinfo=timezone.utc)  # 12:00 IST, inside the window
NIGHT_IST = datetime(2026, 8, 26, 21, 30, tzinfo=timezone.utc)  # 03:00 IST, outside it


class FrozenClock:
    def __init__(self, at: datetime) -> None:
        self._at = at

    @property
    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = at


class FakeRail:
    """A rail that can charge and can be told what to do."""

    def __init__(self, succeed: bool = False, converts: bool = True) -> None:
        self.succeed = succeed
        self.converts = converts
        self.charges: list[str] = []

    def charge(self, subscription_id: str, now: datetime) -> ChargeResult:
        self.charges.append(subscription_id)
        if self.succeed:
            return ChargeResult(succeeded=True)
        return ChargeResult(succeeded=False, error_reason="card_expired")

    def deliver_update_request(self, subscription_id: str, now: datetime) -> bool:
        return self.converts

    def create_pay_now_link(self, subscription_id: str, now: datetime) -> str | None:
        # `.invalid` is a reserved TLD that can never resolve, matching
        # SimulatedRail's convention -- a fake link can never be mistaken for
        # a real one, in a log, a rendered message, or an audit export.
        return f"https://example.invalid/pay/{subscription_id}"

    def deliver_pay_now_link(self, subscription_id: str, now: datetime) -> bool:
        return self.converts


class RefusingRail(FakeRail):
    """Stands in for the real Razorpay rail, which cannot manually retry (F2).

    A manual retry is unsupported, not link creation: Razorpay's payment
    links are a separate API this rail has no reason to refuse, so
    ``create_pay_now_link`` and ``deliver_pay_now_link`` are inherited from
    ``FakeRail`` unchanged -- only ``charge`` is overridden to raise.
    """

    def charge(self, subscription_id: str, now: datetime) -> ChargeResult:
        raise ManualRetryUnsupported("Razorpay exposes no manual-retry API")


def event(reason: str = "card_expired", sub: str = "sub_live_1") -> FailureEvent:
    return FailureEvent(
        event_id=f"evt_{sub}",
        subscription_id=sub,
        invoice_id=f"inv_{sub}",
        error_reason=reason,
        error_source="issuer",
        error_step="payment_authorization",
        attempt_number=1,
        occurred_at=NOON_IST,
        source="webhook",
    )


@pytest.fixture
def audit(tmp_path):
    log = AuditLog(tmp_path / "live.db")
    yield log
    log.close()


def stages(audit, sub_id: str) -> list[str]:
    return [r.stage for r in audit.reconstruct(sub_id)]


def test_a_real_event_gets_classified_and_planned_not_just_logged(audit):
    agent = LiveAgent(audit=audit, rail=FakeRail(), clock=FrozenClock(NOON_IST))
    decision = agent.handle(event(), amount_paise=99900)

    assert decision.classification.failure_class is FailureClass.INSTRUMENT_INVALID
    assert decision.planned > 0
    seen = stages(audit, "sub_live_1")
    assert "classify" in seen, seen
    assert "plan" in seen, seen


def test_the_agent_acts_and_does_not_stop_at_planning(audit):
    agent = LiveAgent(audit=audit, rail=FakeRail(), clock=FrozenClock(NOON_IST))
    decision = agent.handle(event(), amount_paise=99900)
    assert decision.executed > 0, "the agent planned but never acted"
    assert "execute" in stages(audit, "sub_live_1")


def test_a_rail_that_cannot_charge_is_recorded_not_faked(audit):
    """The real Razorpay rail raises rather than invent an outcome (F2)."""
    agent = LiveAgent(audit=audit, rail=RefusingRail(), clock=FrozenClock(NOON_IST))
    agent.handle(event("insufficient_funds"), amount_paise=99900)

    later = NOON_IST + timedelta(days=7)
    agent.clock.set(later)
    decision = agent.due(later)

    seen = stages(audit, "sub_live_1")
    assert "execute_unsupported" in seen, seen
    assert decision.unsupported > 0
    unsupported = [r for r in audit.reconstruct("sub_live_1") if r.stage == "execute_unsupported"]
    assert "manual-retry" in unsupported[0].payload["detail"]


def test_a_contact_at_three_in_the_morning_is_rescheduled_not_dropped(audit):
    agent = LiveAgent(audit=audit, rail=FakeRail(), clock=FrozenClock(NIGHT_IST))
    decision = agent.handle(event(), amount_paise=99900)

    assert decision.rescheduled > 0, "a night-time contact should be moved, not lost"
    moved = [r for r in audit.reconstruct("sub_live_1") if r.stage == "contact_rescheduled"]
    assert moved[0].payload["rule"] == "contact_window"
    assert moved[0].payload["rescheduled_to"] > moved[0].payload["original_time"]


def test_future_actions_are_held_rather_than_executed_early(audit):
    agent = LiveAgent(audit=audit, rail=FakeRail(), clock=FrozenClock(NOON_IST))
    decision = agent.handle(event("insufficient_funds"), amount_paise=99900)
    assert decision.held > 0, "a multi-day ladder should not all fire at once"

    before = len([r for r in audit.reconstruct("sub_live_1") if r.stage == "execute"])
    agent.clock.set(NOON_IST + timedelta(days=7))
    agent.due(NOON_IST + timedelta(days=7))
    after = len([r for r in audit.reconstruct("sub_live_1") if r.stage == "execute"])
    assert after > before, "held actions never ran once their time came"


def test_a_message_with_no_transport_is_not_recorded_as_delivered(audit):
    dispatcher = UndeliveredDispatcher()
    agent = LiveAgent(
        audit=audit, rail=FakeRail(), dispatcher=dispatcher, clock=FrozenClock(NOON_IST)
    )
    agent.handle(event(), amount_paise=99900)

    assert dispatcher.attempted, "the agent should have tried to send something"
    executions = [r for r in audit.reconstruct("sub_live_1") if r.stage == "execute"]
    messages = [r for r in executions if r.payload["action_type"] != "retry_charge"]
    assert messages, executions
    assert all(r.payload["succeeded"] is False for r in messages), (
        "a message with no transport configured must not be logged as sent"
    )


def test_a_revoked_mandate_is_never_charged(audit):
    rail = FakeRail()
    agent = LiveAgent(audit=audit, rail=rail, clock=FrozenClock(NOON_IST))
    agent.handle(event("subscription_cancelled", sub="sub_revoked"), amount_paise=99900)
    agent.clock.set(NOON_IST + timedelta(days=14))
    agent.due(NOON_IST + timedelta(days=14))
    assert rail.charges == [], "consent was withdrawn and the agent charged anyway"
