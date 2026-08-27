from datetime import datetime, timedelta, timezone

import pytest

from recoup.audit.log import AuditLog
from recoup.execute.executor import CHARGE_ATTEMPT_COST_PAISE
from recoup.experiment.control import CONTROL_RETRY_DELAYS_HOURS, run_control_arm
from recoup.models.enums import ActionType, Band, FailureClass, TerminalState
from recoup.orchestrate.runner import RunConfig

START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def config(**overrides) -> RunConfig:
    base = dict(run_id="control", seed=11, band=Band.MID, cohort_size=40, start_at=START)
    base.update(overrides)
    return RunConfig(**base)


@pytest.fixture()
def audit(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    yield log
    log.close()


def test_the_baseline_ladder_is_three_retries_a_day_apart():
    assert CONTROL_RETRY_DELAYS_HOURS == (24, 48, 72)


def test_every_subject_gets_an_outcome(audit):
    assert len(run_control_arm(config(), audit).outcomes) == 40


def test_no_subject_is_charged_more_than_the_baseline_allows(audit):
    result = run_control_arm(config(), audit)
    max_cost = CHARGE_ATTEMPT_COST_PAISE * len(CONTROL_RETRY_DELAYS_HOURS)
    assert all(o.cost_paise <= max_cost for o in result.outcomes)


def test_an_unrecovered_subject_is_charged_the_full_ladder(audit):
    result = run_control_arm(config(), audit)
    unrecovered = [o for o in result.outcomes if o.terminal is TerminalState.UNRECOVERED]
    assert unrecovered
    assert all(
        o.cost_paise == CHARGE_ATTEMPT_COST_PAISE * len(CONTROL_RETRY_DELAYS_HOURS)
        for o in unrecovered
    )


def test_the_control_arm_never_contacts_anyone(audit):
    run_control_arm(config(), audit)
    contact_types = {ActionType.SEND_MESSAGE.value, ActionType.REQUEST_INSTRUMENT_UPDATE.value}
    executed = {r.payload.get("action_type") for r in audit.all() if r.stage == "control_execute"}
    assert not (executed & contact_types)


def test_the_control_arm_wastes_money_on_dead_instruments(audit):
    result = run_control_arm(config(cohort_size=200, seed=3), audit)
    dead = [
        o
        for o in result.outcomes
        if o.failure_class in {FailureClass.INSTRUMENT_INVALID, FailureClass.MANDATE_REVOKED}
    ]
    assert dead
    assert all(o.cost_paise > 0 for o in dead)


def test_a_revoked_mandate_is_still_booked_as_voluntary_churn(audit):
    result = run_control_arm(config(cohort_size=200, seed=3), audit)
    revoked = [o for o in result.outcomes if o.failure_class is FailureClass.MANDATE_REVOKED]
    assert revoked
    assert all(o.terminal is TerminalState.VOLUNTARY_CHURN for o in revoked)


def test_the_control_arm_reaches_only_three_terminal_states(audit):
    result = run_control_arm(config(cohort_size=200, seed=3), audit)
    assert {o.terminal for o in result.outcomes} <= {
        TerminalState.RECOVERED,
        TerminalState.UNRECOVERED,
        TerminalState.VOLUNTARY_CHURN,
    }


def test_a_recovered_subject_stops_being_charged(audit):
    result = run_control_arm(config(cohort_size=200, seed=3), audit)
    recovered = [o for o in result.outcomes if o.terminal is TerminalState.RECOVERED]
    assert recovered
    assert all(o.cost_paise == o.actions_executed * CHARGE_ATTEMPT_COST_PAISE for o in recovered)
    assert all(1 <= o.actions_executed <= len(CONTROL_RETRY_DELAYS_HOURS) for o in recovered)
    assert any(o.actions_executed < len(CONTROL_RETRY_DELAYS_HOURS) for o in recovered)


def test_a_recovered_subject_records_when_it_recovered(audit):
    result = run_control_arm(config(cohort_size=200, seed=3), audit)
    for outcome in result.outcomes:
        if outcome.terminal is TerminalState.RECOVERED:
            assert outcome.recovered_at is not None
            assert outcome.recovered_at >= START + timedelta(hours=24)
        else:
            assert outcome.recovered_at is None


def test_a_recovered_subject_is_attributed_to_retry(audit):
    # The baseline has no payment link and never will -- everything it
    # recovers, it recovers by retrying the same instrument.
    result = run_control_arm(config(cohort_size=200, seed=3), audit)
    recovered = [o for o in result.outcomes if o.terminal is TerminalState.RECOVERED]
    assert recovered
    assert all(o.recovered_via == "retry" for o in recovered)
    unrecovered = [o for o in result.outcomes if o.terminal is not TerminalState.RECOVERED]
    assert all(o.recovered_via is None for o in unrecovered)


def test_every_subject_records_its_own_first_failure(audit):
    result = run_control_arm(config(cohort_size=40), audit)
    assert all(o.first_failure_at is not None for o in result.outcomes)


def test_gross_recovery_only_counts_recovered_subjects(audit):
    result = run_control_arm(config(cohort_size=200, seed=3), audit)
    assert result.gross_recovered_paise == sum(
        o.gross_recovered_paise for o in result.outcomes if o.terminal is TerminalState.RECOVERED
    )


def test_the_same_seed_reproduces_the_control_arm(tmp_path):
    def run(name: str):
        log = AuditLog(tmp_path / f"{name}.db")
        try:
            return run_control_arm(config(), log)
        finally:
            log.close()

    first, second = run("a"), run("b")
    assert [o.terminal for o in first.outcomes] == [o.terminal for o in second.outcomes]
    assert first.gross_recovered_paise == second.gross_recovered_paise


def test_every_charge_attempt_is_audited(audit):
    result = run_control_arm(config(cohort_size=20), audit)
    attempts = [r for r in audit.all() if r.stage == "control_execute"]
    expected = sum(o.cost_paise for o in result.outcomes) // CHARGE_ATTEMPT_COST_PAISE
    assert len(attempts) == expected
