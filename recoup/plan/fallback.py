"""The deterministic planner.

Two jobs. It is the plan Recoup uses when the model is unavailable or
returns something unusable, and it is the reference the model's proposals
are measured against. Every branch here is a direct reading of the spec's
per-class strategy, so a judge can check the intervention against the root
cause by eye.

Note what is absent: there is no branch that retries a dead card, and no
branch that contacts a customer who has revoked their mandate.
"""

from datetime import datetime, timedelta

from recoup.models.core import Action, Classification, FailureEvent, InterventionPlan
from recoup.models.enums import ActionType, FailureClass, Tier
from recoup.plan.budgets import action_id, clamp_to_budget

FUNDS_RETRY_DELAYS_HOURS = (24, 72)
"""Wait a day, then three days: payday and salary-credit cycles are the
reason an insufficient-funds decline recovers at all."""

TRANSIENT_RETRY_DELAYS_HOURS = (6, 24)
"""Issuer outages clear in hours, so there is no reason to wait a day."""

UNCLASSIFIED_RETRY_DELAYS_HOURS = (24, 48, 72)
"""With no root cause to act on, fall back to the baseline ladder."""

FINAL_NOTICE_DELAY_HOURS = 72


def _action(
    subscription_id: str,
    index: int,
    action_type: ActionType,
    scheduled_at: datetime,
    tier: Tier,
    reason: str,
    channel: str | None = None,
    template_id: str | None = None,
) -> Action:
    return Action(
        action_id=action_id(subscription_id, index),
        subscription_id=subscription_id,
        type=action_type,
        scheduled_at=scheduled_at,
        tier=tier,
        channel=channel,
        template_id=template_id,
        free_text=None,
        reason=reason,
    )


def build_plan(
    event: FailureEvent, classification: Classification, now: datetime
) -> InterventionPlan:
    sub = event.subscription_id
    failure_class = classification.failure_class
    actions: list[Action] = []

    def add(action_type: ActionType, delay_hours: float, tier: Tier, reason: str, **kw) -> None:
        actions.append(
            _action(
                sub,
                len(actions),
                action_type,
                now + timedelta(hours=delay_hours),
                tier,
                reason,
                **kw,
            )
        )

    if failure_class is FailureClass.INSUFFICIENT_FUNDS:
        add(
            ActionType.SEND_MESSAGE,
            0,
            Tier.T1_NOTIFY,
            "tell the customer the debit failed for funds so they can top up before the retry",
            channel="email",
            template_id="t1_notify_email",
        )
        for delay in FUNDS_RETRY_DELAYS_HOURS:
            add(
                ActionType.RETRY_CHARGE,
                delay,
                Tier.T1_NOTIFY,
                f"retry {delay}h later, when a salary or transfer may have landed",
            )

    elif failure_class is FailureClass.INSTRUMENT_INVALID:
        add(
            ActionType.REQUEST_INSTRUMENT_UPDATE,
            0,
            Tier.T2_REQUEST_ACTION,
            "the card cannot succeed as it stands, so ask for a new one instead of retrying",
            channel="email",
            template_id="t2_update_instrument_email",
        )
        add(
            ActionType.SEND_MESSAGE,
            FINAL_NOTICE_DELAY_HOURS,
            Tier.T3_FINAL_NOTICE,
            "final notice that the subscription will lapse without an updated instrument",
            channel="email",
            template_id="t3_final_notice_email",
        )

    elif failure_class is FailureClass.MANDATE_REVOKED:
        add(
            ActionType.STOP,
            0,
            Tier.T4_TERMINAL,
            "authorisation has been withdrawn; contacting or charging further is not permitted",
        )

    elif failure_class is FailureClass.TRANSIENT_ISSUER:
        for delay in TRANSIENT_RETRY_DELAYS_HOURS:
            add(
                ActionType.RETRY_CHARGE,
                delay,
                Tier.T1_NOTIFY,
                f"issuer-side technical failure; retry {delay}h later without bothering the customer",
            )

    elif failure_class is FailureClass.RISK_DECLINE:
        add(
            ActionType.ESCALATE_MANUAL_REVIEW,
            0,
            Tier.T4_TERMINAL,
            "a risk block is not something an automated retry should try to argue with",
        )

    else:
        add(
            ActionType.SEND_MESSAGE,
            0,
            Tier.T1_NOTIFY,
            "root cause unknown, so notify once and fall back to the baseline retry ladder",
            channel="email",
            template_id="t1_notify_email",
        )
        for delay in UNCLASSIFIED_RETRY_DELAYS_HOURS:
            add(
                ActionType.RETRY_CHARGE,
                delay,
                Tier.T1_NOTIFY,
                f"baseline ladder retry at {delay}h with no root cause to act on",
            )

    return clamp_to_budget(
        InterventionPlan(subscription_id=sub, failure_class=failure_class, actions=actions)
    )
