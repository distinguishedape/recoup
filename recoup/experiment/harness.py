"""Runs both arms over one cohort and freezes what was measured.

The arms are separated only by their strategy. Same cohort, same seed,
same per-subject random streams, same probability band, same cost
constants. If the treatment arm looks better, the difference is the
intervention, because there is nothing else left for it to be.

``freeze_config`` exists so the held-out run means something. A
configuration hash written before the held-out slice runs, and verified
after, is the difference between "we tuned until it worked" and "we
committed and then looked".
"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from recoup.audit.log import AuditLog
from recoup.experiment.control import run_control_arm
from recoup.experiment.inputs import inputs_hash, measurement_inputs, what_changed
from recoup.llm.client import LLMClient
from recoup.orchestrate.runner import RunConfig, config_hash, run_recoup_arm
from recoup.report.metrics import ArmMetrics, Comparison, compare, compute_metrics


class ConfigurationDrift(RuntimeError):
    """The configuration changed after it was frozen."""


class ExperimentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    config: RunConfig
    control: ArmMetrics
    treatment: ArmMetrics
    comparison: Comparison
    control_audit_path: str
    treatment_audit_path: str


def run_paired_experiment(
    config: RunConfig,
    audit_dir: Path,
    llm_client: LLMClient | None = None,
) -> ExperimentResult:
    audit_dir = Path(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)

    control_path = audit_dir / "control.db"
    treatment_path = audit_dir / "treatment.db"

    # An audit log is append-only, which is right for a log and wrong for the
    # scratch space of a repeatable measurement: re-running into the same
    # directory silently stacks runs on top of each other, and the exported CSV
    # then describes six runs at once. An evidence bundle was published that way.
    # Metrics come from the in-memory result so they stayed correct; the exported
    # audit did not. A measurement starts from nothing.
    for path in (control_path, treatment_path,
                 audit_dir / "control.jsonl", audit_dir / "treatment.jsonl"):
        path.unlink(missing_ok=True)

    control_audit = AuditLog(control_path, audit_dir / "control.jsonl")
    try:
        control_result = run_control_arm(config, control_audit)
    finally:
        control_audit.close()

    treatment_audit = AuditLog(treatment_path, audit_dir / "treatment.jsonl")
    try:
        treatment_result = run_recoup_arm(config, treatment_audit, llm_client)
    finally:
        treatment_audit.close()

    control_metrics = compute_metrics(control_result, "control")
    treatment_metrics = compute_metrics(treatment_result, "treatment")

    return ExperimentResult(
        config=config,
        control=control_metrics,
        treatment=treatment_metrics,
        comparison=compare(control_metrics, treatment_metrics),
        control_audit_path=str(control_path),
        treatment_audit_path=str(treatment_path),
    )


def freeze_config(config: RunConfig, path: Path, model: str | None = None) -> str:
    """Register the run *and* the inputs that decide its numbers.

    ``config_hash`` is kept exactly as it was, so a published run keeps its
    identity and stays comparable with earlier bundles. What is added beside it
    is the registration that was missing: the prompts, probabilities, budgets,
    costs, cohort distribution and schedule, stored in full rather than as a
    digest so that drift can name what moved instead of only that something did.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = config_hash(config)
    path.write_text(
        json.dumps(
            {
                "config_hash": digest,
                "seed": config.seed,
                "band": config.band.value,
                "cohort_size": config.cohort_size,
                "start_at": config.start_at.isoformat(),
                "inputs_hash": inputs_hash(model),
                "inputs": measurement_inputs(model),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return digest


def verify_frozen_config(config: RunConfig, path: Path, model: str | None = None) -> str:
    path = Path(path)
    if not path.exists():
        raise ConfigurationDrift(
            f"no frozen configuration at {path}; freeze before running the held-out slice"
        )
    frozen = json.loads(path.read_text(encoding="utf-8"))
    digest = config_hash(config)
    if frozen.get("config_hash") != digest:
        raise ConfigurationDrift(
            f"configuration changed after freezing: frozen {frozen.get('config_hash')!r}, "
            f"current {digest!r}"
        )
    registered = frozen.get("inputs")
    if registered is None:
        # A file written before the inputs were registered cannot vouch for
        # them. Passing it silently would be the same false reassurance that
        # let an edited prompt through under an unchanged hash.
        raise ConfigurationDrift(
            f"the frozen configuration at {path} registered no measurement inputs, so it "
            "cannot say whether the prompts, probabilities, budgets or schedule changed. "
            "Re-freeze to register them."
        )
    changed = what_changed(registered, measurement_inputs(model))
    if changed:
        raise ConfigurationDrift(
            "the measurement inputs changed after freezing: "
            + ", ".join(changed)
            + ". These decide the numbers as surely as the seed does -- a changed prompt "
            "re-asks every plan. Re-freeze deliberately if the change is intended."
        )
    return digest
