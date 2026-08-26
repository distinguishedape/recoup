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


def freeze_config(config: RunConfig, path: Path) -> str:
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
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return digest


def verify_frozen_config(config: RunConfig, path: Path) -> str:
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
    return digest
