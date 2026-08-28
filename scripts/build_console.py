"""Build the decision console -- a self-contained HTML view of one run.

    python -m scripts.build_console --out artifacts/console.html

The report answers "how much". This answers "why did it do that", which is the
question a metrics table cannot. It runs both arms over the same cohort, picks
six subjects that each demonstrate something different, and renders each one's
audit trail beside what the baseline ladder did to the *same* subject.

Nothing here computes an outcome. Every number and every line of every trace is
read back out of the audit log after the run, so the console cannot flatter the
pipeline that produced it.
"""

import argparse
import collections
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recoup.audit.log import AuditLog
from recoup.experiment.control import run_control_arm
from recoup.llm.client import LLMClient
from recoup.models.enums import Band
from recoup.orchestrate.runner import RunConfig, run_recoup_arm
from recoup.razorpay.config import load_dotenv

ZERO_RETRY = ("INSTRUMENT_INVALID", "MANDATE_REVOKED", "RISK_DECLINE")

EVIDENCE = Path("evidence")
"""The committed bundle. Acts 2 and 3 read the frozen numbers rather than the
numbers this run happens to produce, so the deck and the report cannot drift."""

CASE_BLURB = {
    "hero": (
        "A dead card",
        "The cause the whole product exists for. The baseline retries a card that "
        "cannot work; Recoup asks for a new one instead.",
    ),
    "rescheduled": (
        "Blocked by the contact window",
        "The agent wanted to message someone in the middle of the night. Its own "
        "policy said no. Watch what it does with the refusal.",
    ),
    "revoked": (
        "A revoked mandate",
        "Consent is gone. There is no clever intervention here, and the correct "
        "action is to stop -- which costs nothing and is easy to get wrong.",
    ),
    "risk": (
        "A risk decline",
        "The bank made a decision about the transaction. A retry would be arguing "
        "with it, so this one goes to a human.",
    ),
    "transient": (
        "Somebody else's outage",
        "The best failures to hold. Retry fast and quietly -- no message, because "
        "nothing is wrong on the customer's side.",
    ),
    "funds": (
        "A shortfall",
        "Money will arrive; the question is when. This is the cause the baseline "
        "ladder already handles reasonably well.",
    ),
}


def pick_cases(outcomes: dict[str, dict], traces: dict[str, list]) -> dict[str, str]:
    """One subject per demonstration, chosen by what its trace contains."""
    want: dict[str, str] = {}
    for sid, o in outcomes.items():
        stages = [r["stage"] for r in traces[sid]]
        cls, term = o["failure_class"], o["terminal"]
        key = None
        if cls == "INSTRUMENT_INVALID" and term == "recovered" and o["recovered_at_tier"] == 2:
            key = "hero"
        elif cls == "MANDATE_REVOKED":
            key = "revoked"
        elif cls == "RISK_DECLINE" and term == "manual_review":
            key = "risk"
        elif "contact_rescheduled" in stages and term == "recovered":
            key = "rescheduled"
        elif cls == "TRANSIENT_ISSUER" and term == "recovered":
            key = "transient"
        elif cls == "INSUFFICIENT_FUNDS" and term == "recovered":
            key = "funds"
        if key and key not in want:
            want[key] = sid
    return want


def totals_for(outcomes: dict[str, dict]) -> dict[str, Any]:
    values = list(outcomes.values())
    recovered = [o for o in values if o["terminal"] == "recovered"]
    churned = [o for o in values if o["terminal"] == "voluntary_churn"]
    charges = sum(o["charge_attempts"] for o in values)
    by_class: dict[str, int] = collections.defaultdict(int)
    for o in values:
        by_class[o["failure_class"]] += o["gross_recovered_paise"]
    return {
        "gross": sum(o["gross_recovered_paise"] for o in values),
        "cost": sum(o["cost_paise"] for o in values),
        "recovered": len(recovered),
        "denominator": len(values) - len(churned),
        "charges": charges,
        "per_recovery": charges / len(recovered) if recovered else 0.0,
        "wasted": sum(
            o["charge_attempts"]
            for o in values
            if o["failure_class"] in ZERO_RETRY and o["terminal"] != "recovered"
        ),
        "by_class": dict(by_class),
    }



TARGET_NET_LIFT = 0.15
"""The spec's net-recovery target. A grid cell clears it or it does not."""


