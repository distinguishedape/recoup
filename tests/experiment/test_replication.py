from datetime import datetime, timezone

from recoup.experiment.replication import build_replicated_findings, run_replication
from recoup.experiment.sweep import Finding, SweepResult
from recoup.models.enums import Band
from recoup.orchestrate.runner import RunConfig

START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def config(**overrides) -> RunConfig:
    base = dict(run_id="rep", seed=3, band=Band.MID, cohort_size=40, start_at=START)
    base.update(overrides)
    return RunConfig(**base)


def finding(name: str, mid: float, survives: bool) -> Finding:
    return Finding(
        name=name,
        unit="paise",
        low=mid,
        mid=mid,
        high=mid,
        higher_is_better=True,
        survives=survives,
        note="fixture",
    )


def sweep(seed: int, survives: bool, mid: float = 100.0) -> SweepResult:
    return SweepResult(
        config=config(seed=seed),
        results={},
        findings=[finding("gross_recovered", mid, survives)],
    )


def test_a_finding_that_survives_everywhere_replicates():
    result = build_replicated_findings({3: sweep(3, True), 11: sweep(11, True)})
    assert result[0].replicates is True
    assert "all 2 cohorts" in result[0].note


def test_a_finding_that_survives_in_most_cohorts_does_not_replicate():
    # Deliberately strict. Surviving in three of four is how a lucky result
    # gets laundered into a robust-looking one.
    result = build_replicated_findings(
        {3: sweep(3, True), 11: sweep(11, True), 29: sweep(29, True), 47: sweep(47, False)}
    )
    assert result[0].replicates is False
    assert "3 of 4" in result[0].note
    assert "47" in result[0].note


def test_a_finding_that_survives_nowhere_says_so_plainly():
    result = build_replicated_findings({3: sweep(3, False), 11: sweep(11, False)})
    assert result[0].replicates is False
    assert result[0].note == "survives in no cohort"


def test_the_per_seed_values_are_kept_so_the_spread_is_visible():
    result = build_replicated_findings(
        {3: sweep(3, True, 500.0), 11: sweep(11, False, -300.0)}
    )
    assert result[0].per_seed == {3: 500.0, 11: -300.0}
    assert result[0].mean == 100.0


def test_which_cohorts_it_survived_in_is_recorded():
    result = build_replicated_findings(
        {3: sweep(3, True), 11: sweep(11, False), 29: sweep(29, True)}
    )
    assert result[0].survived_in == [3, 29]


def test_no_cohorts_yields_no_findings():
    assert build_replicated_findings({}) == []


def test_running_a_replication_sweeps_every_seed(tmp_path):
    result = run_replication(config(), [3, 11], tmp_path)
    assert sorted(result.sweeps) == [3, 11]
    assert result.seeds == [3, 11]
    assert {f.name for f in result.findings} == {
        "gross_recovered",
        "net_recovered",
        "recovery_rate",
        "attempts_per_recovery",
        "wasted_attempts",
    }


def test_each_cohort_gets_its_own_seed_not_the_configs(tmp_path):
    result = run_replication(config(seed=999), [3, 11], tmp_path)
    assert result.sweeps[3].config.seed == 3
    assert result.sweeps[11].config.seed == 11


def test_the_efficiency_findings_replicate_on_real_cohorts(tmp_path):
    # The substantive claim this project actually makes: fewer attempts per
    # recovery, and far less waste, in every cohort tried.
    result = run_replication(config(cohort_size=200), [3, 11, 29], tmp_path)
    by_name = {f.name: f for f in result.findings}
    assert by_name["wasted_attempts"].replicates is True
    assert by_name["attempts_per_recovery"].replicates is True
