"""The report and the evidence bundle.

The report is written for someone who intends to disbelieve it. It leads
with money because that is what was asked for, prints the baseline it
compared against, prints every assumption it depends on, and prints the
sweep's own verdict on each finding rather than a verdict chosen here.

Rupee formatting uses integer arithmetic and Indian digit grouping
(``10,00,000``, not ``1,000,000``). Floats are never used for money
anywhere in Recoup, and a report that quietly reintroduced them would
undermine the one number the whole exercise exists to produce.
"""

import json
from pathlib import Path

from recoup.audit.log import AuditLog
from recoup.execute.executor import CHANNEL_COST_PAISE, CHARGE_ATTEMPT_COST_PAISE
from recoup.execute.probabilities import BANDS
from recoup.experiment.control import CONTROL_RETRY_DELAYS_HOURS
from recoup.experiment.sweep import SweepResult
from recoup.ingest.cohort import CLASS_WEIGHTS, PLAN_AMOUNTS_PAISE
from recoup.models.enums import Band, FailureClass
from recoup.orchestrate.runner import config_hash
from recoup.plan.budgets import BUDGETS
from recoup.report.metrics import ArmMetrics

RUPEE = "₹"


def _group_indian(digits: str) -> str:
    """Indian digit grouping: last three, then pairs (10,00,000)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    if head:
        pairs.insert(0, head)
    return ",".join(pairs) + "," + tail


def format_rupees(paise: int) -> str:
    sign = "-" if paise < 0 else ""
    rupees, remainder = divmod(abs(int(paise)), 100)
    return f"{sign}{RUPEE}{_group_indian(str(rupees))}.{remainder:02d}"


def _arm_row(metrics: ArmMetrics) -> str:
    label = "Baseline ladder" if metrics.arm == "control" else "Recoup"
    return (
        f"| {label} "
        f"| {format_rupees(metrics.gross_recovered_paise)} "
        f"| {format_rupees(metrics.total_cost_paise)} "
        f"| {format_rupees(metrics.net_recovered_paise)} "
        f"| {metrics.recovery_rate * 100:.1f}% "
        f"| {metrics.attempts_per_recovery:.2f} "
        f"| {metrics.wasted_attempts} "
        f"| {metrics.mean_hours_to_recovery:.1f}h |"
    )


_ARM_TABLE_HEADER = (
    "| Arm | Gross recovered | Cost | Net recovered | Recovery rate | "
    "Attempts / recovery | Wasted attempts | Mean time to recovery |\n"
    "|---|---|---|---|---|---|---|---|"
)


def _format_value(value: float, unit: str) -> str:
    if unit == "paise":
        return format_rupees(int(value))
    if unit == "percentage points":
        return f"{value:+.1f}pp"
    return f"{value:+.2f}"


def render_report(sweep: SweepResult) -> str:
    mid = sweep.results[Band.MID.value]
    lines: list[str] = []

    lines.append("# Recoup - measured recovery against the baseline retry ladder")
    lines.append("")
    lines.append(
        f"Cohort of {sweep.config.cohort_size} failed subscription charges, seed "
        f"{sweep.config.seed}, configuration hash `{config_hash(sweep.config)}`."
    )
    lines.append("")

    lines.append("## Headline (Mid band)")
    lines.append("")
    lines.append(_ARM_TABLE_HEADER)
    lines.append(_arm_row(mid.control))
    lines.append(_arm_row(mid.treatment))
    lines.append("")
    lines.append(
        f"Gross lift {format_rupees(mid.comparison.gross_lift_paise)} - "
        f"net lift {format_rupees(mid.comparison.net_lift_paise)} - "
        f"recovery rate {mid.comparison.recovery_rate_lift_pp:+.1f}pp - "
        f"wasted attempts avoided {mid.comparison.wasted_attempts_avoided}."
    )
    lines.append("")

    lines.append("## Findings across the sensitivity sweep")
    lines.append("")
    lines.append("| Finding | Low | Mid | High | Verdict | Note |")
    lines.append("|---|---|---|---|---|---|")
    for finding in sweep.findings:
        verdict = "**survives**" if finding.survives else "does not survive"
        lines.append(
            f"| {finding.name} "
            f"| {_format_value(finding.low, finding.unit)} "
            f"| {_format_value(finding.mid, finding.unit)} "
            f"| {_format_value(finding.high, finding.unit)} "
            f"| {verdict} | {finding.note} |"
        )
    lines.append("")
    lines.append(
        "A finding is reported as surviving only if it points the right way at **all "
        "three** bands. A lift that appears only at the High band is reported as not "
        "surviving, however large it is."
    )
    lines.append("")

    lines.append("## Per-band detail")
    lines.append("")
    for band in Band:
        experiment = sweep.results[band.value]
        lines.append(f"### {band.value.capitalize()} band")
        lines.append("")
        lines.append(_ARM_TABLE_HEADER)
        lines.append(_arm_row(experiment.control))
        lines.append(_arm_row(experiment.treatment))
        lines.append("")

    lines.append("## The baseline this was compared against")
    lines.append("")
    delays = ", ".join(f"T+{hours // 24}" for hours in CONTROL_RETRY_DELAYS_HOURS)
    lines.append(
        f"Four total charge attempts - the initial failure plus three retries at {delays} "
        "days - context-blind, with no intervention beyond Razorpay's own failure email, "
        "terminating in `halted`. Day-stepping is used rather than the test-mode "
        "10-minute/1-hour ladder because the latter reads as test acceleration rather than "
        "production behaviour, and Razorpay's own documentation is inconsistent between "
        "the two."
    )
    lines.append("")

    lines.append("## Assumptions")
    lines.append("")
    lines.append(
        "Recovery outcomes are **simulated**. Razorpay test mode offers only "
        "Charge-as-Success and Charge-as-Failure from the Dashboard; it cannot inject a "
        "specific decline reason, and it exposes no manual-retry API for domestic cards. "
        "Every recovery probability below is a stated assumption drawn from published "
        "dunning benchmarks, not a measurement. The sweep exists because of this."
    )
    lines.append("")
    lines.append("**Cohort class distribution**")
    lines.append("")
    lines.append("| Class | Share |")
    lines.append("|---|---|")
    for failure_class, weight in CLASS_WEIGHTS.items():
        lines.append(f"| `{failure_class.value}` | {weight * 100:.0f}% |")
    lines.append("")
    lines.append(
        "**Plan amounts** drawn uniformly from "
        + ", ".join(format_rupees(amount) for amount in PLAN_AMOUNTS_PAISE)
        + "."
    )
    lines.append("")
    lines.append(
        f"**Attempt cost**: {format_rupees(CHARGE_ATTEMPT_COST_PAISE)} per charge attempt, "
        + ", ".join(
            f"{format_rupees(cost)} per {channel}"
            for channel, cost in CHANNEL_COST_PAISE.items()
        )
        + "."
    )
    lines.append("")
    lines.append("**Recovery probability bands**")
    lines.append("")
    lines.append("| Class | Low | Mid | High |")
    lines.append("|---|---|---|---|")
    for failure_class in FailureClass:
        row = " | ".join(
            f"{BANDS[band].retry_success[failure_class] * 100:.1f}%" for band in Band
        )
        lines.append(f"| `{failure_class.value}` | {row} |")
    lines.append(
        "| instrument-update conversion | "
        + " | ".join(f"{BANDS[band].update_request_conversion * 100:.0f}%" for band in Band)
        + " |"
    )
    lines.append("")
    lines.append("**Per-class attempt budgets**")
    lines.append("")
    lines.append("| Class | Charge retries | Contacts |")
    lines.append("|---|---|---|")
    for failure_class, budget in BUDGETS.items():
        lines.append(
            f"| `{failure_class.value}` | {budget.charge_retries} | {budget.contacts} |"
        )
    lines.append("")

    lines.append("## Definitions")
    lines.append("")
    lines.append(
        "- **Recovery rate** excludes voluntary churn from the denominator. A customer who "
        "revoked their mandate did not fail to be recovered; they left."
    )
    lines.append(
        "- **Wasted attempts** are charge attempts spent on a cause a retry cannot fix "
        "(`INSTRUMENT_INVALID`, `MANDATE_REVOKED`, `RISK_DECLINE`) on a subject that never "
        "recovered. A charge after a successful instrument update is not counted as waste."
    )
    lines.append(
        "- **Net recovered** is gross recovered minus every rupee spent across *all* "
        "subjects in the arm, recovered or not."
    )
    lines.append("")
    return "\n".join(lines)


def write_bundle(sweep: SweepResult, out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    report_path = out_dir / "report.md"
    report_path.write_text(render_report(sweep), encoding="utf-8")
    written.append(report_path)

    sweep_path = out_dir / "sweep.json"
    sweep_path.write_text(sweep.model_dump_json(indent=2), encoding="utf-8")
    written.append(sweep_path)

    for band_name, experiment in sweep.results.items():
        for arm, db_path in (
            ("control", experiment.control_audit_path),
            ("treatment", experiment.treatment_audit_path),
        ):
            log = AuditLog(Path(db_path))
            try:
                csv_path = out_dir / f"audit_{band_name}_{arm}.csv"
                log.export_csv(csv_path)
                written.append(csv_path)
            finally:
                log.close()

    return written
