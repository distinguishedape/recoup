"""The gate decision, shared by every caller that runs the pipeline.

There are two things that run actions: the simulation runner and the live
agent. The policy gate is the part that must not differ between them, because
a divergence here is not a wrong number -- it is a compliance rule that applies
in the measurement and not in production, or the reverse.

This session already paid for that lesson once. The experiment runner and the
console disagreed about the same configuration for a week because one loaded
credentials and the other did not, and an entire evidence bundle was published
from the degraded path. That was a wiring difference nobody could see. This
module exists so the *decision* cannot become one: both callers get their
verdict from here, and a change to the reschedule rule reaches production and
the experiment in the same commit or neither.

The three outcomes are exhaustive and deliberately not booleans:

* ``Execute`` carries the ``AuthorizedAction``, which is the only object the
  executor accepts and which only the policy engine can construct.
* ``Reschedule`` is what a *timing* rule earns. Discarding a contact blocked
  at 3am turns "not now" into "not ever" -- a different and far more expensive
  policy, and one that looks like restraint while losing money.
* ``Block`` ends the action. Every other rule means it should not happen.
"""

from dataclasses import dataclass
from datetime import datetime

from recoup.models.core import Action, PolicyVerdict
from recoup.policy.authorized import AuthorizedAction
from recoup.policy.engine import authorize
from recoup.policy.rules import MAX_RESCHEDULES, PolicyContext, next_permitted_contact_time

RESCHEDULABLE_RULE = "contact_window"
"""The one rule answered by a different time rather than by giving up.

Deliberately a single named rule and not a category. A budget denial, an
opt-out or a revoked mandate all mean the action should not happen at all, and
retrying them later would be a bypass wearing a scheduler's clothes."""


@dataclass(frozen=True)
class Execute:
    authorized: AuthorizedAction
    verdict: PolicyVerdict


@dataclass(frozen=True)
class Reschedule:
    when: datetime
    attempt: int
    verdict: PolicyVerdict


@dataclass(frozen=True)
class Block:
    verdict: PolicyVerdict


GateDecision = Execute | Reschedule | Block


def gate(
    action: Action,
    context: PolicyContext,
    reschedules_used: int = 0,
    max_reschedules: int = MAX_RESCHEDULES,
) -> GateDecision:
    """Run the rules and say what should happen to this action.

    ``reschedules_used`` is how many times this specific action has already
    been moved. It is a parameter rather than state owned here because the two
    callers store it differently -- the runner in its ladder state, the live
    agent reconstructed from the audit log -- and the gate has no business
    caring which.
    """
    authorized, verdict = authorize(action, context)
    if authorized is not None:
        return Execute(authorized=authorized, verdict=verdict)

    if verdict.rule == RESCHEDULABLE_RULE and reschedules_used < max_reschedules:
        return Reschedule(
            when=next_permitted_contact_time(context.now),
            attempt=reschedules_used + 1,
            verdict=verdict,
        )

    return Block(verdict=verdict)


def reschedule_payload(action: Action, decision: Reschedule, now: datetime) -> dict:
    """The audit payload for a reschedule, so both callers write the same record.

    An audit trail whose shape depends on which code path produced it is an
    audit trail nobody can query.
    """
    return {
        "action_id": action.action_id,
        "action_type": action.type.value,
        "rule": decision.verdict.rule,
        "detail": decision.verdict.detail,
        "original_time": now.isoformat(),
        "rescheduled_to": decision.when.isoformat(),
        "attempt": decision.attempt,
    }


def block_payload(action: Action, verdict: PolicyVerdict) -> dict:
    return {
        "action_id": action.action_id,
        "action_type": action.type.value,
        "rule": verdict.rule,
        "detail": verdict.detail,
    }
