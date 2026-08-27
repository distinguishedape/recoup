from datetime import datetime, timedelta, timezone

from recoup.models.core import Action
from recoup.models.enums import ActionType, FailureClass, Tier
from recoup.plan.budgets import budget_for
from recoup.policy.rules import (
    IST,
    PolicyContext,
    class_retry_budget,
    contact_rate_limit,
    contact_window,
    opt_out_stop,
    pay_now_link_causes,
    promise_to_pay_suppression,
    template_allowlist,
)


def at_ist(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 25, hour, minute, tzinfo=IST).astimezone(timezone.utc)


def message(scheduled_at: datetime, template_id: str = "t1_notify_email") -> Action:
    return Action(
        action_id="act_1",
        subscription_id="sub_1",
        type=ActionType.SEND_MESSAGE,
        scheduled_at=scheduled_at,
        tier=Tier.T1_NOTIFY,
        channel="email",
        template_id=template_id,
        free_text=None,
        reason="test",
    )


def charge(scheduled_at: datetime) -> Action:
    return Action(
        action_id="act_2",
        subscription_id="sub_1",
        type=ActionType.RETRY_CHARGE,
        scheduled_at=scheduled_at,
        tier=Tier.T1_NOTIFY,
        channel=None,
        template_id=None,
        free_text=None,
        reason="test",
    )


def pay_now(scheduled_at: datetime = at_ist(10)) -> Action:
    return Action(
        action_id="act_3",
        subscription_id="sub_1",
        type=ActionType.PAY_NOW_LINK,
        scheduled_at=scheduled_at,
        tier=Tier.T2_REQUEST_ACTION,
        channel="email",
        template_id="t2_pay_now_email",
        free_text=None,
        reason="test",
    )


def context(**overrides) -> PolicyContext:
    base = dict(
        now=at_ist(10),
        failure_class=FailureClass.INSUFFICIENT_FUNDS,
        contacts_sent=0,
        charge_retries_used=0,
        opted_out=False,
        promise_to_pay_until=None,
        last_contact_at=None,
        replacement_instrument_id=None,
        charged_instrument_ids=frozenset(),
    )
    base.update(overrides)
    return PolicyContext(**base)


def test_a_message_inside_the_window_is_allowed():
    assert contact_window(message(at_ist(10)), context(now=at_ist(10))).allowed


def test_a_message_at_three_in_the_morning_is_denied():
    verdict = contact_window(message(at_ist(3)), context(now=at_ist(3)))
    assert verdict.allowed is False
    assert verdict.rule == "contact_window"


def test_a_message_at_seven_thirty_pm_is_denied():
    assert contact_window(message(at_ist(19, 30)), context(now=at_ist(19, 30))).allowed is False


def test_the_window_boundaries_are_inclusive_at_eight_and_exclusive_at_nineteen():
    assert contact_window(message(at_ist(8, 0)), context(now=at_ist(8, 0))).allowed is True
    assert contact_window(message(at_ist(18, 59)), context(now=at_ist(18, 59))).allowed is True
    assert contact_window(message(at_ist(19, 0)), context(now=at_ist(19, 0))).allowed is False


def test_a_charge_retry_at_three_in_the_morning_is_fine_nobody_is_woken_up():
    assert contact_window(charge(at_ist(3)), context(now=at_ist(3))).allowed is True


def test_an_opted_out_customer_is_never_contacted():
    assert opt_out_stop(message(at_ist(10)), context(opted_out=True)).allowed is False


def test_an_opted_out_customer_can_still_be_charged_under_a_live_mandate():
    assert opt_out_stop(charge(at_ist(10)), context(opted_out=True)).allowed is True


def test_two_contacts_inside_the_rate_limit_window_are_refused():
    ctx = context(now=at_ist(10), last_contact_at=at_ist(10) - timedelta(hours=3))
    assert contact_rate_limit(message(at_ist(10)), ctx).allowed is False


def test_a_contact_after_the_rate_limit_window_is_allowed():
    ctx = context(now=at_ist(10), last_contact_at=at_ist(10) - timedelta(hours=25))
    assert contact_rate_limit(message(at_ist(10)), ctx).allowed is True


