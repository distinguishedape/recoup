from datetime import datetime, timedelta, timezone

from recoup.models.core import Action
from recoup.models.enums import ActionType, FailureClass, Tier
from recoup.policy.engine import authorize
from recoup.policy.rules import IST, PolicyContext


def at_ist(hour: int) -> datetime:
    return datetime(2026, 8, 25, hour, 0, tzinfo=IST).astimezone(timezone.utc)


def message(template_id: str = "t1_notify_email") -> Action:
    return Action(
        action_id="act_1",
        subscription_id="sub_1",
        type=ActionType.SEND_MESSAGE,
        scheduled_at=at_ist(10),
        tier=Tier.T1_NOTIFY,
        channel="email",
        template_id=template_id,
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
    )
    base.update(overrides)
    return PolicyContext(**base)


def test_a_compliant_action_is_authorized():
    authorized, verdict = authorize(message(), context())
    assert verdict.allowed is True
    assert verdict.rule == "all_rules_passed"
    assert authorized is not None
    assert authorized.action.action_id == "act_1"


def test_a_denied_action_yields_no_authorization():
    authorized, verdict = authorize(message(), context(now=at_ist(3)))
    assert authorized is None
    assert verdict.allowed is False
    assert verdict.rule == "contact_window"


def test_the_first_failing_rule_is_the_one_reported():
    authorized, verdict = authorize(
        message("t9_not_allowed"), context(now=at_ist(3), opted_out=True)
    )
    assert authorized is None
    assert verdict.rule == "opt_out_stop"


def test_the_verdict_explains_itself_in_words_a_human_can_read():
    _, verdict = authorize(message(), context(now=at_ist(23)))
    assert "IST" in verdict.detail


def test_evaluation_short_circuits_at_the_first_denial():
    _, verdict = authorize(
        message(),
        context(now=at_ist(3), last_contact_at=at_ist(3) - timedelta(minutes=5)),
    )
    assert verdict.rule in {
        "opt_out_stop",
        "promise_to_pay_suppression",
        "class_retry_budget",
        "template_allowlist",
        "contact_window",
    }
    assert verdict.allowed is False
