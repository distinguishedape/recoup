import json
from datetime import datetime, timezone

import pytest

from recoup.experiment.harness import (
    ConfigurationDrift, freeze_config, run_paired_experiment, verify_frozen_config,
)
from recoup.models.enums import Band
from recoup.orchestrate.runner import RunConfig, config_hash

START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def config(**overrides) -> RunConfig:
    base = dict(run_id="exp", seed=11, band=Band.MID, cohort_size=60, start_at=START)
    base.update(overrides)
    return RunConfig(**base)


def test_both_arms_run_over_the_same_cohort_size(tmp_path):
    r = run_paired_experiment(config(), tmp_path)
    assert r.control.cohort_size == 60 and r.treatment.cohort_size == 60


def test_both_arms_see_the_same_voluntary_churn(tmp_path):
    r = run_paired_experiment(config(cohort_size=200, seed=3), tmp_path)
    assert r.control.voluntary_churn == r.treatment.voluntary_churn


def test_the_arms_write_separate_audit_databases(tmp_path):
    r = run_paired_experiment(config(), tmp_path)
    assert r.control_audit_path != r.treatment_audit_path
    assert (tmp_path / "control.db").exists() and (tmp_path / "treatment.db").exists()


def test_the_comparison_is_computed_from_both_arms(tmp_path):
    r = run_paired_experiment(config(), tmp_path)
    assert r.comparison.control == r.control and r.comparison.treatment == r.treatment


def test_recoup_wastes_fewer_attempts_than_the_baseline(tmp_path):
    r = run_paired_experiment(config(cohort_size=200, seed=3), tmp_path)
    assert r.treatment.wasted_attempts < r.control.wasted_attempts


def test_the_experiment_is_reproducible(tmp_path):
    a = run_paired_experiment(config(), tmp_path / "a")
    b = run_paired_experiment(config(), tmp_path / "b")
    assert a.comparison.gross_lift_paise == b.comparison.gross_lift_paise
    assert a.comparison.net_lift_paise == b.comparison.net_lift_paise


def test_freezing_writes_the_configuration_hash(tmp_path):
    path = tmp_path / "frozen.json"
    assert freeze_config(config(), path) == config_hash(config())
    assert path.exists()


def test_verifying_an_unchanged_configuration_passes(tmp_path):
    path = tmp_path / "frozen.json"
    freeze_config(config(), path)
    assert verify_frozen_config(config(), path) == config_hash(config())


def test_verifying_a_changed_configuration_is_refused(tmp_path):
    path = tmp_path / "frozen.json"
    freeze_config(config(), path)
    with pytest.raises(ConfigurationDrift):
        verify_frozen_config(config(seed=12), path)


def test_verifying_against_a_missing_freeze_file_is_refused(tmp_path):
    with pytest.raises(ConfigurationDrift):
        verify_frozen_config(config(), tmp_path / "nothing.json")


def test_the_freeze_file_records_what_was_frozen_for_a_human_to_read(tmp_path):
    path = tmp_path / "frozen.json"
    freeze_config(config(), path)
    frozen = json.loads(path.read_text(encoding="utf-8"))
    assert frozen["seed"] == 11 and frozen["band"] == "mid" and frozen["cohort_size"] == 60
