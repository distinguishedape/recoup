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
from recoup.models.core import Action, PolicyVerdict, Subscription
from recoup.models.enums import ActionType, Band, FailureClass, Tier
from recoup.policy.authorized import mint

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
    return mint(action_obj, PolicyVerdict(allowed=True, rule="all_rules_passed", detail="ok"))


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
