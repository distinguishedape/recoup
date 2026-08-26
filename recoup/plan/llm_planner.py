"""The LLM planner: propose, validate, clamp -- or fall back.

The model is allowed to choose the shape of the intervention: which
actions, in which order, with which spacing, at which tier. It is not
allowed to choose what is permissible. Anything it returns is parsed into
the same ``Action`` objects the deterministic planner produces, and
rejected outright if it names an action type, template or delay outside
the allowed set -- or if it overspends the per-class budget.

``propose_plan`` returning ``None`` is the normal, expected path when the
model is off, unreachable, or wrong. ``plan`` is what callers use: it
degrades to the deterministic planner without anyone upstream noticing.
"""

import json
import re
from datetime import datetime, timedelta

from recoup.execute.messages import ALLOWED_TEMPLATE_IDS
from recoup.execute.probabilities import expected_recovery
from recoup.llm.client import LLMClient, LLMUnavailable
from recoup.models.core import Action, Classification, FailureEvent, InterventionPlan
from recoup.models.enums import ActionType, Band, Tier
from recoup.plan.budgets import action_id, budget_for, clamp_to_budget
from recoup.plan.fallback import build_plan

MAX_DELAY_HOURS = 168
MAX_ACTIONS = 8
MAX_TOKENS = 3000
"""Same reasoning as the resolver: models that think before answering spend
this allowance on the thinking, and a truncated plan is discarded whole."""

PLANNER_SYSTEM = f"""You plan recovery interventions for failed subscription auto-debits \
in India.

You are given the root cause of one failure. Propose the sequence of actions that best \
recovers the payment without harassing the customer.

Allowed action types: {", ".join(a.value for a in ActionType)}.
Allowed tiers: 1 (notify), 2 (request action), 3 (final notice), 4 (terminal).
Allowed template ids: {", ".join(sorted(ALLOWED_TEMPLATE_IDS))}.

Hard rules -- a plan that breaks any of these is discarded entirely:
- You may not write free text to a customer. Every message action must name one of the \
allowed template ids and must not include a `free_text` field.
- Each class has an attempt budget. A plan that exceeds it is discarded entirely, so \
proposing more than the budget loses the whole plan rather than extending it.
- Never propose a charge retry for a cause a retry cannot fix (an invalid instrument, \
a revoked mandate, a risk block).
- `delay_hours` must be between 0 and {MAX_DELAY_HOURS}.
- At most {MAX_ACTIONS} actions.
- Do not include a stop or an escalation alongside actions scheduled at or after
it. A plan that stops and then retries is discarded entirely.

Timing matters as much as choice of action, and the two causes differ sharply:
- A bank or gateway outage clears in *hours*, not days. Retry within a few hours;
waiting a day wastes the window in which a retry would have worked.
- A funds shortfall tracks the customer's pay cycle. Retry after a day, then
again around three days, which is where salary and transfer credits land.

Reply with JSON only, in exactly this shape:
{{"actions": [{{"type": "...", "delay_hours": 0, "tier": 1, "channel": "email" or null, \
"template_id": "..." or null, "reason": "one sentence"}}]}}
"""


def build_planner_prompt(event: FailureEvent, classification: Classification) -> str:
    budget = budget_for(classification.failure_class)
    return (
        "Plan the recovery for this failure.\n\n"
        f"failure_class: {classification.failure_class.value}\n"
        f"classifier_rationale: {classification.rationale}\n"
        f"reason: {event.error_reason}\n"
        f"source: {event.error_source}\n"
        f"step: {event.error_step}\n"
        f"attempt_number: {event.attempt_number}\n"
        f"budget_charge_retries: {budget.charge_retries}\n"
        f"budget_contacts: {budget.contacts}\n"
    )


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_action(raw: dict, subscription_id: str, index: int, now: datetime) -> Action | None:
    if not isinstance(raw, dict):
        return None
    try:
        action_type = ActionType(str(raw.get("type", "")))
        tier = Tier(int(raw.get("tier", 1)))
        delay = float(raw.get("delay_hours", 0))
    except (ValueError, TypeError):
        return None
    if not 0 <= delay <= MAX_DELAY_HOURS:
        return None

    template_id = raw.get("template_id") or None
    channel = raw.get("channel") or None
    if template_id is not None and template_id not in ALLOWED_TEMPLATE_IDS:
        return None
    if action_type in {ActionType.SEND_MESSAGE, ActionType.REQUEST_INSTRUMENT_UPDATE}:
        if template_id is None or channel is None:
            return None

    # Free text is substituted rather than rejected, provided an approved
    # template covers the same moment. Throwing the plan away loses a good
    # sequence over one bad sentence; keeping the sentence in the record and
    # sending the template loses nothing and shows a reviewer exactly what the
    # model wanted to say.
    proposed_text = raw.get("free_text")
    suppressed = str(proposed_text).strip() if proposed_text else None
    if suppressed and template_id is None:
        # Nothing approved to send in its place, so there is no substitution to
        # make and the action cannot go out.
        return None

    reason = str(raw.get("reason", "")).strip() or "proposed by the planning model"
    return Action(
        action_id=action_id(subscription_id, index),
        subscription_id=subscription_id,
        type=action_type,
        scheduled_at=now + timedelta(hours=delay),
        tier=tier,
        channel=channel,
        template_id=template_id,
        free_text=None,
        suppressed_free_text=suppressed,
        reason=reason,
    )


