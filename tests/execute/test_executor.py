import random
from datetime import datetime, timezone

import pytest

from recoup.audit.log import AuditLog
from recoup.clock.virtual import VirtualClock
from recoup.execute.executor import (
    CHANNEL_COST_PAISE,
    CHARGE_ATTEMPT_COST_PAISE,
    Executor,
    SimulatedDispatcher,
    cost_of,
    render_context_for,
)
from recoup.execute.rail import SimSubject, SimulatedRail, canonical_decline
from recoup.models.core import Action, Subscription
from recoup.models.enums import ActionType, Band, FailureClass, Tier
from recoup.policy.engine import authorize
from recoup.policy.rules import PolicyContext

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
SUB = Subscription(subscription_id="sub_1", customer_id="cust_1", plan_amount_paise=99900)
CONTEXT = render_context_for(SUB)


def action(action_type: ActionType, template_id: str | None = None, channel: str | None = None):
    return Action(
        action_id="act_1",
        subscription_id="sub_1",
        type=action_type,
        scheduled_at=NOW,
        tier=Tier.T1_NOTIFY,
        channel=channel,
        template_id=template_id,
        free_text=None,
        reason="test",
    )


def authorized(action_obj: Action):
    """Route the action through the real policy engine, as production does.

    There is deliberately no shortcut for building an AuthorizedAction, so the
    tests obtain one the same way the orchestrator does.
    """
    result, verdict = authorize(
        action_obj,
        PolicyContext(
            now=NOW,
            failure_class=FailureClass.UNCLASSIFIED,
            contacts_sent=0,
            charge_retries_used=0,
            opted_out=False,
            promise_to_pay_until=None,
            last_contact_at=None,
        ),
    )
    assert result is not None, f"fixture action was denied by {verdict.rule}: {verdict.detail}"
    return result


def pay_now_action():
    return action(ActionType.PAY_NOW_LINK, "t2_pay_now_email", "email")


def render_context():
    return dict(CONTEXT)


class ConfigurablePayNowRail:
    """A rail double for pay-now tests: link creation and conversion are each
    configurable directly, independent of SimulatedRail's probability model."""

    def __init__(self, link_url: str | None, converts: bool) -> None:
        self._link_url = link_url
        self._converts = converts

    def create_pay_now_link(self, subscription_id: str, now: datetime) -> str | None:
        return self._link_url

    def deliver_pay_now_link(self, subscription_id: str, now: datetime) -> bool:
        return self._converts


class ConfigurableDispatcher:
    """Like SimulatedDispatcher, but whether delivery succeeds is configurable."""

    def __init__(self, delivers: bool = True) -> None:
        self.sent: list[tuple[str, str, object]] = []
        self._delivers = delivers

    def send(self, subscription_id, channel, message, now):
        self.sent.append((subscription_id, channel, message))
        return self._delivers


@pytest.fixture()
def executor_with(tmp_path):
    """Factory fixture: build an Executor wired to configurable pay-now fakes.

    ``converts`` controls whether the rail reports the pay-now link as paid.
    ``delivers`` controls whether the dispatcher reports the message as sent.
    ``link_url`` controls what the rail hands back when asked to create a link.
    """
    logs: list[AuditLog] = []

    def _make(
        converts: bool = True,
        delivers: bool = True,
        link_url: str | None = "https://example.invalid/pay/sub_1",
    ):
        rail = ConfigurablePayNowRail(link_url=link_url, converts=converts)
        dispatcher = ConfigurableDispatcher(delivers=delivers)
        audit = AuditLog(tmp_path / f"audit_{len(logs)}.db")
        logs.append(audit)
        clock = VirtualClock(NOW)
        return Executor(rail, dispatcher, audit, clock), rail, dispatcher

    yield _make
    for log in logs:
        log.close()


@pytest.fixture()
def harness(tmp_path):
    reason, source, step = canonical_decline(FailureClass.INSUFFICIENT_FUNDS)
    subject = SimSubject(
        subscription_id="sub_1",
        latent_class=FailureClass.INSUFFICIENT_FUNDS,
        plan_amount_paise=99900,
        declined_reason=reason,
        error_source=source,
        error_step=step,
        first_failure_at=NOW,
    )
    rail = SimulatedRail({"sub_1": subject}, Band.MID, random.Random(3))
    dispatcher = SimulatedDispatcher()
    audit = AuditLog(tmp_path / "audit.db")
    clock = VirtualClock(NOW)
    yield Executor(rail, dispatcher, audit, clock), dispatcher, audit, subject
    audit.close()


def test_render_context_exposes_rupees_not_paise():
    assert CONTEXT["amount_inr"] == "999.00"
    assert CONTEXT["customer_id"] == "cust_1"
    assert CONTEXT["update_link"].startswith("http")


def test_the_executor_refuses_a_raw_unauthorized_action(harness):
    executor, _, _, _ = harness
    with pytest.raises(TypeError):
        executor.execute(action(ActionType.RETRY_CHARGE), CONTEXT)


