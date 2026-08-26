"""The Recoup arm: cohort in, terminal states and money out.

The loop is deliberately boring. Every event is ingested, classified and
planned; every planned action is put on the virtual clock; and every
action that comes off the clock must clear the ladder and then the policy
engine before the executor will look at it. A block is not an error --
it is the product working, and it is audited as carefully as an execution.

Recovery ends a subject immediately: once a charge succeeds, the remaining
actions for that subject are dropped rather than executed, because
continuing to dun a customer who has already paid is the single most
damaging thing a recovery system can do.
"""

import hashlib
import random
from collections import defaultdict
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from recoup.audit.log import AuditLog, new_record
from recoup.classify.engine import classify
from recoup.clock.virtual import VirtualClock
from recoup.escalate.ladder import (
    LadderState,
    assign_terminal,
    is_exhausted,
    may_enter,
    record_execution,
)
from recoup.execute.executor import Executor, SimulatedDispatcher, render_context_for
from recoup.execute.rail import SimulatedRail
from recoup.ingest.cohort import CohortSpec, generate_cohort
from recoup.llm.client import LLMClient
from recoup.models.core import Action, Subscription
from recoup.models.enums import ActionType, Band, FailureClass, TerminalState
from recoup.plan.budgets import CONTACT_ACTION_TYPES
from recoup.plan.llm_planner import plan as build_intervention_plan
from recoup.policy.engine import authorize
from recoup.policy.rules import PolicyContext

POST_UPDATE_CHARGE_DELAY_HOURS = 1
"""Once a customer updates their instrument, charge shortly afterwards --
while the intent is fresh and before the new card can go stale."""


class RunConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    seed: int
    band: Band
    cohort_size: int = Field(ge=1)
    start_at: datetime
    opted_out_ids: frozenset[str] = frozenset()
    promise_to_pay: dict[str, datetime] = Field(default_factory=dict)


class SubjectOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    subscription_id: str
    failure_class: FailureClass
    terminal: TerminalState
    gross_recovered_paise: int
    cost_paise: int
    actions_executed: int
    actions_blocked: int


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    config_hash: str
    outcomes: list[SubjectOutcome]
    gross_recovered_paise: int
    total_cost_paise: int

    @property
    def net_recovered_paise(self) -> int:
        return self.gross_recovered_paise - self.total_cost_paise


