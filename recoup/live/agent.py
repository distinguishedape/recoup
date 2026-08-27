"""The agent, running against real events instead of a cohort.

Until this module existed the live path stopped at ingest. A signed Razorpay
webhook arrived, its signature was verified, the payload was mapped and one
audit record was written -- and nothing classified it, planned for it, gated it
or acted on it. Every claim in the README rested on the simulation harness while
the deployed artefact was a receiver that appended to a log. This joins the two
halves.

Three things are deliberately different from the simulation, and all three are
about refusing to fabricate:

* **The rail is whatever you supply.** ``RazorpayTestRail.charge()`` raises,
  because Razorpay exposes no manual-retry API (spike finding F2). The agent
  catches that and records ``execute_unsupported`` naming the reason, rather
  than inventing an outcome. A deployment with a real charge transport passes
  one in and the same code path executes for real.
* **There is no default dispatcher.** A message the agent cannot actually send
  is recorded as undelivered, not as sent. ``UndeliveredDispatcher`` is the
  honest default for a demo without an email provider, and it returns False.
* **Time is real.** Actions the plan schedules for the future are held, not
  executed early. ``due(now)`` runs the ones that have come round, and a
  deployment drives it from a scheduler or from the scanner's poll loop.

State lives in memory. That is a real limitation and it is stated rather than
hidden: a restart forgets which subjects are mid-ladder. The audit log holds
everything needed to rebuild it, so the fix is a reconstruction function and
not a schema change.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from recoup.audit.log import AuditLog, new_record
from recoup.classify.engine import classify
from recoup.escalate.ladder import (
    LADDER_GOVERNED_TYPES,
    LadderState,
    is_exhausted,
    may_enter,
    record_execution,
)
from recoup.execute.executor import Executor, MessageDispatcher, RenderedMessage
from recoup.execute.rail import PaymentRail
from recoup.llm.client import LLMClient
from recoup.models.core import Action, Classification, FailureEvent, Subscription
from recoup.models.enums import ActionType, Tier
from recoup.plan.budgets import CONTACT_ACTION_TYPES
from recoup.plan.llm_planner import plan as build_intervention_plan
from recoup.policy.gate import Block, Execute, Reschedule, block_payload, gate, reschedule_payload
from recoup.policy.rules import PolicyContext

POST_UPDATE_CHARGE_DELAY_HOURS = 1


class RealClock:
    """A clock the executor accepts, reading the actual time."""

    @property
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class UndeliveredDispatcher:
    """Records what the agent wanted to send and reports it as not delivered.

    The honest default when no message transport is configured. Returning True
    here would put a fabricated delivery into the audit log through the path
    labelled real, which is the same mistake ``RazorpayTestRail.charge`` exists
    to prevent one layer down.
    """

    def __init__(self) -> None:
        self.attempted: list[tuple[str, str, RenderedMessage]] = []

    def send(
        self, subscription_id: str, channel: str, message: RenderedMessage, now: datetime
    ) -> bool:
        self.attempted.append((subscription_id, channel, message))
        return False


@dataclass
class _Pending:
    when: datetime
    action: Action


@dataclass
class Decision:
    """What the agent did with one event, for the caller to log or return."""

    subscription_id: str
    classification: Classification
    planned: int
    executed: int = 0
    blocked: int = 0
    rescheduled: int = 0
    unsupported: int = 0
    held: int = 0


@dataclass
class LiveAgent:
    audit: AuditLog
    rail: PaymentRail
    dispatcher: MessageDispatcher = field(default_factory=UndeliveredDispatcher)
    llm_client: LLMClient | None = None
    clock: RealClock = field(default_factory=RealClock)
    default_amount_paise: int = 0
    """Plan amount used for message rendering when the event does not carry one.

    Razorpay's ``payment.failed`` payload has the amount; ``subscription.pending``
    does not, and fetching the plan is the deployer's job rather than a guess
    made here."""

    _states: dict[str, LadderState] = field(default_factory=dict, init=False)
    _pending: list[_Pending] = field(default_factory=list, init=False)
    _last_contact: dict[str, datetime] = field(default_factory=dict, init=False)
    _charged_instruments: dict[str, set[str]] = field(default_factory=dict, init=False)
    _amounts: dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._executor = Executor(self.rail, self.dispatcher, self.audit, self.clock)

    # -- ingestion ---------------------------------------------------------

    def handle(self, event: FailureEvent, amount_paise: int | None = None) -> Decision:
        """Classify, plan and run whatever is already due for one failure."""
        sub_id = event.subscription_id
        now = self.clock.now
        self._amounts[sub_id] = amount_paise or self.default_amount_paise

        classification = classify(event, self.llm_client)
        self.audit.append(
            new_record(sub_id, now, "classify", classification.model_dump(mode="json"))
        )

        intervention = build_intervention_plan(event, classification, self.llm_client, now)
        starting_tier = (
            min(a.tier for a in intervention.actions) if intervention.actions else Tier.T1_NOTIFY
        )
        self._states[sub_id] = LadderState(
            subscription_id=sub_id,
            failure_class=classification.failure_class,
            opted_out=False,
            starting_tier=starting_tier,
        )
        self.audit.append(
            new_record(
                sub_id,
                now,
                "plan",
                {
                    "failure_class": intervention.failure_class.value,
                    "actions": [a.model_dump(mode="json") for a in intervention.actions],
                },
            )
        )
        for action in intervention.actions:
            self._pending.append(_Pending(when=action.scheduled_at, action=action))

        decision = Decision(
            subscription_id=sub_id,
            classification=classification,
            planned=len(intervention.actions),
        )
        self.due(now, decision)
        return decision

    # -- execution ---------------------------------------------------------

    def due(self, now: datetime | None = None, decision: Decision | None = None) -> Decision:
        """Run every held action whose time has come.

        Drive this from a scheduler, or from the scanner's poll loop -- the
        same tick that looks for newly at-risk revenue is the natural place to
        also advance subjects already in the ladder.
        """
        now = now or self.clock.now
        decision = decision or Decision(
            subscription_id="", classification=None, planned=0  # type: ignore[arg-type]
        )
        ready = [p for p in self._pending if p.when <= now]
        self._pending = [p for p in self._pending if p.when > now]
        for item in ready:
            self._run(item.action, now, decision)
        # Counted after running, and scoped to the subject this decision is
        # about -- the queue is shared across every subject the agent holds, so
        # its raw length says nothing about the one being reported on.
        decision.held = sum(
            1 for p in self._pending if p.action.subscription_id == decision.subscription_id
        )
        return decision

    def _run(self, action: Action, now: datetime, decision: Decision) -> None:
        sub_id = action.subscription_id
        state = self._states.get(sub_id)
        if state is None:
            return

        terminal_action = action.type in {ActionType.STOP, ActionType.ESCALATE_MANUAL_REVIEW}
        if state.recovered or (is_exhausted(state) and not terminal_action):
            decision.blocked += 1
            self.audit.append(
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
            return

        if action.type in LADDER_GOVERNED_TYPES and not may_enter(state, action.tier):
            decision.blocked += 1
            self.audit.append(
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
            return

        replacement = None
        if hasattr(self.rail, "replacement_instrument_id"):
            replacement = self.rail.replacement_instrument_id(sub_id)

        context = PolicyContext(
            now=now,
            failure_class=state.failure_class,
            contacts_sent=state.contacts_sent,
            charge_retries_used=state.charge_retries_used,
            opted_out=state.opted_out,
            promise_to_pay_until=None,
            last_contact_at=self._last_contact.get(sub_id),
            replacement_instrument_id=replacement,
            charged_instrument_ids=frozenset(self._charged_instruments.get(sub_id, set())),
        )
        gated = gate(action, context, state.reschedules.get(action.action_id, 0))

        if isinstance(gated, Reschedule):
            state.reschedules[action.action_id] = gated.attempt
            decision.rescheduled += 1
            self.audit.append(
                new_record(
                    sub_id, now, "contact_rescheduled", reschedule_payload(action, gated, now)
                )
            )
            self._pending.append(
                _Pending(
                    when=gated.when,
                    action=action.model_copy(update={"scheduled_at": gated.when}),
                )
            )
            return

        if isinstance(gated, Block):
            decision.blocked += 1
            self.audit.append(
                new_record(sub_id, now, "policy_block", block_payload(action, gated.verdict))
            )
            return

        self._execute(gated, action, sub_id, now, context, state, decision)

    def _execute(self, gated: Execute, action, sub_id, now, context, state, decision) -> None:
        render_context = _render_context(sub_id, self._amounts.get(sub_id, 0))
        try:
            result = self._executor.execute(gated.authorized, render_context)
        except NotImplementedError as exc:
            # The rail said it cannot do this for real. Recorded, not faked.
            decision.unsupported += 1
            self.audit.append(
                new_record(
                    sub_id,
                    now,
                    "execute_unsupported",
                    {
                        "action_id": action.action_id,
                        "action_type": action.type.value,
                        "detail": str(exc),
                    },
                )
            )
            return

        decision.executed += 1
        if context.replacement_instrument_id and action.type is ActionType.RETRY_CHARGE:
            self._charged_instruments.setdefault(sub_id, set()).add(
                context.replacement_instrument_id
            )
        record_execution(state, action, result.succeeded)
        if action.type in CONTACT_ACTION_TYPES:
            self._last_contact[sub_id] = now
        if action.type is ActionType.REQUEST_INSTRUMENT_UPDATE and result.succeeded:
            when = now + timedelta(hours=POST_UPDATE_CHARGE_DELAY_HOURS)
            self._pending.append(
                _Pending(
                    when=when,
                    action=Action(
                        action_id=f"{sub_id}:act:post_update",
                        subscription_id=sub_id,
                        type=ActionType.RETRY_CHARGE,
                        scheduled_at=when,
                        tier=action.tier,
                        channel=None,
                        template_id=None,
                        free_text=None,
                        reason="the customer supplied a new instrument, so charge it",
                    ),
                )
            )


def _render_context(subscription_id: str, amount_paise: int) -> dict[str, str]:
    from recoup.execute.executor import render_context_for

    return render_context_for(
        Subscription(
            subscription_id=subscription_id,
            customer_id=subscription_id,
            plan_amount_paise=amount_paise,
        )
    )