def test_a_charge_goes_to_the_rail_and_costs_the_charge_fee(harness):
    executor, _, _, subject = harness
    result = executor.execute(authorized(action(ActionType.RETRY_CHARGE)), CONTEXT)
    assert subject.attempts_made == 1
    assert result.cost_paise == CHARGE_ATTEMPT_COST_PAISE


def test_a_failed_charge_records_the_decline_reason(harness):
    executor, _, _, _ = harness
    result = executor.execute(authorized(action(ActionType.RETRY_CHARGE)), CONTEXT)
    if not result.succeeded:
        assert "insufficient_funds" in result.detail


def test_a_message_is_rendered_and_dispatched(harness):
    executor, dispatcher, _, _ = harness
    executor.execute(
        authorized(action(ActionType.SEND_MESSAGE, "t1_notify_email", "email")), CONTEXT
    )
    assert len(dispatcher.sent) == 1
    assert "999.00" in dispatcher.sent[0][2].body


def test_a_message_costs_its_channel_rate(harness):
    executor, _, _, _ = harness
    email = executor.execute(
        authorized(action(ActionType.SEND_MESSAGE, "t1_notify_email", "email")), CONTEXT
    )
    assert email.cost_paise == CHANNEL_COST_PAISE["email"]


def test_an_sms_costs_more_than_an_email():
    assert CHANNEL_COST_PAISE["sms"] > CHANNEL_COST_PAISE["email"]


def test_an_instrument_update_request_asks_the_rail_whether_it_converted(harness):
    executor, _, _, subject = harness
    result = executor.execute(
        authorized(
            action(ActionType.REQUEST_INSTRUMENT_UPDATE, "t2_update_instrument_email", "email")
        ),
        CONTEXT,
    )
    assert result.succeeded == subject.instrument_updated


def test_stopping_costs_nothing_and_always_succeeds(harness):
    executor, _, _, _ = harness
    result = executor.execute(authorized(action(ActionType.STOP)), CONTEXT)
    assert result.succeeded is True
    assert result.cost_paise == 0


def test_escalating_to_a_human_costs_nothing_and_always_succeeds(harness):
    executor, _, _, _ = harness
    result = executor.execute(authorized(action(ActionType.ESCALATE_MANUAL_REVIEW)), CONTEXT)
    assert result.succeeded is True
    assert result.cost_paise == 0


def test_every_execution_writes_an_audit_record_naming_the_rule_that_allowed_it(harness):
    executor, _, audit, _ = harness
    executor.execute(authorized(action(ActionType.RETRY_CHARGE)), CONTEXT)
    records = audit.reconstruct("sub_1")
    assert len(records) == 1
    assert records[0].stage == "execute"
    assert records[0].payload["verdict_rule"] == "all_rules_passed"
    assert records[0].payload["action_type"] == "retry_charge"


def test_the_audit_record_carries_the_virtual_time_not_the_wall_clock(harness):
    executor, _, audit, _ = harness
    executor.execute(authorized(action(ActionType.RETRY_CHARGE)), CONTEXT)
    assert audit.reconstruct("sub_1")[0].virtual_time == NOW


def test_cost_of_agrees_with_what_execution_actually_charges():
    assert cost_of(action(ActionType.RETRY_CHARGE)) == CHARGE_ATTEMPT_COST_PAISE
    assert cost_of(action(ActionType.SEND_MESSAGE, "t1_notify_email", "sms")) == (
        CHANNEL_COST_PAISE["sms"]
    )
    assert cost_of(action(ActionType.STOP)) == 0


def test_a_pay_now_link_is_sent_and_its_outcome_recorded(executor_with):
    executor, rail, dispatcher = executor_with(converts=True)
    result = executor.execute(authorized(pay_now_action()), render_context())
    assert result.succeeded is True
    assert "paid" in result.detail.lower()


def test_a_pay_now_link_that_is_not_paid_is_not_a_failure_to_send(executor_with):
    executor, rail, dispatcher = executor_with(converts=False)
    result = executor.execute(authorized(pay_now_action()), render_context())
    assert result.succeeded is False
    assert dispatcher.sent, "the message should still have gone out"


def test_an_undeliverable_pay_now_link_never_counts_as_paid(executor_with):
    """No transport means no link reached anyone, so conversion is impossible."""
    executor, rail, dispatcher = executor_with(converts=True, delivers=False)
    result = executor.execute(authorized(pay_now_action()), render_context())
    assert result.succeeded is False
    assert "not delivered" in result.detail


def test_an_action_with_no_link_available_is_not_reported_as_sent(executor_with):
    executor, rail, dispatcher = executor_with(link_url=None)
    result = executor.execute(authorized(pay_now_action()), render_context())
    assert result.succeeded is False
    assert "no pay-now link" in result.detail
    assert not dispatcher.sent, "nothing should be sent without a link in it"
