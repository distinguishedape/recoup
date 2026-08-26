"""The control arm: Razorpay's own retry ladder, modelled honestly.

The baseline is four total charge attempts -- the initial failure that put
the subscription into ``pending``, plus three retries a day apart -- with
no intervention beyond Razorpay's own failure email, ending in ``halted``
(spec section 6). It does not classify, it does not vary its spacing, and
it does not know that the card it is retrying expired in March.

That last point is the entire experiment. The control arm is not a straw
man; it is what a merchant gets today, and its weakness is specifically
that it spends the same four attempts on a customer who is 200 rupees
short as on a customer who has revoked their mandate.

Day-stepping is used rather than the test-mode 10min/1h ladder because the
latter reads as test acceleration rather than production behaviour, and
Razorpay's own documentation is inconsistent between the two (spike
finding F4). The choice is stated in the report.
"""

import random
from collections import defaultdict
from datetime import datetime, timedelta

from recoup.audit.log import AuditLog, new_record
from recoup.clock.virtual import VirtualClock
from recoup.execute.executor import CHARGE_ATTEMPT_COST_PAISE
from recoup.execute.rail import SimulatedRail
from recoup.ingest.cohort import CohortSpec, generate_cohort
from recoup.models.enums import ActionType, FailureClass, TerminalState
from recoup.orchestrate.runner import RunConfig, RunResult, SubjectOutcome, config_hash

CONTROL_RETRY_DELAYS_HOURS: tuple[int, ...] = (24, 48, 72)


def run_control_arm(config: RunConfig, audit: AuditLog) -> RunResult:
    cohort = generate_cohort(
        CohortSpec(size=config.cohort_size, seed=config.seed), config.start_at
    )
    clock = VirtualClock(config.start_at)
    rail = SimulatedRail(
        cohort.subjects,
        config.band,
        random.Random(config.seed + 1),
        paired_seed=config.seed,
    )

    recovered_at: dict[str, datetime] = {}
    spend: dict[str, int] = defaultdict(int)
    attempts: dict[str, int] = defaultdict(int)

    for event in cohort.events:
        sub_id = event.subscription_id
        audit.append(
            new_record(sub_id, event.occurred_at, "ingest", event.model_dump(mode="json"))
        )
        for delay in CONTROL_RETRY_DELAYS_HOURS:
            clock.schedule(event.occurred_at + timedelta(hours=delay), sub_id)

    while (popped := clock.pop()) is not None:
        now, sub_id = popped
        if sub_id in recovered_at:
            continue
        result = rail.charge(sub_id, now)
        attempts[sub_id] += 1
        spend[sub_id] += CHARGE_ATTEMPT_COST_PAISE
        if result.succeeded:
            recovered_at[sub_id] = now
        audit.append(
            new_record(
                sub_id,
                now,
                "control_execute",
                {
                    "action_type": ActionType.RETRY_CHARGE.value,
                    "attempt": attempts[sub_id],
                    "succeeded": result.succeeded,
                    "detail": (
                        "charge succeeded"
                        if result.succeeded
                        else f"charge declined: {result.error_reason}"
                    ),
                    "cost_paise": CHARGE_ATTEMPT_COST_PAISE,
                },
            )
        )

    outcomes: list[SubjectOutcome] = []
    for subscription in cohort.subscriptions:
        sub_id = subscription.subscription_id
        latent = cohort.subjects[sub_id].latent_class
        if sub_id in recovered_at:
            terminal = TerminalState.RECOVERED
        elif latent is FailureClass.MANDATE_REVOKED:
            # Bookkeeping only. The control arm still spent its full ladder on
            # this subject -- it had no way to know -- but a revoked mandate is
            # voluntary churn in both arms and must leave the denominator in
            # both, or the comparison is rigged.
            terminal = TerminalState.VOLUNTARY_CHURN
        else:
            terminal = TerminalState.UNRECOVERED

        gross = subscription.plan_amount_paise if terminal is TerminalState.RECOVERED else 0
        audit.append(
            new_record(
                sub_id,
                clock.now,
                "terminal",
                {
                    "terminal": terminal.value,
                    "failure_class": latent.value,
                    "gross_recovered_paise": gross,
                    "cost_paise": spend[sub_id],
                },
            )
        )
        outcomes.append(
            SubjectOutcome(
                subscription_id=sub_id,
                failure_class=latent,
                terminal=terminal,
                gross_recovered_paise=gross,
                cost_paise=spend[sub_id],
                actions_executed=attempts[sub_id],
                charge_attempts=attempts[sub_id],
                actions_blocked=0,
                first_failure_at=cohort.subjects[sub_id].first_failure_at,
                recovered_at=recovered_at.get(sub_id),
            )
        )

    return RunResult(
        run_id=f"{config.run_id}:control",
        config_hash=config_hash(config),
        outcomes=outcomes,
        gross_recovered_paise=sum(o.gross_recovered_paise for o in outcomes),
        total_cost_paise=sum(o.cost_paise for o in outcomes),
    )
