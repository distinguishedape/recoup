"""The drift guard has to fire on the command that is actually published.

``--verify-frozen`` existed before this and would not have caught the prompt
change, because the published command does not pass it. A guard nobody invokes
is a guard that does not exist, so the check runs whenever a frozen file is
present and re-freezing is the deliberate act that re-registers.
"""

import json

import pytest

from recoup.plan import llm_planner
from scripts.run_experiment import main

FAST = ["--cohort-size", "1", "--no-llm"]


def freeze(tmp_path) -> int:
    return main([*FAST, "--freeze", "--out-dir", str(tmp_path)])


def test_a_freeze_records_the_measurement_inputs(tmp_path):
    assert freeze(tmp_path) == 0
    frozen = json.loads((tmp_path / "frozen_config.json").read_text(encoding="utf-8"))
    assert frozen["inputs"]["prompts"]["planner_system"] == llm_planner.PLANNER_SYSTEM
    assert (tmp_path / "report.md").exists()


def test_an_edited_prompt_refuses_to_publish_and_names_itself(tmp_path, capsys, monkeypatch):
    freeze(tmp_path)
    monkeypatch.setattr(
        llm_planner, "PLANNER_SYSTEM", llm_planner.PLANNER_SYSTEM + "\nOne more sentence."
    )
    (tmp_path / "report.md").unlink()

    assert main([*FAST, "--out-dir", str(tmp_path)]) == 1

    err = capsys.readouterr().err
    assert "prompts.planner_system" in err
    assert not (tmp_path / "report.md").exists(), "a drifted run must not publish a bundle"


def test_the_refusal_happens_before_the_experiment_runs(tmp_path, capsys, monkeypatch):
    """Two minutes of simulation and then a refusal teaches people to pass the
    override. The check is cheap; it goes first."""
    freeze(tmp_path)
    monkeypatch.setattr(
        llm_planner, "PLANNER_SYSTEM", llm_planner.PLANNER_SYSTEM + "\nOne more sentence."
    )

    def explode(*args, **kwargs):
        raise AssertionError("the sweep must not start on a drifted configuration")

    monkeypatch.setattr("scripts.run_experiment.run_sweep", explode)
    assert main([*FAST, "--out-dir", str(tmp_path)]) == 1


def test_re_freezing_is_how_a_deliberate_change_is_registered(tmp_path, monkeypatch):
    freeze(tmp_path)
    monkeypatch.setattr(
        llm_planner, "PLANNER_SYSTEM", llm_planner.PLANNER_SYSTEM + "\nOne more sentence."
    )
    assert main([*FAST, "--freeze", "--out-dir", str(tmp_path)]) == 0
    frozen = json.loads((tmp_path / "frozen_config.json").read_text(encoding="utf-8"))
    assert frozen["inputs"]["prompts"]["planner_system"].endswith("One more sentence.")


def test_no_frozen_file_means_no_guard_to_trip(tmp_path):
    """A first exploratory run has registered nothing and is not drift."""
    assert main([*FAST, "--out-dir", str(tmp_path)]) == 0
