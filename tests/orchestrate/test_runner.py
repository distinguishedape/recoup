from datetime import datetime, timedelta, timezone

import pytest

from recoup.audit.log import AuditLog
from recoup.models.enums import ActionType, Band, FailureClass, TerminalState
from recoup.orchestrate.runner import RunConfig, config_hash, run_recoup_arm

START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def config(**overrides) -> RunConfig:
    base = dict(run_id="test", seed=11, band=Band.MID, cohort_size=10, start_at=START)
    base.update(overrides)
    return RunConfig(**base)


@pytest.fixture()
def audit(tmp_path):
    log = AuditLog(tmp_path / "audit.db", tmp_path / "audit.jsonl")
    yield log
    log.close()


def test_a_cohort_of_ten_runs_end_to_end(audit):
    result = run_recoup_arm(config(), audit)
    assert len(result.outcomes) == 10


def test_every_subject_reaches_exactly_one_terminal_state(audit):
    result = run_recoup_arm(config(cohort_size=60), audit)
    counts = {state: 0 for state in TerminalState}
    for outcome in result.outcomes:
        counts[outcome.terminal] += 1
    assert sum(counts.values()) == 60


def test_a_revoked_mandate_is_counted_as_voluntary_churn_not_a_recovery_failure(audit):
    result = run_recoup_arm(config(cohort_size=100, seed=3), audit)
    revoked = [o for o in result.outcomes if o.failure_class is FailureClass.MANDATE_REVOKED]
    assert revoked
    assert all(o.terminal is TerminalState.VOLUNTARY_CHURN for o in revoked)


def test_a_revoked_mandate_is_never_charged_or_contacted(audit):
    result = run_recoup_arm(config(cohort_size=100, seed=3), audit)
    revoked = [o for o in result.outcomes if o.failure_class is FailureClass.MANDATE_REVOKED]
    assert revoked
    assert all(o.cost_paise == 0 for o in revoked)
    billable = {
        ActionType.RETRY_CHARGE.value,
        ActionType.SEND_MESSAGE.value,
        ActionType.REQUEST_INSTRUMENT_UPDATE.value,
    }
    for outcome in revoked:
        executed = [
            r.payload["action_type"]
            for r in audit.reconstruct(outcome.subscription_id)
            if r.stage == "execute"
        ]
        assert not (set(executed) & billable)


def test_a_risk_decline_reaches_a_human_rather_than_dying_silently(audit):
    result = run_recoup_arm(config(cohort_size=100, seed=3), audit)
    risky = [o for o in result.outcomes if o.failure_class is FailureClass.RISK_DECLINE]
    assert risky
    assert all(o.terminal is TerminalState.MANUAL_REVIEW for o in risky)


def test_an_invalid_instrument_is_never_retried_on_the_dead_card(audit):
    result = run_recoup_arm(config(cohort_size=100, seed=3), audit)
    charges_before_update = []
    for outcome in result.outcomes:
        if outcome.failure_class is not FailureClass.INSTRUMENT_INVALID:
            continue
        records = audit.reconstruct(outcome.subscription_id)
        seen_update = False
        for record in records:
            if record.stage != "execute":
                continue
            if record.payload["action_type"] == ActionType.REQUEST_INSTRUMENT_UPDATE.value:
                seen_update = record.payload["succeeded"]
            if record.payload["action_type"] == ActionType.RETRY_CHARGE.value and not seen_update:
                charges_before_update.append(outcome.subscription_id)
    assert charges_before_update == []


def test_recovered_subjects_bring_in_their_full_plan_amount(audit):
    result = run_recoup_arm(config(cohort_size=40, seed=5), audit)
    recovered = [o for o in result.outcomes if o.terminal is TerminalState.RECOVERED]
    assert recovered
    assert all(o.gross_recovered_paise > 0 for o in recovered)
    assert all(
        o.gross_recovered_paise == 0
        for o in result.outcomes
        if o.terminal is not TerminalState.RECOVERED
    )