def config_hash(config: RunConfig) -> str:
    material = "|".join(
        [
            str(config.seed),
            config.band.value,
            str(config.cohort_size),
            config.start_at.isoformat(),
            ",".join(sorted(config.opted_out_ids)),
            ",".join(f"{k}={v.isoformat()}" for k, v in sorted(config.promise_to_pay.items())),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def run_recoup_arm(
    config: RunConfig,
    audit: AuditLog,
    llm_client: LLMClient | None = None,
) -> RunResult:
    cohort = generate_cohort(
        CohortSpec(size=config.cohort_size, seed=config.seed), config.start_at
    )
    clock = VirtualClock(config.start_at)
    rail = SimulatedRail(cohort.subjects, config.band, random.Random(config.seed + 1))
    executor = Executor(rail, SimulatedDispatcher(), audit, clock)

    subscriptions: dict[str, Subscription] = {s.subscription_id: s for s in cohort.subscriptions}
    states: dict[str, LadderState] = {}
    last_contact: dict[str, datetime] = {}
    executed_count: dict[str, int] = defaultdict(int)
    blocked_count: dict[str, int] = defaultdict(int)
    spend: dict[str, int] = defaultdict(int)

    for event in cohort.events:
        sub_id = event.subscription_id
        audit.append(
            new_record(sub_id, event.occurred_at, "ingest", event.model_dump(mode="json"))
        )
        classification = classify(event, llm_client)
        audit.append(
            new_record(
                sub_id, event.occurred_at, "classify", classification.model_dump(mode="json")
            )
        )
        states[sub_id] = LadderState(
            subscription_id=sub_id,
            failure_class=classification.failure_class,
            opted_out=sub_id in config.opted_out_ids,
        )
        intervention = build_intervention_plan(
            event, classification, llm_client, event.occurred_at
        )
        audit.append(
            new_record(
                sub_id,
                event.occurred_at,
                "plan",
                {
                    "failure_class": intervention.failure_class.value,
                    "actions": [a.model_dump(mode="json") for a in intervention.actions],
                },
            )
        )
        for action in intervention.actions:
            clock.schedule(action.scheduled_at, action)

    while (popped := clock.pop()) is not None:
        now, action = popped
        sub_id = action.subscription_id
        state = states[sub_id]

        # STOP and ESCALATE_MANUAL_REVIEW are exempt from the exhaustion check.
        # A zero-budget class is exhausted from the very first tick, and if that
        # blocked its terminal action the subject would never be handed to a
        # human -- it would simply be recorded as unrecovered, which is the
        # opposite of what a risk decline needs.
        terminal_action = action.type in {ActionType.STOP, ActionType.ESCALATE_MANUAL_REVIEW}
        if state.recovered or (is_exhausted(state) and not terminal_action):
            blocked_count[sub_id] += 1
            audit.append(
                new_record(
                    sub_id,
                    now,
                    "ladder_block",
                    {
                        "action_id": action.action_id,
                        "rule": "recovered" if state.recovered else "ladder_exhausted",
                        "detail": "no further action is warranted for this subject",
                    },
                )
            )
            continue

        if not may_enter(state, action.tier):
            blocked_count[sub_id] += 1
            audit.append(
                new_record(
                    sub_id,
                    now,
                    "ladder_block",
                    {
                        "action_id": action.action_id,
                        "rule": "tier_not_open",
                        "detail": (
                            f"tier {int(action.tier)} cannot be entered from "
                            f"tier {int(state.current_tier)}"
                        ),
                    },
                )
            )
            continue

        context = PolicyContext(
            now=now,
            failure_class=state.failure_class,
            contacts_sent=state.contacts_sent,
            charge_retries_used=state.charge_retries_used,
            opted_out=state.opted_out,
            promise_to_pay_until=config.promise_to_pay.get(sub_id),
            last_contact_at=last_contact.get(sub_id),
            instrument_updated=cohort.subjects[sub_id].instrument_updated,
            post_update_charges_used=state.post_update_charges_used,
        )
        authorized, verdict = authorize(action, context)
        if authorized is None:
            blocked_count[sub_id] += 1
            audit.append(
                new_record(
                    sub_id,
                    now,
                    "policy_block",
                    {
                        "action_id": action.action_id,
                        "action_type": action.type.value,
                        "rule": verdict.rule,
                        "detail": verdict.detail,
                    },
                )
            )
            continue

        result = executor.execute(authorized, render_context_for(subscriptions[sub_id]))
        executed_count[sub_id] += 1
        spend[sub_id] += result.cost_paise
        if context.instrument_updated and action.type is ActionType.RETRY_CHARGE:
            state.post_update_charges_used += 1
        record_execution(state, action, result.succeeded)
        if action.type in CONTACT_ACTION_TYPES:
            last_contact[sub_id] = now
        if action.type is ActionType.REQUEST_INSTRUMENT_UPDATE and result.succeeded:
            clock.schedule(
                now + timedelta(hours=POST_UPDATE_CHARGE_DELAY_HOURS),
                Action(
                    action_id=f"{sub_id}:act:post_update",
                    subscription_id=sub_id,
                    type=ActionType.RETRY_CHARGE,
                    scheduled_at=now + timedelta(hours=POST_UPDATE_CHARGE_DELAY_HOURS),
                    tier=action.tier,
                    channel=None,
                    template_id=None,
                    free_text=None,
                    reason="the customer supplied a new instrument, so charge it",
                ),
            )

    outcomes: list[SubjectOutcome] = []
    for subscription in cohort.subscriptions:
        sub_id = subscription.subscription_id
        state = states[sub_id]
        terminal = assign_terminal(state)
        state.terminal = terminal
        gross = subscription.plan_amount_paise if terminal is TerminalState.RECOVERED else 0
        audit.append(
            new_record(
                sub_id,
                clock.now,
                "terminal",
                {
                    "terminal": terminal.value,
                    "failure_class": state.failure_class.value,
                    "gross_recovered_paise": gross,
                    "cost_paise": spend[sub_id],
                },
            )
        )
        outcomes.append(
            SubjectOutcome(
                subscription_id=sub_id,
                failure_class=state.failure_class,
                terminal=terminal,
                gross_recovered_paise=gross,
                cost_paise=spend[sub_id],
                actions_executed=executed_count[sub_id],
                actions_blocked=blocked_count[sub_id],
            )
        )

    return RunResult(
        run_id=config.run_id,
        config_hash=config_hash(config),
        outcomes=outcomes,
        gross_recovered_paise=sum(o.gross_recovered_paise for o in outcomes),
        total_cost_paise=sum(o.cost_paise for o in outcomes),
    )
