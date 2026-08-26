"""The Low/Mid/High sensitivity sweep.

Recovery outcomes in this experiment are simulated, so a single point
estimate would be an assertion about a number nobody measured. Running the
whole paired experiment three times, once at each band, converts that into
something falsifiable: either the intervention wins across the plausible
range or it does not.

The survival rule is deliberately strict. A lift that appears only at the
optimistic band is a lift that depends on the assumption rather than on
the product, and this module reports it as not surviving no matter how
large it is. ``Finding.survives`` is computed here and printed verbatim by
the renderer, so there is no path by which a favourable-looking number
reaches the report with a claim the sweep did not support.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from recoup.experiment.harness import ExperimentResult, run_paired_experiment
from recoup.llm.client import LLMClient
from recoup.models.enums import Band
from recoup.orchestrate.runner import RunConfig
from recoup.report.metrics import Comparison


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    unit: str
    low: float
    mid: float
    high: float
    higher_is_better: bool
    survives: bool
    note: str


class SweepResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    config: RunConfig
    results: dict[str, ExperimentResult]
    findings: list[Finding]


_HEADLINES: tuple[tuple[str, str, str, bool], ...] = (
    ("gross_recovered", "paise", "gross_lift_paise", True),
    ("net_recovered", "paise", "net_lift_paise", True),
    ("recovery_rate", "percentage points", "recovery_rate_lift_pp", True),
    ("attempts_per_recovery", "attempts", "attempts_per_recovery_delta", False),
    ("wasted_attempts", "attempts avoided", "wasted_attempts_avoided", True),
)


def _points_the_right_way(value: float, higher_is_better: bool) -> bool:
    return value > 0 if higher_is_better else value < 0


def build_findings(by_band: dict[Band, Comparison]) -> list[Finding]:
    findings: list[Finding] = []
    for name, unit, attribute, higher_is_better in _HEADLINES:
        low = float(getattr(by_band[Band.LOW], attribute))
        mid = float(getattr(by_band[Band.MID], attribute))
        high = float(getattr(by_band[Band.HIGH], attribute))

        good = [_points_the_right_way(v, higher_is_better) for v in (low, mid, high)]
        survives = all(good)

        if survives:
            note = "holds at every band"
        elif good[2] and not good[0] and not good[1]:
            note = (
                "appears only at the High band, so it is reported as not surviving: "
                "it depends on the optimistic assumption rather than on the intervention"
            )
        elif not any(good):
            note = "does not hold at any band"
        else:
            note = "holds at some bands but not all, so it is reported as not surviving"

        findings.append(
            Finding(
                name=name,
                unit=unit,
                low=low,
                mid=mid,
                high=high,
                higher_is_better=higher_is_better,
                survives=survives,
                note=note,
            )
        )
    return findings


def run_sweep(
    config: RunConfig,
    audit_dir: Path,
    llm_client: LLMClient | None = None,
) -> SweepResult:
    audit_dir = Path(audit_dir)
    results: dict[str, ExperimentResult] = {}
    by_band: dict[Band, Comparison] = {}

    for band in Band:
        band_config = config.model_copy(update={"band": band})
        experiment = run_paired_experiment(band_config, audit_dir / band.value, llm_client)
        results[band.value] = experiment
        by_band[band] = experiment.comparison

    return SweepResult(config=config, results=results, findings=build_findings(by_band))