def test_the_first_contact_is_never_rate_limited():
    assert contact_rate_limit(message(at_ist(10)), context()).allowed is True


def test_a_template_off_the_allowlist_is_denied():
    verdict = template_allowlist(message(at_ist(10), "t9_threatening_letter"), context())
    assert verdict.allowed is False
    assert verdict.rule == "template_allowlist"


def test_a_message_with_no_template_at_all_is_denied():
    action = message(at_ist(10)).model_copy(update={"template_id": None})
    assert template_allowlist(action, context()).allowed is False


def test_free_text_is_denied_even_alongside_a_valid_template():
    action = message(at_ist(10)).model_copy(update={"free_text": "pay up"})
    assert template_allowlist(action, context()).allowed is False


def test_a_charge_needs_no_template():
    assert template_allowlist(charge(at_ist(10)), context()).allowed is True


def test_a_pay_now_template_sent_as_a_plain_message_is_denied():
    # t2_pay_now_email needs {pay_now_url} in its context, which only the
    # PAY_NOW_LINK execution branch supplies. Sent as a plain SEND_MESSAGE it
    # would crash at render time instead of being caught here.
    action = message(at_ist(10), "t2_pay_now_email")
    verdict = template_allowlist(action, context())
    assert verdict.allowed is False
    assert verdict.rule == "template_allowlist"


def test_a_pay_now_link_using_the_pay_now_template_is_allowed():
    assert template_allowlist(pay_now(), context()).allowed is True


def test_a_live_promise_to_pay_suppresses_both_contact_and_charge():
    ctx = context(now=at_ist(10), promise_to_pay_until=at_ist(10) + timedelta(days=2))
    assert promise_to_pay_suppression(message(at_ist(10)), ctx).allowed is False
    assert promise_to_pay_suppression(charge(at_ist(10)), ctx).allowed is False


def test_an_expired_promise_to_pay_suppresses_nothing():
    ctx = context(now=at_ist(10), promise_to_pay_until=at_ist(10) - timedelta(days=2))
    assert promise_to_pay_suppression(message(at_ist(10)), ctx).allowed is True


def test_a_charge_beyond_the_class_budget_is_denied():
    ctx = context(failure_class=FailureClass.INSUFFICIENT_FUNDS, charge_retries_used=3)
    assert class_retry_budget(charge(at_ist(10)), ctx).allowed is False


def test_a_contact_beyond_the_class_budget_is_denied():
    # "Beyond the budget" means fully spent, not any particular number -- derive
    # it from the budget itself so a future budget change cannot make this
    # fixture mean "still within budget" without the test noticing.
    at_budget = budget_for(FailureClass.INSUFFICIENT_FUNDS).contacts
    ctx = context(failure_class=FailureClass.INSUFFICIENT_FUNDS, contacts_sent=at_budget)
    assert class_retry_budget(message(at_ist(10)), ctx).allowed is False


def test_a_dead_card_can_never_be_charged_however_the_plan_was_built():
    ctx = context(failure_class=FailureClass.INSTRUMENT_INVALID, charge_retries_used=0)
    assert class_retry_budget(charge(at_ist(10)), ctx).allowed is False


def test_a_replacement_instrument_may_be_charged_once():
    ctx = context(
        failure_class=FailureClass.INSTRUMENT_INVALID,
        replacement_instrument_id="sub_1:instrument:v1",
    )
    assert class_retry_budget(charge(at_ist(10)), ctx).allowed is True


def test_a_replacement_instrument_may_not_be_charged_twice():
    ctx = context(
        failure_class=FailureClass.INSTRUMENT_INVALID,
        replacement_instrument_id="sub_1:instrument:v1",
        charged_instrument_ids=frozenset({"sub_1:instrument:v1"}),
    )
    assert class_retry_budget(charge(at_ist(10)), ctx).allowed is False


