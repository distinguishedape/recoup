from datetime import datetime, timedelta, timezone

from recoup.models.enums import FailureClass, TerminalState
from recoup.orchestrate.runner import RunResult, SubjectOutcome
from recoup.report.metrics import ZERO_RETRY_CLASSES, compare, compute_metrics

START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def outcome(sub_id, terminal, failure_class=FailureClass.INSUFFICIENT_FUNDS,
            gross=0, cost=0, executed=0, hours=None, charges=None, via=None) -> SubjectOutcome:
    # `executed` counts every action; `charges` counts only charge attempts,
    # which is what the spec's efficiency metrics are defined on. Defaulting
    # charges to executed keeps the charge-only fixtures readable.
    return SubjectOutcome(
        subscription_id=sub_id,
        failure_class=failure_class,
        terminal=terminal,
        gross_recovered_paise=gross,
        cost_paise=cost,
        actions_executed=executed,
        charge_attempts=executed if charges is None else charges,
        actions_blocked=0,
        first_failure_at=START,
        recovered_at=START + timedelta(hours=hours) if hours is not None else None,
        recovered_via=via,
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


def test_efficiency_metrics_count_charges_and_ignore_messages():
    # The spec defines both on charge attempts. Summing every action made a
    # metric named "charge attempts" move whenever messaging changed, which
    # made rescheduling blocked contacts look like a regression when it was
    # recovering money.
    m = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, gross=99900, executed=6, charges=2, hours=24),
    ), "treatment")
    assert m.charge_attempts == 2
    assert m.attempts_per_recovery == 2.0


def test_a_wasted_attempt_is_a_charge_not_a_message():
    m = compute_metrics(result(
        outcome("a", TerminalState.UNRECOVERED, FailureClass.INSTRUMENT_INVALID,
                executed=5, charges=0),
    ), "treatment")
    # Asking a customer for a new card is the remedy for this cause, not waste.
    assert m.wasted_attempts == 0


def test_money_is_broken_down_by_failure_class():
    m = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, FailureClass.INSUFFICIENT_FUNDS, gross=99900, hours=24),
        outcome("b", TerminalState.RECOVERED, FailureClass.INSTRUMENT_INVALID, gross=49900, hours=24),
        outcome("c", TerminalState.UNRECOVERED, FailureClass.INSUFFICIENT_FUNDS),
    ), "treatment")
    assert m.money_by_class[FailureClass.INSUFFICIENT_FUNDS] == 99900
    assert m.money_by_class[FailureClass.INSTRUMENT_INVALID] == 49900


def test_every_class_appears_even_when_it_recovered_nothing():
    m = compute_metrics(result(outcome("a", TerminalState.UNRECOVERED)), "x")
    assert set(m.money_by_class) == set(FailureClass)
    assert m.money_by_class[FailureClass.MANDATE_REVOKED] == 0


def test_the_class_breakdown_sums_to_the_total():
    m = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, FailureClass.INSUFFICIENT_FUNDS, gross=99900, hours=1),
        outcome("b", TerminalState.RECOVERED, FailureClass.TRANSIENT_ISSUER, gross=49900, hours=1),
    ), "t")
    assert sum(m.money_by_class.values()) == m.gross_recovered_paise


def test_money_is_broken_down_by_the_mechanism_that_earned_it():
    m = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, FailureClass.INSUFFICIENT_FUNDS, gross=99900, hours=1, via="retry"),
        outcome("b", TerminalState.RECOVERED, FailureClass.INSUFFICIENT_FUNDS, gross=49900, hours=1, via="pay_now_link"),
    ), "treatment")
    assert m.money_by_mechanism["retry"] == 99900
    assert m.money_by_mechanism["pay_now_link"] == 49900


def test_an_unrecovered_subject_contributes_nothing_to_the_mechanism_breakdown():
    m = compute_metrics(result(
        outcome("a", TerminalState.UNRECOVERED, FailureClass.INSUFFICIENT_FUNDS),
    ), "treatment")
    assert sum(m.money_by_mechanism.values()) == 0


def test_the_mechanism_breakdown_sums_to_the_total():
    m = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, FailureClass.INSUFFICIENT_FUNDS, gross=99900, hours=1, via="retry"),
        outcome("b", TerminalState.RECOVERED, FailureClass.INSTRUMENT_INVALID, gross=49900, hours=1, via="instrument_update"),
        outcome("c", TerminalState.RECOVERED, FailureClass.INSUFFICIENT_FUNDS, gross=29900, hours=1, via="pay_now_link"),
    ), "t")
    assert sum(m.money_by_mechanism.values()) == m.gross_recovered_paise


def test_money_is_available_per_failed_charge_not_only_in_total():
    """A total scales to nothing in a reader's head. Per-charge does.

    Integer division, in paise, like every other money figure here -- a float
    would be the first one in the codebase and there is no reason to start.
    Denominated on every subject in the arm, recovered or not, which is the
    same denominator ``net_recovered_paise`` uses, so the two cannot disagree.
    """
    m = compute_metrics(result(
        outcome("a", TerminalState.RECOVERED, FailureClass.INSUFFICIENT_FUNDS,
                gross=99900, cost=300, hours=1, via="retry"),
        outcome("b", TerminalState.UNRECOVERED, FailureClass.INSUFFICIENT_FUNDS,
                gross=0, cost=600),
    ), "treatment")
    assert m.cohort_size == 2
    assert m.net_recovered_per_subject_paise == (99900 - 900) // 2
    assert m.cost_per_subject_paise == 900 // 2


def test_per_subject_money_is_zero_rather_than_a_crash_on_an_empty_arm():
    m = compute_metrics(result(), "treatment")
    assert m.cohort_size == 0
    assert m.net_recovered_per_subject_paise == 0
    assert m.cost_per_subject_paise == 0