TERMINAL_TYPES = frozenset({ActionType.STOP, ActionType.ESCALATE_MANUAL_REVIEW})


def _stops_before_it_acts(actions: list[Action]) -> bool:
    """True if a terminal action is scheduled at or before a non-terminal one."""
    terminal_times = [a.scheduled_at for a in actions if a.type in TERMINAL_TYPES]
    if not terminal_times:
        return False
    earliest_stop = min(terminal_times)
    return any(
        a.scheduled_at >= earliest_stop for a in actions if a.type not in TERMINAL_TYPES
    )


def _buries_the_remedy(actions: list[Action]) -> bool:
    """True if a contact is scheduled before the instrument-update request.

    When a card cannot be charged, asking for a different one is not one option
    among several -- it is the only thing that recovers the payment. A plan that
    sends a notice first spends a contact on saying nothing actionable *and*
    puts the remedy behind it on the ladder, where a tier that never executed
    keeps the next one shut.

    That is not hypothetical. A model plan did exactly this: notice at tier one,
    update request at tier two. Every action was permitted and within budget.
    Whenever the notice fell outside the contact window the request never ran,
    and the cause recovered nobody at all.
    """
    updates = [a for a in actions if a.type is ActionType.REQUEST_INSTRUMENT_UPDATE]
    if not updates:
        return False
    earliest_update = min(a.scheduled_at for a in updates)
    return any(
        a.scheduled_at < earliest_update
        for a in actions
        if a.type is ActionType.SEND_MESSAGE
    )


def propose_plan(
    event: FailureEvent,
    classification: Classification,
    client: LLMClient,
    now: datetime,
) -> InterventionPlan | None:
    try:
        text = client.complete(
            PLANNER_SYSTEM, build_planner_prompt(event, classification), MAX_TOKENS
        )
    except LLMUnavailable:
        return None

    parsed = _extract_json(text)
    if parsed is None:
        return None
    raw_actions = parsed.get("actions")
    if not isinstance(raw_actions, list) or len(raw_actions) > MAX_ACTIONS:
        return None

    actions: list[Action] = []
    for index, raw in enumerate(raw_actions):
        action = _parse_action(raw, event.subscription_id, index, now)
        if action is None:
            return None
        actions.append(action)

    if _buries_the_remedy(actions):
        return None

    if _stops_before_it_acts(actions):
        # "Stop now, then retry tomorrow" is not a plan, it is two plans
        # disagreeing. A terminal action means the subject is finished, so
        # anything scheduled at or after it cannot also be intended. The
        # policy engine would have executed each action safely and the
        # sequence would still have been nonsense, which is the difference
        # between a gate that enforces permission and one that enforces sense.
        return None

    proposed = InterventionPlan(
        subscription_id=event.subscription_id,
        failure_class=classification.failure_class,
        actions=actions,
    )
    clamped = clamp_to_budget(proposed)
    if len(clamped.actions) != len(proposed.actions):
        # The spec's rule is reject-and-regenerate, not silently trim: a plan
        # that overspends its budget is evidence the model misread the
        # situation, and the rest of that plan does not deserve more trust
        # than the part that was dropped. Fall back to the deterministic
        # planner instead.
        return None
    return clamped


def _retry_delays_hours(plan_obj: InterventionPlan, now: datetime) -> list[float]:
    return [
        (action.scheduled_at - now).total_seconds() / 3600
        for action in plan_obj.actions
        if action.type is ActionType.RETRY_CHARGE
    ]


def plan(
    event: FailureEvent,
    classification: Classification,
    client: LLMClient | None,
    now: datetime,
    band: Band = Band.MID,
) -> InterventionPlan:
    """Use the model's plan only when it is at least as good as ours.

    The deterministic planner is the floor, not merely the fallback. Validation
    already established that a proposal is *permitted*; this asks whether it is
    *better*, by scoring both schedules against the timing model and keeping
    the stronger one.

    That distinction was expensive to learn. A model plan can pass every safety
    check and still lose money: asked to recover an issuer outage, this one
    proposed retries a day, three days and five days out -- a reasonable
    dunning cadence, and wrong, because an outage settles within hours and
    those late attempts arrive after the decay has eaten them. Every action was
    allowed. The sequence was worse than the one it replaced.

    Scoring makes the model upside-only. When it finds something better than
    the hand-written schedule it is used, and when it does not the hand-written
    schedule stands.
    """
    fallback = build_plan(event, classification, now)
    if client is None:
        return fallback

    proposed = propose_plan(event, classification, client, now)
    if proposed is None:
        return fallback

    failure_class = classification.failure_class
    def asks_for_instrument(plan_obj: InterventionPlan) -> bool:
        return any(
            a.type is ActionType.REQUEST_INSTRUMENT_UPDATE for a in plan_obj.actions
        )

    proposed_score = expected_recovery(
        failure_class, band, _retry_delays_hours(proposed, now), asks_for_instrument(proposed)
    )
    fallback_score = expected_recovery(
        failure_class, band, _retry_delays_hours(fallback, now), asks_for_instrument(fallback)
    )
    return proposed if proposed_score >= fallback_score else fallback
