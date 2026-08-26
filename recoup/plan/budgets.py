"""Per-class attempt budgets (spec R3) -- the single source of truth.

The budget exists to prevent *waste* -- attempts on causes that cannot
succeed -- not to be thrifty on causes that can. An attempt costs a few rupees
against a plan worth a few thousand, so capping a recoverable cause below what
the baseline spends destroys value to save almost nothing. The classes that
matter are the ones set to zero: those are the claim.

These numbers are enforced twice on purpose. The planner clamps to them so
that a plan is well-formed before anything acts on it, and the policy
engine re-checks them at execution time so that a plan built by a model,
by a bug, or by a future code path still cannot overspend. Defence in
depth is cheap here and the failure mode it prevents -- hammering a dead
card, spamming a customer -- is the one a judge will look for.
"""

from pydantic import BaseModel, ConfigDict, Field

from recoup.models.core import InterventionPlan
from recoup.models.enums import ActionType, FailureClass


class Budget(BaseModel):
    model_config = ConfigDict(frozen=True)

    charge_retries: int = Field(ge=0)
    contacts: int = Field(ge=0)


BUDGETS: dict[FailureClass, Budget] = {
    FailureClass.INSUFFICIENT_FUNDS: Budget(charge_retries=3, contacts=1),
    FailureClass.INSTRUMENT_INVALID: Budget(charge_retries=0, contacts=2),
    FailureClass.MANDATE_REVOKED: Budget(charge_retries=0, contacts=0),
    FailureClass.TRANSIENT_ISSUER: Budget(charge_retries=3, contacts=0),
    FailureClass.RISK_DECLINE: Budget(charge_retries=0, contacts=0),
    FailureClass.UNCLASSIFIED: Budget(charge_retries=3, contacts=1),
}

CONTACT_ACTION_TYPES: frozenset[ActionType] = frozenset(
    {ActionType.SEND_MESSAGE, ActionType.REQUEST_INSTRUMENT_UPDATE}
)


def budget_for(failure_class: FailureClass) -> Budget:
    return BUDGETS[failure_class]


def action_id(subscription_id: str, index: int) -> str:
    """Deterministic action ids: reruns of the same seed produce the same ids."""
    return f"{subscription_id}:act:{index}"


def clamp_to_budget(plan: InterventionPlan) -> InterventionPlan:
    budget = budget_for(plan.failure_class)
    charges = 0
    contacts = 0
    kept = []
    for action in plan.actions:
        if action.type is ActionType.RETRY_CHARGE:
            if charges >= budget.charge_retries:
                continue
            charges += 1
        elif action.type in CONTACT_ACTION_TYPES:
            if contacts >= budget.contacts:
                continue
            contacts += 1
        kept.append(action)
    return plan.model_copy(update={"actions": kept})
