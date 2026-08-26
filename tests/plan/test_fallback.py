from datetime import datetime, timedelta, timezone

import pytest

from recoup.models.core import Classification, FailureEvent
from recoup.models.enums import ActionType, FailureClass, Tier
from recoup.plan.budgets import CONTACT_ACTION_TYPES, budget_for
from recoup.plan.fallback import build_plan

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def event() -> FailureEvent:
    return FailureEvent(
        event_id="evt_1",
        subscription_id="sub_1",
        invoice_id="inv_1",
        error_reason="insufficient_funds",
        error_source="bank",
        error_step="payment_authorization",
        attempt_number=1,
        occurred_at=NOW,
        source="cohort",
    )


def classification(failure_class: FailureClass) -> Classification:
    return Classification(
        failure_class=failure_class, method="table", confidence=0.99, rationale="test"
    )


@pytest.mark.parametrize("failure_class", list(FailureClass))
def test_every_class_gets_a_plan_that_respects_its_budget(failure_class):
    plan = build_plan(event(), classification(failure_class), NOW)
    budget = budget_for(failure_class)
    charges = [a for a in plan.actions if a.type is ActionType.RETRY_CHARGE]
    contacts = [a for a in plan.actions if a.type in CONTACT_ACTION_TYPES]
    assert len(charges) <= budget.charge_retries
    assert len(contacts) <= budget.contacts


def test_insufficient_funds_notifies_then_chases_the_pay_cycle():
    plan = build_plan(event(), classification(FailureClass.INSUFFICIENT_FUNDS), NOW)
    types = [a.type for a in plan.actions]
    assert types == [
        ActionType.SEND_MESSAGE,
        ActionType.RETRY_CHARGE,
        ActionType.RETRY_CHARGE,
        ActionType.RETRY_CHARGE,
    ]


def test_retries_are_spaced_out_in_time_not_stacked_on_the_same_instant():
    plan = build_plan(event(), classification(FailureClass.INSUFFICIENT_FUNDS), NOW)
    retries = [a for a in plan.actions if a.type is ActionType.RETRY_CHARGE]
    # Spread wide and late: a shortfall resolves when wages arrive, so the
    # probability is in the later attempts rather than the eager ones.
    assert retries[0].scheduled_at == NOW + timedelta(hours=24)
    assert retries[1].scheduled_at == NOW + timedelta(hours=72)
    assert retries[2].scheduled_at == NOW + timedelta(hours=120)


def test_instrument_invalid_asks_for_an_update_and_never_retries_the_dead_card():
    plan = build_plan(event(), classification(FailureClass.INSTRUMENT_INVALID), NOW)
    assert not [a for a in plan.actions if a.type is ActionType.RETRY_CHARGE]
    assert plan.actions[0].type is ActionType.REQUEST_INSTRUMENT_UPDATE


def test_instrument_invalid_escalates_from_tier_two_to_tier_three():
    plan = build_plan(event(), classification(FailureClass.INSTRUMENT_INVALID), NOW)
    assert [a.tier for a in plan.actions] == [Tier.T2_REQUEST_ACTION, Tier.T3_FINAL_NOTICE]


def test_mandate_revoked_stops_immediately_and_contacts_nobody():
    plan = build_plan(event(), classification(FailureClass.MANDATE_REVOKED), NOW)
    assert [a.type for a in plan.actions] == [ActionType.STOP]
    assert plan.actions[0].tier is Tier.T4_TERMINAL


def test_transient_issuer_retries_sooner_than_the_baseline_and_stays_silent():
    plan = build_plan(event(), classification(FailureClass.TRANSIENT_ISSUER), NOW)
    assert [a.type for a in plan.actions] == [
        ActionType.RETRY_CHARGE,
        ActionType.RETRY_CHARGE,
        ActionType.RETRY_CHARGE,
    ]
    # Twelve hours, not six. Measured against the timing model, six was too
    # eager: a good share of outages are still ongoing, so the attempt burns
    # and the decay penalty lands on the next one. Still ahead of the
    # baseline's flat daily ladder, and still without messaging anyone about
    # a problem on the bank's side.
    assert plan.actions[0].scheduled_at == NOW + timedelta(hours=12)
    assert all(a.channel is None for a in plan.actions)


def test_risk_decline_goes_to_a_human_rather_than_being_retried():
    plan = build_plan(event(), classification(FailureClass.RISK_DECLINE), NOW)
    assert [a.type for a in plan.actions] == [ActionType.ESCALATE_MANUAL_REVIEW]


def test_unclassified_gets_three_retries():
    plan = build_plan(event(), classification(FailureClass.UNCLASSIFIED), NOW)
    retries = [a for a in plan.actions if a.type is ActionType.RETRY_CHARGE]
    assert len(retries) == 3


def test_every_message_action_names_a_template_and_a_channel():
    for failure_class in FailureClass:
        plan = build_plan(event(), classification(failure_class), NOW)
        for action in plan.actions:
            if action.type in CONTACT_ACTION_TYPES:
                assert action.template_id
                assert action.channel


def test_every_action_carries_a_human_readable_reason():
    for failure_class in FailureClass:
        plan = build_plan(event(), classification(failure_class), NOW)
        for action in plan.actions:
            assert len(action.reason) > 10


def test_the_plan_is_deterministic():
    first = build_plan(event(), classification(FailureClass.UNCLASSIFIED), NOW)
    second = build_plan(event(), classification(FailureClass.UNCLASSIFIED), NOW)
    assert first == second
