"""The synthetic cohort generator -- the second of the two ingestion paths.

It emits exactly the ``FailureEvent`` shape the real Razorpay webhook
receiver emits. Nothing downstream branches on ``source``, so the same
pipeline that processes a live webhook processes the cohort. That is what
makes the real ingestion slice load-bearing rather than decorative.

The class distribution and plan-amount distribution are declared here and
reproduced in the generated report, because a cohort tuned to flatter the
agent would invalidate every number that follows.
"""

import random
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from recoup.execute.rail import SimSubject, canonical_decline
from recoup.models.core import FailureEvent, Subscription
from recoup.models.enums import FailureClass

CLASS_WEIGHTS: dict[FailureClass, float] = {
    FailureClass.INSUFFICIENT_FUNDS: 0.40,
    FailureClass.INSTRUMENT_INVALID: 0.20,
    FailureClass.TRANSIENT_ISSUER: 0.15,
    FailureClass.UNCLASSIFIED: 0.15,
    FailureClass.MANDATE_REVOKED: 0.05,
    FailureClass.RISK_DECLINE: 0.05,
}
"""Roughly the shape reported in published dunning benchmarks: funds
problems dominate, dead instruments are the next largest block, and
revocations and risk blocks are small but decisive tails."""

PLAN_AMOUNTS_PAISE: tuple[int, ...] = (49900, 99900, 199900, 499900)

FAILURE_SPREAD_HOURS = 48
"""First failures are spread across two days so the run is not a single
thundering herd at t=0."""


class CohortSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    size: int = Field(ge=1)
    seed: int
    class_weights: dict[FailureClass, float] = Field(default_factory=lambda: dict(CLASS_WEIGHTS))
    plan_amounts_paise: tuple[int, ...] = PLAN_AMOUNTS_PAISE


class Cohort(BaseModel):
    model_config = ConfigDict(frozen=True)

    subscriptions: list[Subscription]
    events: list[FailureEvent]
    subjects: dict[str, SimSubject]


def generate_cohort(spec: CohortSpec, start_at: datetime) -> Cohort:
    rng = random.Random(spec.seed)
    classes = list(spec.class_weights)
    weights = [spec.class_weights[c] for c in classes]

    subscriptions: list[Subscription] = []
    events: list[FailureEvent] = []
    subjects: dict[str, SimSubject] = {}

    for index in range(spec.size):
        subscription_id = f"sub_{index:04d}"
        customer_id = f"cust_{index:04d}"
        latent_class = rng.choices(classes, weights=weights, k=1)[0]
        amount = rng.choice(spec.plan_amounts_paise)
        occurred_at = start_at + timedelta(hours=rng.uniform(0, FAILURE_SPREAD_HOURS))
        reason, source, step = canonical_decline(latent_class)

        subscriptions.append(
            Subscription(
                subscription_id=subscription_id,
                customer_id=customer_id,
                plan_amount_paise=amount,
            )
        )
        events.append(
            FailureEvent(
                event_id=f"evt_{index:04d}",
                subscription_id=subscription_id,
                invoice_id=f"inv_{index:04d}",
                error_reason=reason,
                error_source=source,
                error_step=step,
                attempt_number=1,
                occurred_at=occurred_at,
                source="cohort",
            )
        )
        subjects[subscription_id] = SimSubject(
            subscription_id=subscription_id,
            latent_class=latent_class,
            plan_amount_paise=amount,
            declined_reason=reason,
            error_source=source,
            error_step=step,
            first_failure_at=occurred_at,
        )

    events.sort(key=lambda e: (e.occurred_at, e.subscription_id))
    return Cohort(subscriptions=subscriptions, events=events, subjects=subjects)
