"""Everything outside ``RunConfig`` that determines the measured numbers.

``config_hash`` covers the seed, the band, the cohort size and the start time.
Those are the inputs a reader assumes a frozen configuration is protecting, and
none of them is what actually moved the published figures.

What moved them was a sentence added to the planner prompt. It changed every
plan prompt hash, so every class was re-asked, and dead-card money shifted by
Rs 45,475 on a cause the change was not aimed at -- while ``config_hash``
stayed at ``7aa7962cac907ba0`` and ``--verify-frozen`` reported "configuration
verified unchanged". The freeze was true and useless at the same time.

So the registration covers the things that decide the outcome: the probability
bands and the timing model, the per-class budgets, the attempt and channel
costs, the cohort's own distribution, the deterministic schedule, the model
name, and the prompts -- both the system prompts and the *shape* of the
per-event prompts, rendered against a fixed probe event so a changed field
order counts as the re-ask it is.

Values, not source text: a reworded comment is not a measurement change and
must not be reported as one.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from recoup.models.core import Classification, FailureEvent
from recoup.models.enums import Band, FailureClass

#: A fixed event the per-event prompt builders are rendered against. Its values
#: are arbitrary and never leave this module; what matters is that the same
#: event produces the same text unless the builder itself changed.
PROBE_EVENT = FailureEvent(
    event_id="evt_probe",
    subscription_id="sub_probe",
    invoice_id="inv_probe",
    error_reason="payment_failed",
    error_source="gateway",
    error_step="payment_authorization",
    attempt_number=1,
    occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    source="cohort",
)

PROBE_CLASSIFICATION = Classification(
    failure_class=FailureClass.INSUFFICIENT_FUNDS,
    method="table",
    confidence=0.9,
    rationale="probe",
)


def measurement_inputs(model: str | None = None) -> dict[str, Any]:
    """The registered inputs, as plain JSON-able values.

    Imported lazily and read through their modules rather than pulled into this
    module's namespace at import time, so that a test (or a caller) patching a
    constant is seen here rather than silently missed -- which is the whole
    failure mode this guards against.
    """
    from recoup.classify import llm_resolver
    from recoup.execute import executor, probabilities
    from recoup.ingest import cohort
    from recoup.plan import budgets, fallback, llm_planner

    return {
        "probabilities": {
            "bands": {
                band.value: {
                    "retry_success": {
                        cls.value: probabilities.BANDS[band].retry_success[cls]
                        for cls in FailureClass
                    },
                    "update_request_conversion": (
                        probabilities.BANDS[band].update_request_conversion
                    ),
                    "pay_now_conversion": probabilities.BANDS[band].pay_now_conversion,
                }
                for band in Band
            },
            "retry_decay": probabilities.RETRY_DECAY,
            "post_update_charge_success": probabilities.POST_UPDATE_CHARGE_SUCCESS,
            "timing": {
                cls.value: [
                    probabilities.TIMING[cls].floor,
                    probabilities.TIMING[cls].ceiling,
                    probabilities.TIMING[cls].half_life_hours,
                ]
                for cls in FailureClass
            },
        },
        "budgets": {
            cls.value: [
                budgets.BUDGETS[cls].charge_retries,
                budgets.BUDGETS[cls].contacts,
            ]
            for cls in FailureClass
        },
        "costs": {
            "charge_attempt_paise": executor.CHARGE_ATTEMPT_COST_PAISE,
            "channel_paise": dict(sorted(executor.CHANNEL_COST_PAISE.items())),
        },
        "cohort": {
            "class_weights": {cls.value: cohort.CLASS_WEIGHTS[cls] for cls in FailureClass},
            "plan_amounts_paise": list(cohort.PLAN_AMOUNTS_PAISE),
            "failure_spread_hours": cohort.FAILURE_SPREAD_HOURS,
        },
        "schedule": {
            "funds_retry_delays_hours": list(fallback.FUNDS_RETRY_DELAYS_HOURS),
            "transient_retry_delays_hours": list(fallback.TRANSIENT_RETRY_DELAYS_HOURS),
            "unclassified_retry_delays_hours": list(fallback.UNCLASSIFIED_RETRY_DELAYS_HOURS),
            "final_notice_delay_hours": fallback.FINAL_NOTICE_DELAY_HOURS,
        },
        "prompts": {
            "planner_system": llm_planner.PLANNER_SYSTEM,
            "resolver_system": llm_resolver.RESOLVER_SYSTEM,
            "planner_user_shape": llm_planner.build_planner_prompt(
                PROBE_EVENT, PROBE_CLASSIFICATION
            ),
            "resolver_user_shape": llm_resolver.build_user_prompt(PROBE_EVENT),
        },
        "model": model or "",
    }


def inputs_hash(model: str | None = None) -> str:
    material = json.dumps(measurement_inputs(model), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def what_changed(registered: Any, current: Any, prefix: str = "") -> list[str]:
    """Dotted paths of every leaf that differs, so drift names itself.

    A digest that only says "something moved" sends the reader through every
    constant in the project. The registered inputs are stored in full precisely
    so this can answer the question instead.
    """
    if isinstance(registered, dict) and isinstance(current, dict):
        changed: list[str] = []
        for key in sorted(set(registered) | set(current)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in registered or key not in current:
                changed.append(path)
            else:
                changed.extend(what_changed(registered[key], current[key], path))
        return changed
    return [] if registered == current else [prefix]