def test_the_bound_survives_a_caller_that_forgets_to_record_anything():
    # The weakness this closes. The bound used to be a count the caller kept,
    # so a stale snapshot or a retry loop that never incremented it granted
    # unlimited charges on a cause whose budget is deliberately zero. Identity
    # cannot be reset by accident: charging twice means naming the same
    # instrument twice, and the engine can see that.
    ctx = context(
        failure_class=FailureClass.INSTRUMENT_INVALID,
        replacement_instrument_id="sub_1:instrument:v1",
        charged_instrument_ids=frozenset({"sub_1:instrument:v1"}),
    )
    assert all(class_retry_budget(charge(at_ist(10)), ctx).allowed is False for _ in range(10))


def test_a_second_genuine_replacement_is_chargeable_again():
    # Bounded by real conversions, not by an arbitrary constant: if the
    # customer actually supplies another card, that card gets its one charge.
    ctx = context(
        failure_class=FailureClass.INSTRUMENT_INVALID,
        replacement_instrument_id="sub_1:instrument:v2",
        charged_instrument_ids=frozenset({"sub_1:instrument:v1"}),
    )
    assert class_retry_budget(charge(at_ist(10)), ctx).allowed is True


def test_a_replacement_does_not_unlock_charging_for_a_risk_block():
    ctx = context(
        failure_class=FailureClass.RISK_DECLINE,
        replacement_instrument_id="sub_1:instrument:v1",
    )
    assert class_retry_budget(charge(at_ist(10)), ctx).allowed is False


def test_stopping_is_always_permitted_by_every_single_rule():
    stop = charge(at_ist(3)).model_copy(update={"type": ActionType.STOP})
    ctx = context(
        now=at_ist(3),
        opted_out=True,
        charge_retries_used=99,
        contacts_sent=99,
        last_contact_at=at_ist(3),
        promise_to_pay_until=at_ist(3) + timedelta(days=30),
    )
    for rule in (
        opt_out_stop,
        promise_to_pay_suppression,
        class_retry_budget,
        template_allowlist,
        contact_window,
        contact_rate_limit,
    ):
        assert rule(stop, ctx).allowed is True, f"{rule.__name__} blocked a stop"


def test_escalating_to_a_human_is_also_permitted_by_every_rule():
    escalate = charge(at_ist(3)).model_copy(update={"type": ActionType.ESCALATE_MANUAL_REVIEW})
    ctx = context(
        now=at_ist(3),
        opted_out=True,
        charge_retries_used=99,
        contacts_sent=99,
        last_contact_at=at_ist(3),
        promise_to_pay_until=at_ist(3) + timedelta(days=30),
    )
    for rule in (
        opt_out_stop,
        promise_to_pay_suppression,
        class_retry_budget,
        template_allowlist,
        contact_window,
        contact_rate_limit,
    ):
        assert rule(escalate, ctx).allowed is True, f"{rule.__name__} blocked an escalation"


def test_a_risk_block_is_not_undone_by_the_customer_adding_a_new_card():
    # The zero charge budget on a risk decline is a decision about the
    # transaction, not about the instrument. A new card does not answer it.
    ctx = context(
        failure_class=FailureClass.RISK_DECLINE,
        instrument_updated=True,
        post_update_charges_used=0,
    )
    assert class_retry_budget(charge(at_ist(10)), ctx).allowed is False


def test_a_revoked_mandate_is_not_undone_by_the_customer_adding_a_new_card():
    ctx = context(
        failure_class=FailureClass.MANDATE_REVOKED,
        instrument_updated=True,
        post_update_charges_used=0,
    )
    assert class_retry_budget(charge(at_ist(10)), ctx).allowed is False


def test_a_naive_timestamp_is_refused_rather_than_assumed_to_be_local():
    from datetime import datetime as _dt

    import pytest as _pytest

    with _pytest.raises(Exception):
        context(now=_dt(2026, 8, 25, 10, 0))


def test_a_time_inside_the_window_is_returned_unchanged():
    from recoup.policy.rules import next_permitted_contact_time

    assert next_permitted_contact_time(at_ist(10)) == at_ist(10)


