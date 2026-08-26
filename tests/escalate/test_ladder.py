from datetime import datetime, timezone

from recoup.escalate.ladder import (
    TIER_CHANNELS,
    LadderState,
    assign_terminal,
    is_exhausted,
    may_enter,
    record_execution,
)
from recoup.models.core import Action
from recoup.models.enums import ActionType, FailureClass, TerminalState, Tier

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def state(failure_class=FailureClass.INSUFFICIENT_FUNDS, **overrides) -> LadderState:
    base = dict(subscription_id="sub_1", failure_class=failure_class)
    base.update(overrides)
    return LadderState(**base)


def action(action_type: ActionType, tier: Tier = Tier.T1_NOTIFY) -> Action:
    return Action(
        action_id="act_1",
        subscription_id="sub_1",
        type=action_type,
        scheduled_at=NOW,
        tier=tier,
        channel="email" if action_type is ActionType.SEND_MESSAGE else None,
        template_id="t1_notify_email" if action_type is ActionType.SEND_MESSAGE else None,
        free_text=None,
        reason="test",
    )


def test_tier_one_is_always_enterable():
    assert may_enter(state(), Tier.T1_NOTIFY) is True


def test_a_tier_may_not_be_skipped():
    assert may_enter(state(), Tier.T3_FINAL_NOTICE) is False


def test_a_tier_opens_once_the_previous_tier_has_actually_been_executed():
    s = state()
    record_execution(s, action(ActionType.SEND_MESSAGE, Tier.T1_NOTIFY), succeeded=True)
    assert may_enter(s, Tier.T2_REQUEST_ACTION) is True


def test_a_planned_but_blocked_tier_does_not_open_the_next_one():
    s = state()
    assert may_enter(s, Tier.T2_REQUEST_ACTION) is False


def test_a_recovered_subject_may_not_escalate_further():
    s = state()
    record_execution(s, action(ActionType.SEND_MESSAGE, Tier.T1_NOTIFY), succeeded=True)
    record_execution(s, action(ActionType.RETRY_CHARGE, Tier.T1_NOTIFY), succeeded=True)
    assert s.recovered is True
    assert may_enter(s, Tier.T2_REQUEST_ACTION) is False


def test_terminal_tier_is_always_reachable():
    assert may_enter(state(), Tier.T4_TERMINAL) is True


def test_a_revoked_mandate_goes_straight_to_terminal():
    s = state(FailureClass.MANDATE_REVOKED)
    assert may_enter(s, Tier.T2_REQUEST_ACTION) is False
    assert may_enter(s, Tier.T4_TERMINAL) is True


def test_an_opted_out_customer_goes_straight_to_terminal():
    s = state(opted_out=True)
    record_execution(s, action(ActionType.SEND_MESSAGE, Tier.T1_NOTIFY), succeeded=True)
    assert may_enter(s, Tier.T2_REQUEST_ACTION) is False


def test_executing_a_contact_spends_the_contact_budget():
    s = state()
    record_execution(s, action(ActionType.SEND_MESSAGE), succeeded=True)
    assert s.contacts_sent == 1


def test_executing_a_charge_spends_the_charge_budget_whether_or_not_it_worked():
    s = state()
    record_execution(s, action(ActionType.RETRY_CHARGE), succeeded=False)
    assert s.charge_retries_used == 1


def test_a_successful_charge_marks_the_subject_recovered():
    s = state()
    record_execution(s, action(ActionType.RETRY_CHARGE), succeeded=True)
    assert s.recovered is True


def test_a_successful_message_does_not_mark_the_subject_recovered():
    s = state()
    record_execution(s, action(ActionType.SEND_MESSAGE), succeeded=True)
    assert s.recovered is False


def test_an_escalation_action_flags_manual_review():
    s = state(FailureClass.RISK_DECLINE)
    record_execution(s, action(ActionType.ESCALATE_MANUAL_REVIEW, Tier.T4_TERMINAL), True)
    assert s.escalated_manual_review is True


def test_a_subject_with_both_budgets_spent_is_exhausted():
    s = state(contacts_sent=1, charge_retries_used=2)
    assert is_exhausted(s) is True


def test_a_subject_with_budget_remaining_is_not_exhausted():
    assert is_exhausted(state()) is False


def test_a_subject_whose_final_notice_failed_is_exhausted():
    s = state(FailureClass.INSTRUMENT_INVALID)
    record_execution(s, action(ActionType.SEND_MESSAGE, Tier.T3_FINAL_NOTICE), succeeded=True)
    assert is_exhausted(s) is True


def test_a_zero_budget_class_is_exhausted_from_the_start():
    assert is_exhausted(state(FailureClass.MANDATE_REVOKED)) is True


def test_terminal_states_reflect_what_actually_happened():
    assert assign_terminal(state(recovered=True)) is TerminalState.RECOVERED
    assert assign_terminal(state(FailureClass.MANDATE_REVOKED)) is TerminalState.VOLUNTARY_CHURN
    assert assign_terminal(state(escalated_manual_review=True)) is TerminalState.MANUAL_REVIEW
    assert assign_terminal(state()) is TerminalState.UNRECOVERED


def test_recovery_beats_every_other_terminal_state():
    s = state(FailureClass.MANDATE_REVOKED, recovered=True, escalated_manual_review=True)
    assert assign_terminal(s) is TerminalState.RECOVERED


def test_every_tier_declares_its_channels():
    assert TIER_CHANNELS[Tier.T1_NOTIFY] == ("email",)
    assert "sms" in TIER_CHANNELS[Tier.T3_FINAL_NOTICE]
    assert TIER_CHANNELS[Tier.T4_TERMINAL] == ()


def test_the_planner_chosen_starting_tier_is_enterable_without_lower_tiers_running():
    # A dead card opens at T2: asking for a new instrument, not sending a
    # neutral notice about a payment that was never going to succeed. Requiring
    # T1 first would shut that intervention permanently, which it once did.
    s = state(FailureClass.INSTRUMENT_INVALID, starting_tier=Tier.T2_REQUEST_ACTION)
    assert may_enter(s, Tier.T2_REQUEST_ACTION) is True
    assert may_enter(s, Tier.T3_FINAL_NOTICE) is False


def test_advancement_beyond_the_starting_tier_is_still_earned_by_execution():
    s = state(FailureClass.INSTRUMENT_INVALID, starting_tier=Tier.T2_REQUEST_ACTION)
    record_execution(s, action(ActionType.SEND_MESSAGE, Tier.T2_REQUEST_ACTION), succeeded=True)
    assert may_enter(s, Tier.T3_FINAL_NOTICE) is True


def test_a_starting_tier_does_not_open_tiers_above_it():
    s = state(starting_tier=Tier.T1_NOTIFY)
    assert may_enter(s, Tier.T2_REQUEST_ACTION) is False


def test_the_ladder_governs_contact_and_nothing_else():
    # Every tier is defined by a channel. A charge retry has no channel and the
    # customer never sees it, so it does not belong on a scale of contact
    # intensity. Gating retries by tier meant a notification blocked for being
    # outside the contact window silently killed the retries behind it.
    from recoup.escalate.ladder import LADDER_GOVERNED_TYPES

    assert ActionType.SEND_MESSAGE in LADDER_GOVERNED_TYPES
    assert ActionType.REQUEST_INSTRUMENT_UPDATE in LADDER_GOVERNED_TYPES
    assert ActionType.RETRY_CHARGE not in LADDER_GOVERNED_TYPES
    assert ActionType.STOP not in LADDER_GOVERNED_TYPES
    assert ActionType.ESCALATE_MANUAL_REVIEW not in LADDER_GOVERNED_TYPES