def experiment_data(sweep: dict[str, Any], replication: dict[str, Any]) -> dict[str, Any]:
    """Reshape the published bundle for the deck. Reads, never recomputes.

    Every figure a slide shows comes straight out of ``sweep.json`` and
    ``replication.json``. A dashboard that derived its own totals could disagree
    with the report, and the disagreement would be silent -- it looks like a
    rounding difference until somebody checks.
    """
    bands: dict[str, Any] = {}
    for band, result in sweep["results"].items():
        bands[band] = {
            arm: {
                "gross": result[arm]["gross_recovered_paise"],
                "cost": result[arm]["total_cost_paise"],
                "net": result[arm]["net_recovered_paise"],
                "rate": result[arm]["recovery_rate"],
                "recovered": result[arm]["recovered"],
                "charges": result[arm]["charge_attempts"],
                "per_recovery": result[arm]["attempts_per_recovery"],
                "wasted": result[arm]["wasted_attempts"],
                "hours": result[arm]["mean_hours_to_recovery"],
                "per_subject_net": result[arm]["net_recovered_paise"] // result[arm]["cohort_size"],
                "per_subject_cost": result[arm]["total_cost_paise"] // result[arm]["cohort_size"],
            }
            for arm in ("control", "treatment")
        }

    mid = sweep["results"]["mid"]
    by_class = []
    for cause, treatment in mid["treatment"]["money_by_class"].items():
        control = mid["control"]["money_by_class"].get(cause, 0)
        delta = treatment - control
        by_class.append({
            "cause": cause,
            "control": control,
            "treatment": treatment,
            "delta": delta,
            # The sign travels with the datum. Red and green are the one pair a
            # colourblind reader cannot separate, so the fill colour is never
            # the only thing saying which way a number went.
            "sign": "+" if delta > 0 else "-" if delta < 0 else "",
        })
    by_class.sort(key=lambda r: -r["delta"])

    by_mechanism = [
        {
            "mechanism": mech,
            "control": mid["control"]["money_by_mechanism"].get(mech, 0),
            "treatment": value,
        }
        for mech, value in sorted(
            mid["treatment"]["money_by_mechanism"].items(), key=lambda kv: -kv[1]
        )
    ]

    grid = []
    for seed in replication["seeds"]:
        for band in ("low", "mid", "high"):
            arms = replication["sweeps"][str(seed)]["results"][band]
            control = arms["control"]["net_recovered_paise"]
            lift = (arms["treatment"]["net_recovered_paise"] - control) / control
            grid.append({
                "seed": seed,
                "band": band,
                "lift": lift,
                "clears": lift >= TARGET_NET_LIFT,
            })

    return {
        "bands": bands,
        "by_class": by_class,
        "by_mechanism": by_mechanism,
        "grid": grid,
        "seeds": list(replication["seeds"]),
        "target": TARGET_NET_LIFT,
    }


def collect(config: RunConfig, workdir: Path, client: LLMClient | None) -> dict[str, Any]:
    arms: dict[str, tuple[dict, dict]] = {}
    for arm, run, kwargs in (
        ("treatment", run_recoup_arm, {"llm_client": client}),
        ("control", run_control_arm, {}),
    ):
        db = workdir / f"console_{arm}.db"
        if db.exists():
            db.unlink()
        audit = AuditLog(db)
        result = run(config, audit, **kwargs)
        traces: dict[str, list] = collections.defaultdict(list)
        for record in audit.all():
            traces[record.subscription_id].append(
                {
                    "t": record.virtual_time.isoformat(),
                    "stage": record.stage,
                    "payload": record.payload,
                }
            )
        audit.close()
        db.unlink(missing_ok=True)
        arms[arm] = (
            {o.subscription_id: o.model_dump(mode="json") for o in result.outcomes},
            dict(traces),
        )
        print(f"  {arm}: {len(result.outcomes)} subjects", file=sys.stderr)

    picked = pick_cases(*arms["treatment"])
    cases = {}
    for key, sid in picked.items():
        title, blurb = CASE_BLURB[key]
        cases[key] = {
            "id": sid,
            "title": title,
            "blurb": blurb,
            **{
                arm: {"outcome": outs[sid], "trace": trs[sid]}
                for arm, (outs, trs) in arms.items()
            },
        }
    return {
        "meta": {
            "seed": config.seed,
            "cohort": config.cohort_size,
            "band": config.band.value,
            "generated": datetime.now(timezone.utc).strftime("%d %b %Y"),
        },
        "cases": cases,
        "totals": {arm: totals_for(outs) for arm, (outs, _) in arms.items()},
        "experiment": experiment_data(
            json.loads((EVIDENCE / "sweep.json").read_text(encoding="utf-8")),
            json.loads((EVIDENCE / "replication.json").read_text(encoding="utf-8")),
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--cohort-size", type=int, default=2000)
    parser.add_argument("--out", type=Path, default=Path("artifacts/console.html"))
    args = parser.parse_args(argv)

    for key, value in load_dotenv().items():
        os.environ.setdefault(key, value)

    config = RunConfig(
        run_id="console",
        seed=args.seed,
        band=Band.MID,
        cohort_size=args.cohort_size,
        start_at=datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    client = LLMClient(cache_path=args.out.parent / "llm_cache.json")

    print("running both arms...", file=sys.stderr)
    data = collect(config, args.out.parent, client)

    template = (Path(__file__).parent / "console_template.html").read_text(encoding="utf-8")
    args.out.write_text(
        template.replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":"))),
        encoding="utf-8",
    )
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