def test_gross_recovery_is_the_sum_of_the_subject_amounts(audit):
    result = run_recoup_arm(config(cohort_size=40, seed=5), audit)
    assert result.gross_recovered_paise == sum(o.gross_recovered_paise for o in result.outcomes)


def test_cost_is_charged_for_unrecovered_subjects_too(audit):
    result = run_recoup_arm(config(cohort_size=40, seed=5), audit)
    unrecovered_cost = sum(
        o.cost_paise for o in result.outcomes if o.terminal is TerminalState.UNRECOVERED
    )
    assert unrecovered_cost > 0
    assert result.total_cost_paise >= unrecovered_cost


def test_net_recovery_is_gross_minus_every_rupee_spent(audit):
    result = run_recoup_arm(config(cohort_size=40, seed=5), audit)
    assert result.net_recovered_paise == result.gross_recovered_paise - result.total_cost_paise


def test_the_same_seed_and_band_reproduce_the_same_result(tmp_path):
    def run(name: str):
        log = AuditLog(tmp_path / f"{name}.db")
        try:
            return run_recoup_arm(config(), log)
        finally:
            log.close()

    first, second = run("a"), run("b")
    assert first.gross_recovered_paise == second.gross_recovered_paise
    assert first.total_cost_paise == second.total_cost_paise
    assert [o.terminal for o in first.outcomes] == [o.terminal for o in second.outcomes]


def test_the_high_band_recovers_more_money_than_the_low_band(tmp_path):
    def run(band: Band) -> int:
        log = AuditLog(tmp_path / f"{band.value}.db")
        try:
            return run_recoup_arm(config(cohort_size=200, band=band), log).gross_recovered_paise
        finally:
            log.close()

    assert run(Band.HIGH) > run(Band.LOW)


def test_the_config_hash_changes_when_the_configuration_changes():
    assert config_hash(config()) == config_hash(config())
    assert config_hash(config()) != config_hash(config(seed=12))
    assert config_hash(config()) != config_hash(config(band=Band.HIGH))


def test_every_subject_has_an_ingest_a_classify_and_a_plan_record(audit):
    result = run_recoup_arm(config(cohort_size=10), audit)
    for outcome in result.outcomes:
        stages = {r.stage for r in audit.reconstruct(outcome.subscription_id)}
        assert {"ingest", "classify", "plan"} <= stages


def test_every_execution_in_the_audit_log_names_the_verdict_that_allowed_it(audit):
    run_recoup_arm(config(cohort_size=30), audit)
    executions = [r for r in audit.all() if r.stage == "execute"]
    assert executions
    assert all(r.payload.get("verdict_rule") for r in executions)


def test_blocked_actions_are_audited_with_the_rule_that_blocked_them(audit):
    run_recoup_arm(config(cohort_size=100, seed=3), audit)
    blocks = [r for r in audit.all() if r.stage in {"policy_block", "ladder_block"}]
    assert blocks
    assert all(r.payload.get("rule") for r in blocks)


def test_the_run_terminates_and_does_not_loop_forever(audit):
    result = run_recoup_arm(config(cohort_size=200, seed=9), audit)
    assert len(result.outcomes) == 200


def test_an_opted_out_subject_is_never_messaged_and_the_denial_is_audited(audit):
    opted = frozenset(f"sub_{i:04d}" for i in range(5))
    run_recoup_arm(config(cohort_size=100, seed=3, opted_out_ids=opted), audit)
    contact_types = {
        ActionType.SEND_MESSAGE.value,
        ActionType.REQUEST_INSTRUMENT_UPDATE.value,
    }
    denials = 0
    for sub_id in opted:
        records = audit.reconstruct(sub_id)
        executed = {
            r.payload["action_type"] for r in records if r.stage == "execute"
        }
        assert not (executed & contact_types), f"{sub_id} was contacted despite opting out"
        denials += sum(1 for r in records if r.stage in {"policy_block", "ladder_block"})
    assert denials > 0


