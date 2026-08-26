from datetime import datetime, timedelta, timezone

from recoup.models.enums import FailureClass, TerminalState
from recoup.orchestrate.runner import RunResult, SubjectOutcome
from recoup.report.metrics import ZERO_RETRY_CLASSES, compare, compute_metrics

START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def outcome(sub_id, terminal, failure_class=FailureClass.INSUFFICIENT_FUNDS,
            gross=0, cost=0, executed=0, hours=None) -> SubjectOutcome:
    return SubjectOutcome(
        subscription_id=sub_id,
        failure_class=failure_class,
        terminal=terminal,
        gross_recovered_paise=gross,
        cost_paise=cost,
        actions_executed=executed,
        actions_blocked=0,
        first_failure_at=START,
        recovered_at=START + timedelta(hours=hours) if hours is not None else None,
    )


def result(*outcomes) -> RunResult:
    return RunResult(
        run_id="test",
        config_hash="deadbeef",
        outcomes=list(outcomes),
        gross_recovered_paise=sum(o.gross_recovered_paise for o in outcomes),
        total_cost_paise=sum(o.cost_paise for o in outcomes),
    )


def test_the_zero_retry_classes_are_the_three_a_retry_cannot_fix():
    assert ZERO_RETRY_CLASSES == frozenset({
        FailureClass.INSTRUMENT_INVALID,
        FailureClass.MANDATE_REVOKED,
        FailureClass.RISK_DECLINE,
    })


def test_terminal_states_are_counted():
    m = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, gross=99900, hours=24),
        outcome("b", TerminalState.UNRECOVERED),
        outcome("c", TerminalState.VOLUNTARY_CHURN, FailureClass.MANDATE_REVOKED),
        outcome("d", TerminalState.MANUAL_REVIEW, FailureClass.RISK_DECLINE),
    ), "treatment")
    assert (m.cohort_size, m.recovered, m.unrecovered, m.voluntary_churn, m.manual_review) == (4, 1, 1, 1, 1)


def test_voluntary_churn_leaves_the_recovery_denominator():
    m = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, gross=99900, hours=24),
        outcome("b", TerminalState.UNRECOVERED),
        outcome("c", TerminalState.VOLUNTARY_CHURN, FailureClass.MANDATE_REVOKED),
    ), "treatment")
    assert m.involuntary_denominator == 2
    assert m.recovery_rate == 0.5


def test_an_all_voluntary_cohort_reports_a_zero_rate_rather_than_dividing_by_zero():
    m = compute_metrics(
        result(outcome("c", TerminalState.VOLUNTARY_CHURN, FailureClass.MANDATE_REVOKED)),
        "treatment")
    assert m.involuntary_denominator == 0
    assert m.recovery_rate == 0.0


def test_net_recovery_is_gross_minus_every_rupee_spent():
    m = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, gross=99900, cost=320, hours=24),
        outcome("b", TerminalState.UNRECOVERED, cost=900),
    ), "treatment")
    assert (m.gross_recovered_paise, m.total_cost_paise, m.net_recovered_paise) == (99900, 1220, 98680)


def test_attempts_per_recovery_uses_charge_attempts_only():
    m = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, gross=99900, cost=600, executed=2, hours=24),
        outcome("b", TerminalState.UNRECOVERED, cost=900, executed=3),
    ), "treatment")
    assert m.charge_attempts == 5
    assert m.attempts_per_recovery == 5.0


def test_attempts_per_recovery_is_zero_when_nothing_recovered():
    assert compute_metrics(result(outcome("b", TerminalState.UNRECOVERED, executed=3)), "x").attempts_per_recovery == 0.0


def test_wasted_attempts_are_those_spent_on_causes_a_retry_cannot_fix():
    m = compute_metrics(result(
        outcome("a", TerminalState.UNRECOVERED, FailureClass.INSTRUMENT_INVALID, executed=3),
        outcome("b", TerminalState.VOLUNTARY_CHURN, FailureClass.MANDATE_REVOKED, executed=3),
        outcome("c", TerminalState.UNRECOVERED, FailureClass.INSUFFICIENT_FUNDS, executed=2),
    ), "control")
    assert m.wasted_attempts == 6


def test_a_recovered_subject_never_counts_as_wasted():
    m = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, FailureClass.INSTRUMENT_INVALID,
                gross=99900, executed=3, hours=8)), "treatment")
    assert m.wasted_attempts == 0


def test_mean_time_to_recovery_is_measured_from_each_subjects_own_failure():
    m = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, gross=99900, hours=24),
        outcome("b", TerminalState.RECOVERED, gross=99900, hours=72),
    ), "treatment")
    assert m.mean_hours_to_recovery == 48.0


def test_mean_time_to_recovery_is_zero_when_nothing_recovered():
    assert compute_metrics(result(outcome("b", TerminalState.UNRECOVERED)), "x").mean_hours_to_recovery == 0.0


def test_the_comparison_states_money_and_rate_side_by_side():
    control = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, gross=99900, cost=900, executed=3, hours=72),
        outcome("b", TerminalState.UNRECOVERED, cost=900, executed=3),
    ), "control")
    treatment = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, gross=99900, cost=320, executed=1, hours=24),
        outcome("b", TerminalState.RECOVERED, gross=99900, cost=620, executed=2, hours=48),
    ), "treatment")
    c = compare(control, treatment)
    assert c.gross_lift_paise == 99900
    assert c.net_lift_paise == (199800 - 940) - (99900 - 1800)
    assert c.recovery_rate_lift_pp == 50.0


def test_the_comparison_reports_attempts_saved_as_a_negative_delta():
    control = compute_metrics(result(outcome("a", TerminalState.RECOVERED, gross=99900, executed=4, hours=72)), "control")
    treatment = compute_metrics(result(outcome("a", TerminalState.RECOVERED, gross=99900, executed=1, hours=24)), "treatment")
    assert compare(control, treatment).attempts_per_recovery_delta == -3.0


def test_wasted_attempt_reduction_is_a_share_of_what_the_control_wasted():
    control = compute_metrics(result(
        outcome("a", TerminalState.UNRECOVERED, FailureClass.INSTRUMENT_INVALID, executed=3),
        outcome("b", TerminalState.VOLUNTARY_CHURN, FailureClass.MANDATE_REVOKED, executed=3),
    ), "control")
    treatment = compute_metrics(result(
        outcome("a", TerminalState.UNRECOVERED, FailureClass.INSTRUMENT_INVALID, executed=0),
        outcome("b", TerminalState.VOLUNTARY_CHURN, FailureClass.MANDATE_REVOKED, executed=0),
    ), "treatment")
    c = compare(control, treatment)
    assert c.wasted_attempts_avoided == 6
    assert c.wasted_attempt_reduction == 1.0


def test_wasted_attempt_reduction_is_zero_when_the_control_wasted_nothing():
    control = compute_metrics(result(outcome("a", TerminalState.UNRECOVERED, executed=1)), "c")
    treatment = compute_metrics(result(outcome("a", TerminalState.UNRECOVERED, executed=1)), "t")
    assert compare(control, treatment).wasted_attempt_reduction == 0.0
