from datetime import datetime, timezone

from recoup.experiment.sweep import build_findings, run_sweep
from recoup.models.enums import Band, FailureClass, TerminalState
from recoup.orchestrate.runner import RunConfig, RunResult, SubjectOutcome
from recoup.report.metrics import compare, compute_metrics

START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def config(**overrides) -> RunConfig:
    base = dict(run_id="sweep", seed=11, band=Band.MID, cohort_size=40, start_at=START)
    base.update(overrides)
    return RunConfig(**base)


def arm(name, recovered, total, gross, cost, executed):
    outcomes = [
        SubjectOutcome(
            subscription_id=f"sub_{i:04d}",
            failure_class=FailureClass.INSUFFICIENT_FUNDS,
            terminal=TerminalState.RECOVERED if i < recovered else TerminalState.UNRECOVERED,
            gross_recovered_paise=gross if i < recovered else 0,
            cost_paise=cost,
            actions_executed=executed,
            charge_attempts=executed,
            actions_blocked=0,
            first_failure_at=START,
            recovered_at=START if i < recovered else None,
        )
        for i in range(total)
    ]
    return compute_metrics(RunResult(
        run_id=name, config_hash="x", outcomes=outcomes,
        gross_recovered_paise=sum(o.gross_recovered_paise for o in outcomes),
        total_cost_paise=sum(o.cost_paise for o in outcomes)), name)


def comparison(control_recovered, treatment_recovered):
    return compare(arm("control", control_recovered, 100, 99900, 900, 3),
                   arm("treatment", treatment_recovered, 100, 99900, 400, 1))


def bands(low, mid, high):
    return {Band.LOW: comparison(*low), Band.MID: comparison(*mid), Band.HIGH: comparison(*high)}


def test_a_lift_positive_at_every_band_survives():
    f = build_findings(bands((20, 30), (30, 45), (40, 60)))
    assert next(x for x in f if x.name == "gross_recovered").survives is True


def test_a_lift_that_appears_only_at_the_high_band_does_not_survive():
    f = build_findings(bands((30, 30), (40, 40), (40, 60)))
    gross = next(x for x in f if x.name == "gross_recovered")
    assert gross.high > 0 and gross.survives is False and "high" in gross.note.lower()


def test_a_lift_that_fails_at_the_low_band_does_not_survive():
    f = build_findings(bands((40, 30), (30, 45), (40, 60)))
    assert next(x for x in f if x.name == "gross_recovered").survives is False


def test_a_metric_where_lower_is_better_survives_when_it_falls_everywhere():
    f = build_findings(bands((20, 30), (30, 45), (40, 60)))
    attempts = next(x for x in f if x.name == "attempts_per_recovery")
    assert attempts.higher_is_better is False and attempts.survives is True


def test_every_headline_metric_gets_a_finding():
    f = build_findings(bands((20, 30), (30, 45), (40, 60)))
    assert {x.name for x in f} == {
        "gross_recovered", "net_recovered", "recovery_rate",
        "attempts_per_recovery", "wasted_attempts"}


def test_every_finding_carries_its_unit():
    f = build_findings(bands((20, 30), (30, 45), (40, 60)))
    assert all(x.unit for x in f)
    assert next(x for x in f if x.name == "gross_recovered").unit == "paise"


def test_the_sweep_runs_the_experiment_once_per_band(tmp_path):
    assert set(run_sweep(config(), tmp_path).results) == {"low", "mid", "high"}


def test_each_band_writes_its_own_audit_directory(tmp_path):
    run_sweep(config(), tmp_path)
    for band in ("low", "mid", "high"):
        assert (tmp_path / band / "control.db").exists()
        assert (tmp_path / band / "treatment.db").exists()


def test_the_sweep_produces_findings(tmp_path):
    assert len(run_sweep(config(cohort_size=200, seed=3), tmp_path).findings) == 5


def test_the_sweep_is_reproducible(tmp_path):
    a = run_sweep(config(), tmp_path / "a")
    b = run_sweep(config(), tmp_path / "b")
    assert [x.survives for x in a.findings] == [x.survives for x in b.findings]
    assert [x.mid for x in a.findings] == [x.mid for x in b.findings]


def test_the_bands_in_the_results_override_the_configs_own_band(tmp_path):
    assert run_sweep(config(band=Band.LOW), tmp_path).results["high"].config.band is Band.HIGH