def test_a_live_promise_to_pay_suppresses_action_and_names_the_rule(audit):
    promised = {f"sub_{i:04d}": START + timedelta(days=30) for i in range(5)}
    run_recoup_arm(config(cohort_size=100, seed=3, promise_to_pay=promised), audit)
    blocks = [
        r
        for sub_id in promised
        for r in audit.reconstruct(sub_id)
        if r.stage == "policy_block" and r.payload["rule"] == "promise_to_pay_suppression"
    ]
    assert blocks


def test_a_failing_policy_rule_halts_the_batch_rather_than_proceeding_ungated(audit, monkeypatch):
    import recoup.policy.engine as engine_module

    def broken(action, context):
        raise RuntimeError("policy engine is unreachable")

    monkeypatch.setattr(engine_module, "RULES", (broken,))
    with pytest.raises(RuntimeError):
        run_recoup_arm(config(cohort_size=100), audit)


def test_a_dead_card_actually_gets_asked_to_supply_a_new_one(audit):
    # Regression: the ladder once required T1 to have executed before T2 could
    # open, and the dead-card plan starts at T2, so every one of these subjects
    # had its entire intervention blocked and the class recovered nothing.
    result = run_recoup_arm(config(cohort_size=200, seed=3), audit)
    invalid = [
        o for o in result.outcomes if o.failure_class is FailureClass.INSTRUMENT_INVALID
    ]
    assert invalid
    requests = 0
    for outcome in invalid:
        requests += sum(
            1
            for r in audit.reconstruct(outcome.subscription_id)
            if r.stage == "execute"
            and r.payload["action_type"] == ActionType.REQUEST_INSTRUMENT_UPDATE.value
        )
    assert requests > 0, "no dead-card subject was ever asked for a new instrument"


def test_some_dead_card_subjects_recover_once_they_supply_a_new_instrument(audit):
    result = run_recoup_arm(config(cohort_size=200, seed=3), audit)
    invalid = [
        o for o in result.outcomes if o.failure_class is FailureClass.INSTRUMENT_INVALID
    ]
    recovered = [o for o in invalid if o.terminal is TerminalState.RECOVERED]
    assert recovered, "the instrument-update intervention recovered nobody"


def test_a_blocked_notification_does_not_also_kill_the_retries_behind_it(audit):
    # Regression. A planner that placed retries a tier above the notification
    # had every retry blocked as tier_not_open whenever the notification fell
    # outside the contact window. The deterministic planner puts everything at
    # tier one, so only a differently-tiered plan exposed it.
    from recoup.models.core import Action, Classification, FailureEvent
    from recoup.models.enums import Tier
    from recoup.plan import llm_planner

    def tiered_plan(event, classification, client, now):
        from recoup.models.core import InterventionPlan

        return InterventionPlan(
            subscription_id=event.subscription_id,
            failure_class=classification.failure_class,
            actions=[
                Action(
                    action_id=f"{event.subscription_id}:act:0",
                    subscription_id=event.subscription_id,
                    type=ActionType.RETRY_CHARGE,
                    scheduled_at=now + timedelta(hours=24),
                    tier=Tier.T2_REQUEST_ACTION,
                    channel=None,
                    template_id=None,
                    free_text=None,
                    reason="a retry the planner placed at tier two",
                )
            ],
        )

    import recoup.orchestrate.runner as runner_module

    original = runner_module.build_intervention_plan
    runner_module.build_intervention_plan = tiered_plan
    try:
        result = run_recoup_arm(config(cohort_size=60, seed=3), audit)
    finally:
        runner_module.build_intervention_plan = original

    executed = [r for r in audit.all() if r.stage == "execute"]
    assert executed, "every retry was blocked because it sat above the starting tier"
