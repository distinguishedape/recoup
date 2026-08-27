"""The freeze must cover what actually determines the numbers.

`config_hash` covers seed, band, cohort size and start time. None of those is
what moved the published figures: editing the planner prompt re-drew every plan
and shifted dead-card money by Rs 45,475 while the hash stayed at
`7aa7962cac907ba0` and `--verify-frozen` reported "configuration verified
unchanged" (D60, D62). A pre-registration that cannot see the inputs it is
registering is decoration.
"""

from datetime import datetime, timezone

import pytest

from recoup.classify import llm_resolver
from recoup.execute import executor, probabilities
from recoup.experiment.harness import ConfigurationDrift, freeze_config, verify_frozen_config
from recoup.experiment.inputs import inputs_hash, measurement_inputs, what_changed
from recoup.ingest import cohort
from recoup.models.enums import Band
from recoup.orchestrate.runner import RunConfig
from recoup.plan import budgets, fallback, llm_planner

START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def config() -> RunConfig:
    return RunConfig(run_id="t", seed=3, band=Band.MID, cohort_size=10, start_at=START)


def test_the_planner_prompt_is_part_of_the_measurement(monkeypatch):
    """The defect this whole module exists for."""
    before = inputs_hash()
    monkeypatch.setattr(
        llm_planner, "PLANNER_SYSTEM", llm_planner.PLANNER_SYSTEM + "\nOne more sentence."
    )
    assert inputs_hash() != before


def test_the_classifier_prompt_is_part_of_the_measurement(monkeypatch):
    before = inputs_hash()
    monkeypatch.setattr(
        llm_resolver, "RESOLVER_SYSTEM", llm_resolver.RESOLVER_SYSTEM + "\nAnd another."
    )
    assert inputs_hash() != before


def test_the_shape_of_a_user_prompt_is_part_of_the_measurement(monkeypatch):
    """Not only the system prompts. The per-event prompt builders are code, and
    a changed field ordering or a new line is as much a re-ask as an edited
    system prompt, so the builders are rendered against a fixed probe event."""
    before = inputs_hash()
    monkeypatch.setattr(
        llm_resolver, "build_user_prompt", lambda event: "completely different prompt"
    )
    assert inputs_hash() != before


def test_the_model_name_is_part_of_the_measurement():
    assert inputs_hash(model="model-a") != inputs_hash(model="model-b")


@pytest.mark.parametrize("target", ["probabilities", "budgets", "costs", "cohort", "schedule"])
def test_every_constant_that_moves_money_is_covered(target, monkeypatch):
    before = inputs_hash()
    if target == "probabilities":
        monkeypatch.setattr(probabilities, "RETRY_DECAY", probabilities.RETRY_DECAY + 0.05)
    elif target == "budgets":
        widened = dict(budgets.BUDGETS)
        first = next(iter(widened))
        widened[first] = budgets.Budget(charge_retries=9, contacts=9)
        monkeypatch.setattr(budgets, "BUDGETS", widened)
    elif target == "costs":
        monkeypatch.setattr(executor, "CHARGE_ATTEMPT_COST_PAISE", 999)
    elif target == "cohort":
        monkeypatch.setattr(cohort, "PLAN_AMOUNTS_PAISE", (12345,))
    elif target == "schedule":
        monkeypatch.setattr(fallback, "FUNDS_RETRY_DELAYS_HOURS", (1, 2, 3))
    assert inputs_hash() != before, f"{target} can change the numbers without changing the hash"


def test_the_hash_is_stable_across_calls():
    assert inputs_hash() == inputs_hash()


def test_what_changed_names_the_section_that_moved(monkeypatch):
    """A hash that only says 'something moved' sends a reader looking through
    every constant in the project. It has to say which one."""
    registered = measurement_inputs()
    monkeypatch.setattr(
        llm_planner, "PLANNER_SYSTEM", llm_planner.PLANNER_SYSTEM + "\nOne more sentence."
    )
    changed = what_changed(registered, measurement_inputs())
    assert changed == ["prompts.planner_system"]


def test_what_changed_is_empty_when_nothing_moved():
    assert what_changed(measurement_inputs(), measurement_inputs()) == []


def test_freezing_records_the_inputs_not_only_their_hash(tmp_path):
    """The registered inputs are the pre-registration. Storing only a digest
    means a later reader can be told a number moved but never what it was."""
    import json

    path = tmp_path / "frozen_config.json"
    freeze_config(config(), path)
    frozen = json.loads(path.read_text(encoding="utf-8"))
    assert frozen["inputs_hash"] == inputs_hash()
    assert frozen["inputs"]["prompts"]["planner_system"] == llm_planner.PLANNER_SYSTEM
    assert frozen["config_hash"], "the existing run identity must survive unchanged"


def test_verifying_refuses_a_changed_prompt_and_says_which_one(tmp_path, monkeypatch):
    path = tmp_path / "frozen_config.json"
    freeze_config(config(), path)
    monkeypatch.setattr(
        llm_planner, "PLANNER_SYSTEM", llm_planner.PLANNER_SYSTEM + "\nOne more sentence."
    )
    with pytest.raises(ConfigurationDrift) as excinfo:
        verify_frozen_config(config(), path)
    assert "prompts.planner_system" in str(excinfo.value)


def test_verifying_passes_when_the_inputs_are_untouched(tmp_path):
    path = tmp_path / "frozen_config.json"
    freeze_config(config(), path)
    assert verify_frozen_config(config(), path)


def test_an_old_frozen_file_without_inputs_is_not_treated_as_agreement(tmp_path):
    """A frozen file written before inputs were registered cannot vouch for
    them. Silently passing would be the same false reassurance this fixes."""
    import json

    path = tmp_path / "frozen_config.json"
    freeze_config(config(), path)
    frozen = json.loads(path.read_text(encoding="utf-8"))
    del frozen["inputs_hash"]
    del frozen["inputs"]
    path.write_text(json.dumps(frozen), encoding="utf-8")
    with pytest.raises(ConfigurationDrift) as excinfo:
        verify_frozen_config(config(), path)
    assert "registered no measurement inputs" in str(excinfo.value)