def test_a_late_evening_contact_moves_to_the_next_morning():
    from recoup.policy.rules import next_permitted_contact_time

    assert next_permitted_contact_time(at_ist(21, 30)) == at_ist(8, 0) + timedelta(days=1)


def test_an_early_morning_contact_moves_to_opening_time_the_same_day():
    from recoup.policy.rules import next_permitted_contact_time

    assert next_permitted_contact_time(at_ist(3)) == at_ist(8, 0)


def test_the_window_boundaries_reschedule_correctly():
    from recoup.policy.rules import next_permitted_contact_time

    assert next_permitted_contact_time(at_ist(8, 0)) == at_ist(8, 0)
    assert next_permitted_contact_time(at_ist(18, 59)) == at_ist(18, 59)
    assert next_permitted_contact_time(at_ist(19, 0)) == at_ist(8, 0) + timedelta(days=1)


def test_a_rescheduled_time_always_passes_the_rule_that_rejected_it():
    # The property that matters: rescheduling must actually resolve the denial,
    # or the action orbits the clock being refused forever.
    from recoup.policy.rules import next_permitted_contact_time

    for hour in range(24):
        moved = next_permitted_contact_time(at_ist(hour))
        assert contact_window(message(moved), context(now=moved)).allowed


def test_rescheduling_is_bounded_by_a_declared_cap():
    from recoup.policy.rules import MAX_RESCHEDULES

    assert 1 <= MAX_RESCHEDULES <= 5


def test_a_shortfall_may_be_offered_a_pay_now_link():
    verdict = pay_now_link_causes(pay_now(), context(failure_class=FailureClass.INSUFFICIENT_FUNDS))
    assert verdict.allowed


def test_an_unknown_cause_may_be_offered_a_pay_now_link():
    verdict = pay_now_link_causes(pay_now(), context(failure_class=FailureClass.UNCLASSIFIED))
    assert verdict.allowed


def test_a_revoked_mandate_is_never_offered_a_pay_now_link():
    verdict = pay_now_link_causes(pay_now(), context(failure_class=FailureClass.MANDATE_REVOKED))
    assert not verdict.allowed
    assert verdict.rule == "pay_now_link_causes"


def test_a_risk_decline_is_never_offered_another_route_to_pay():
    verdict = pay_now_link_causes(pay_now(), context(failure_class=FailureClass.RISK_DECLINE))
    assert not verdict.allowed


def test_a_dead_card_gets_a_card_change_link_not_a_pay_now_link():
    verdict = pay_now_link_causes(pay_now(), context(failure_class=FailureClass.INSTRUMENT_INVALID))
    assert not verdict.allowed


def test_an_issuer_outage_is_not_worth_bothering_the_customer_about():
    verdict = pay_now_link_causes(pay_now(), context(failure_class=FailureClass.TRANSIENT_ISSUER))
    assert not verdict.allowed


def test_the_rule_ignores_every_other_action_type():
    other_actions = {
        ActionType.RETRY_CHARGE: charge(at_ist(10)),
        ActionType.SEND_MESSAGE: message(at_ist(10)),
        ActionType.REQUEST_INSTRUMENT_UPDATE: Action(
            action_id="act_4",
            subscription_id="sub_1",
            type=ActionType.REQUEST_INSTRUMENT_UPDATE,
            scheduled_at=at_ist(10),
            tier=Tier.T2_REQUEST_ACTION,
            channel="email",
            template_id="t2_update_instrument_email",
            free_text=None,
            reason="test",
        ),
        ActionType.STOP: Action(
            action_id="act_5",
            subscription_id="sub_1",
            type=ActionType.STOP,
            scheduled_at=at_ist(10),
            tier=Tier.T4_TERMINAL,
            channel=None,
            template_id=None,
            free_text=None,
            reason="test",
        ),
    }
    for action_type, act in other_actions.items():
        verdict = pay_now_link_causes(act, context(failure_class=FailureClass.MANDATE_REVOKED))
        assert verdict.allowed, action_type


def test_the_rule_is_wired_into_the_engine():
    from recoup.policy.rules import RULES

    assert pay_now_link_causes in RULES
