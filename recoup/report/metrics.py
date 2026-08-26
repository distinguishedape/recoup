"""Per-arm metrics and the paired comparison.

Everything here is computed from ``RunResult`` outcomes, which are
themselves derived from the audit log's terminal records. Nothing is
counted by instrumentation inside the pipeline, so a bug in the pipeline
cannot quietly improve its own score.

Two definitions carry weight and are stated in the report:

* **Recovery rate excludes voluntary churn.** A customer who revoked
  their mandate did not fail to be recovered; they left. Leaving them in
  the denominator rewards whichever arm gives up on them fastest.
* **A wasted attempt is a charge spent on a cause a retry cannot fix,
  on a subject that never recovered.** Charges, specifically. An earlier
  version summed every executed action, so a metric named "charge attempts"
  moved whenever messaging behaviour changed -- which made rescheduling blocked
  contacts look like a regression when it was recovering money. The second half matters: a charge
  after a successful instrument update is not a retry of the dead card,
  and counting it as waste would penalise the intervention that worked.
"""

from statistics import mean

from pydantic import BaseModel, ConfigDict

from recoup.models.enums import FailureClass, TerminalState
from recoup.orchestrate.runner import RunResult

ZERO_RETRY_CLASSES: frozenset[FailureClass] = frozenset(
    {
        FailureClass.INSTRUMENT_INVALID,
        FailureClass.MANDATE_REVOKED,
        FailureClass.RISK_DECLINE,
    }
)


class ArmMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    arm: str
    cohort_size: int
    recovered: int
    voluntary_churn: int
    manual_review: int
    unrecovered: int
    involuntary_denominator: int
    recovery_rate: float
    gross_recovered_paise: int
    total_cost_paise: int
    net_recovered_paise: int
    charge_attempts: int
    attempts_per_recovery: float
    wasted_attempts: int
    mean_hours_to_recovery: float


class Comparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    control: ArmMetrics
    treatment: ArmMetrics
    gross_lift_paise: int
    net_lift_paise: int
    recovery_rate_lift_pp: float
    attempts_per_recovery_delta: float
    wasted_attempts_avoided: int
    wasted_attempt_reduction: float


def _mean_hours_to_recovery(result: RunResult) -> float:
    """Hours from each subject's own first failure to its recovery.

    The cohort's failures are spread over two days, so measuring from a
    single run-start would conflate "recovered slowly" with "failed late".
    """
    spans = [
        (o.recovered_at - o.first_failure_at).total_seconds() / 3600
        for o in result.outcomes
        if o.recovered_at is not None and o.first_failure_at is not None
    ]
    return mean(spans) if spans else 0.0


def compute_metrics(result: RunResult, arm: str) -> ArmMetrics:
    outcomes = result.outcomes
    counts = {state: 0 for state in TerminalState}
    for outcome in outcomes:
        counts[outcome.terminal] += 1

    recovered = counts[TerminalState.RECOVERED]
    voluntary = counts[TerminalState.VOLUNTARY_CHURN]
    denominator = len(outcomes) - voluntary
    charge_attempts = sum(o.charge_attempts for o in outcomes)

    wasted = sum(
        o.charge_attempts
        for o in outcomes
        if o.failure_class in ZERO_RETRY_CLASSES and o.terminal is not TerminalState.RECOVERED
    )

    return ArmMetrics(
        arm=arm,
        cohort_size=len(outcomes),
        recovered=recovered,
        voluntary_churn=voluntary,
        manual_review=counts[TerminalState.MANUAL_REVIEW],
        unrecovered=counts[TerminalState.UNRECOVERED],
        involuntary_denominator=denominator,
        recovery_rate=(recovered / denominator) if denominator else 0.0,
        gross_recovered_paise=result.gross_recovered_paise,
        total_cost_paise=result.total_cost_paise,
        net_recovered_paise=result.net_recovered_paise,
        charge_attempts=charge_attempts,
        attempts_per_recovery=(charge_attempts / recovered) if recovered else 0.0,
        wasted_attempts=wasted,
        mean_hours_to_recovery=_mean_hours_to_recovery(result),
    )


def compare(control: ArmMetrics, treatment: ArmMetrics) -> Comparison:
    avoided = control.wasted_attempts - treatment.wasted_attempts
    reduction = (avoided / control.wasted_attempts) if control.wasted_attempts else 0.0
    return Comparison(
        control=control,
        treatment=treatment,
        gross_lift_paise=treatment.gross_recovered_paise - control.gross_recovered_paise,
        net_lift_paise=treatment.net_recovered_paise - control.net_recovered_paise,
        recovery_rate_lift_pp=(treatment.recovery_rate - control.recovery_rate) * 100,
        attempts_per_recovery_delta=(
            treatment.attempts_per_recovery - control.attempts_per_recovery
        ),
        wasted_attempts_avoided=avoided,
        wasted_attempt_reduction=reduction,
    )
