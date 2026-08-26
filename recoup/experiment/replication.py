"""Running the sweep across several cohorts, and reporting what holds up.

A single cohort answers "did it win here", which is not the question. On this
project the registered cohort showed every finding surviving, including money,
and the held-out cohort did not reproduce the money result at all. Only running
more cohorts settled it.

So the survival rule generalises: a finding **replicates** when it survives the
Low/Mid/High sweep in *every* cohort, not merely on average across them. A
result that holds in three cohorts out of four is reported as not replicating,
for the same reason a lift that appears only at the optimistic band is reported
as not surviving -- averaging is how a result that depends on luck gets
laundered into one that looks robust.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from recoup.experiment.sweep import SweepResult, run_sweep
from recoup.llm.client import LLMClient
from recoup.orchestrate.runner import RunConfig


class ReplicatedFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    unit: str
    higher_is_better: bool
    per_seed: dict[int, float]
    """Mid-band value in each cohort, so a reader can see the spread rather
    than a summary of it."""
    survived_in: list[int]
    replicates: bool
    note: str

    @property
    def mean(self) -> float:
        values = list(self.per_seed.values())
        return sum(values) / len(values) if values else 0.0


class ReplicationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    seeds: list[int]
    cohort_size: int
    sweeps: dict[int, SweepResult]
    findings: list[ReplicatedFinding]


def build_replicated_findings(sweeps: dict[int, SweepResult]) -> list[ReplicatedFinding]:
    seeds = sorted(sweeps)
    if not seeds:
        return []

    by_name: dict[str, list[tuple[int, object]]] = {}
    for seed in seeds:
        for finding in sweeps[seed].findings:
            by_name.setdefault(finding.name, []).append((seed, finding))

    replicated: list[ReplicatedFinding] = []
    for name, pairs in by_name.items():
        template = pairs[0][1]
        per_seed = {seed: finding.mid for seed, finding in pairs}
        survived_in = [seed for seed, finding in pairs if finding.survives]
        replicates = len(survived_in) == len(pairs)

        if replicates:
            note = f"survives the band sweep in all {len(pairs)} cohorts"
        elif not survived_in:
            note = "survives in no cohort"
        else:
            missing = [s for s, _ in pairs if s not in survived_in]
            note = (
                f"survives in {len(survived_in)} of {len(pairs)} cohorts "
                f"(not in seed {', '.join(str(m) for m in missing)}), "
                "so it is reported as not replicating"
            )

        replicated.append(
            ReplicatedFinding(
                name=name,
                unit=template.unit,
                higher_is_better=template.higher_is_better,
                per_seed=per_seed,
                survived_in=survived_in,
                replicates=replicates,
                note=note,
            )
        )
    return replicated


def run_replication(
    config: RunConfig,
    seeds: list[int],
    audit_dir: Path,
    llm_client: LLMClient | None = None,
) -> ReplicationResult:
    audit_dir = Path(audit_dir)
    sweeps: dict[int, SweepResult] = {}
    for seed in seeds:
        sweeps[seed] = run_sweep(
            config.model_copy(update={"seed": seed}), audit_dir / f"seed_{seed}", llm_client
        )
    return ReplicationResult(
        seeds=list(seeds),
        cohort_size=config.cohort_size,
        sweeps=sweeps,
        findings=build_replicated_findings(sweeps),
    )
