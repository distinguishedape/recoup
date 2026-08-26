"""The escalation ladder and the stopping rules (spec R8).

Four tiers of increasing intensity. The rule that matters is that a tier
opens only when the *previous* tier was actually executed and did not
recover the payment -- not when it was merely planned. An action the
policy engine blocked cannot be used to justify escalating to something
louder, which is precisely the failure mode aggressive dunning systems
have.

The contact budget bounds the ladder, so R3 and R8 can never disagree:
whichever runs out first ends the sequence.

Channels are declared per tier rather than hard-coded into the executor,
so adding voice later is a new row here, not a redesign.
"""

from pydantic import BaseModel, Field

from recoup.models.core import Action
from recoup.models.enums import ActionType, FailureClass, TerminalState, Tier
from recoup.plan.budgets import CONTACT_ACTION_TYPES, budget_for

TIER_CHANNELS: dict[Tier, tuple[str, ...]] = {
    Tier.T1_NOTIFY: ("email",),
    Tier.T2_REQUEST_ACTION: ("email", "sms"),
    Tier.T3_FINAL_NOTICE: ("email", "sms"),
    Tier.T4_TERMINAL: (),
}

TERMINAL_ONLY_CLASSES = frozenset({FailureClass.MANDATE_REVOKED})


class LadderState(BaseModel):
    subscription_id: str
    failure_class: FailureClass
    current_tier: Tier = Tier.T1_NOTIFY
    executed_tiers: set[int] = Field(default_factory=set)
    contacts_sent: int = 0
    charge_retries_used: int = 0
    post_update_charges_used: int = 0
    recovered: bool = False
    opted_out: bool = False
    escalated_manual_review: bool = False
    terminal: TerminalState | None = None


def may_enter(state: LadderState, tier: Tier) -> bool:
    if tier is Tier.T4_TERMINAL:
        return True
    if state.recovered:
        return False
    if state.failure_class in TERMINAL_ONLY_CLASSES or state.opted_out:
        return False
    if tier is Tier.T1_NOTIFY:
        return True
    return int(tier) - 1 in state.executed_tiers


def record_execution(state: LadderState, action: Action, succeeded: bool) -> None:
    state.executed_tiers.add(int(action.tier))
    state.current_tier = max(state.current_tier, action.tier)
    if action.type is ActionType.RETRY_CHARGE:
        state.charge_retries_used += 1
        if succeeded:
            state.recovered = True
    elif action.type in CONTACT_ACTION_TYPES:
        state.contacts_sent += 1
    elif action.type is ActionType.ESCALATE_MANUAL_REVIEW:
        state.escalated_manual_review = True


def is_exhausted(state: LadderState) -> bool:
    if state.recovered:
        return True
    if int(Tier.T3_FINAL_NOTICE) in state.executed_tiers:
        return True
    budget = budget_for(state.failure_class)
    return (
        state.contacts_sent >= budget.contacts
        and state.charge_retries_used >= budget.charge_retries
    )


def assign_terminal(state: LadderState) -> TerminalState:
    if state.recovered:
        return TerminalState.RECOVERED
    if state.failure_class is FailureClass.MANDATE_REVOKED:
        return TerminalState.VOLUNTARY_CHURN
    if state.escalated_manual_review:
        return TerminalState.MANUAL_REVIEW
    return TerminalState.UNRECOVERED
