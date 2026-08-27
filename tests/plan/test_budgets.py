from datetime import datetime, timezone

import pytest

from recoup.models.core import Action, InterventionPlan
from recoup.models.enums import ActionType, FailureClass, Tier
from recoup.plan.budgets import BUDGETS, action_id, budget_for, clamp_to_budget

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def act(index: int, action_type: ActionType) -> Action:
    return Action(
        action_id=action_id("sub_1", index),
        subscription_id="sub_1",
        type=action_type,
        scheduled_at=NOW,
        tier=Tier.T1_NOTIFY,
        channel="email" if action_type is ActionType.SEND_MESSAGE else None,
        template_id="t1_notify_email" if action_type is ActionType.SEND_MESSAGE else None,
        free_text=None,
        reason="test",
    )


@pytest.mark.parametrize(
    ("failure_class", "retries", "contacts"),
    [
        (FailureClass.INSUFFICIENT_FUNDS, 3, 2),
        (FailureClass.INSTRUMENT_INVALID, 0, 2),
        (FailureClass.MANDATE_REVOKED, 0, 0),
        (FailureClass.TRANSIENT_ISSUER, 3, 0),
        (FailureClass.RISK_DECLINE, 0, 0),
        (FailureClass.UNCLASSIFIED, 3, 2),
    ],
)
def test_budgets_match_the_spec_table(failure_class, retries, contacts):
    # The zero rows are the product's actual claim: causes a retry cannot fix
    # get none. The non-zero rows match what the baseline spends, because
    # under-spending a recoverable cause destroys value to save a few rupees.
    # INSUFFICIENT_FUNDS and UNCLASSIFIED carry 2 contacts, not 1: the notify
    # already spends one, and a pay-now link -- offering a way to act, not just
    # telling them -- spends the other. Two contacts across a five-day window
    # is not harassment, and this was widened before the pay-now link was
    # measured, for a structural reason (see docs/decisions.md).
    budget = budget_for(failure_class)
    assert budget.charge_retries == retries
    assert budget.contacts == contacts


def test_every_class_has_a_budget():
    assert set(BUDGETS) == set(FailureClass)


def test_action_ids_are_deterministic_and_unique_per_index():
    assert action_id("sub_1", 0) == action_id("sub_1", 0)
    assert action_id("sub_1", 0) != action_id("sub_1", 1)
    assert action_id("sub_1", 0) != action_id("sub_2", 0)


def test_clamping_drops_charge_retries_beyond_the_budget():
    plan = InterventionPlan(
        subscription_id="sub_1",
        failure_class=FailureClass.INSUFFICIENT_FUNDS,
        actions=[act(i, ActionType.RETRY_CHARGE) for i in range(5)],
    )
    clamped = clamp_to_budget(plan)
    assert len(clamped.actions) == 3


def test_clamping_drops_contacts_beyond_the_budget():
    plan = InterventionPlan(
        subscription_id="sub_1",
        failure_class=FailureClass.INSTRUMENT_INVALID,
        actions=[act(i, ActionType.SEND_MESSAGE) for i in range(6)],
    )
    assert len(clamp_to_budget(plan).actions) == 2


def test_an_instrument_update_request_counts_as_a_contact():
    plan = InterventionPlan(
        subscription_id="sub_1",
        failure_class=FailureClass.INSTRUMENT_INVALID,
        actions=[
            act(0, ActionType.REQUEST_INSTRUMENT_UPDATE),
            act(1, ActionType.SEND_MESSAGE),
            act(2, ActionType.SEND_MESSAGE),
        ],
    )
    assert len(clamp_to_budget(plan).actions) == 2


def test_clamping_keeps_the_earliest_actions_and_preserves_order():
    plan = InterventionPlan(
        subscription_id="sub_1",
        failure_class=FailureClass.INSUFFICIENT_FUNDS,
        actions=[act(i, ActionType.RETRY_CHARGE) for i in range(4)],
    )
    kept = [a.action_id for a in clamp_to_budget(plan).actions]
    assert kept == [action_id("sub_1", 0), action_id("sub_1", 1), action_id("sub_1", 2)]


def test_stop_and_escalate_are_never_clamped_away():
    plan = InterventionPlan(
        subscription_id="sub_1",
        failure_class=FailureClass.MANDATE_REVOKED,
        actions=[act(0, ActionType.STOP), act(1, ActionType.ESCALATE_MANUAL_REVIEW)],
    )
    assert len(clamp_to_budget(plan).actions) == 2


def test_a_zero_budget_class_loses_every_charge_and_contact():
    plan = InterventionPlan(
        subscription_id="sub_1",
        failure_class=FailureClass.MANDATE_REVOKED,
        actions=[act(0, ActionType.RETRY_CHARGE), act(1, ActionType.SEND_MESSAGE)],
    )
    assert clamp_to_budget(plan).actions == []


def test_a_pay_now_link_counts_as_a_customer_contact():
    from recoup.plan.budgets import CONTACT_ACTION_TYPES

    assert ActionType.PAY_NOW_LINK in CONTACT_ACTION_TYPES
