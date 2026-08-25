from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from recoup.models.core import (
    Action,
    Classification,
    ExecutionResult,
    FailureEvent,
    InterventionPlan,
    PolicyVerdict,
    Subscription,
)
from recoup.models.enums import ActionType, FailureClass, Tier

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def test_subscription_holds_money_as_integer_paise():
    sub = Subscription(subscription_id="sub_1", customer_id="cust_1", plan_amount_paise=99900)
    assert sub.plan_amount_paise == 99900


def test_subscription_rejects_negative_money():
    with pytest.raises(ValidationError):
        Subscription(subscription_id="sub_1", customer_id="cust_1", plan_amount_paise=-1)


def test_failure_event_records_which_ingestion_source_produced_it():
    event = FailureEvent(
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
    assert event.source == "cohort"


def test_failure_event_rejects_an_unknown_source():
    with pytest.raises(ValidationError):
        FailureEvent(
            event_id="evt_1",
            subscription_id="sub_1",
            invoice_id="inv_1",
            error_reason="insufficient_funds",
            error_source="bank",
            error_step="payment_authorization",
            attempt_number=1,
            occurred_at=NOW,
            source="handwritten",
        )


def test_classification_confidence_is_bounded():
    with pytest.raises(ValidationError):
        Classification(
            failure_class=FailureClass.INSUFFICIENT_FUNDS,
            method="table",
            confidence=1.4,
            rationale="over-confident",
        )


def test_action_carries_its_escalation_tier():
    action = Action(
        action_id="act_1",
        subscription_id="sub_1",
        type=ActionType.SEND_MESSAGE,
        scheduled_at=NOW,
        tier=Tier.T1_NOTIFY,
        channel="email",
        template_id="t1_notify_email",
        free_text=None,
        reason="first notification after a funds decline",
    )
    assert action.tier is Tier.T1_NOTIFY


def test_models_are_frozen_so_a_stage_cannot_mutate_another_stages_data():
    verdict = PolicyVerdict(allowed=False, rule="contact_window", detail="22:14 IST")
    with pytest.raises(ValidationError):
        verdict.allowed = True


def test_intervention_plan_groups_actions_for_one_subscription():
    action = Action(
        action_id="act_1",
        subscription_id="sub_1",
        type=ActionType.RETRY_CHARGE,
        scheduled_at=NOW,
        tier=Tier.T1_NOTIFY,
        channel=None,
        template_id=None,
        free_text=None,
        reason="funds may have arrived",
    )
    plan = InterventionPlan(
        subscription_id="sub_1",
        failure_class=FailureClass.INSUFFICIENT_FUNDS,
        actions=[action],
    )
    assert plan.actions[0].action_id == "act_1"


def test_execution_result_costs_are_integer_paise():
    result = ExecutionResult(
        action_id="act_1",
        subscription_id="sub_1",
        succeeded=False,
        detail="declined: insufficient_funds",
        cost_paise=300,
        occurred_at=NOW,
    )
    assert result.cost_paise == 300
