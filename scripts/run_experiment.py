"""Run the whole experiment and write the evidence bundle.

    python -m scripts.run_experiment --cohort-size 200 --seed 3 --out-dir artifacts

Add --freeze before the held-out run to record the configuration hash, and
--verify-frozen on the held-out run itself; the second refuses to proceed if
anything about the configuration changed in between.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from recoup.experiment.harness import freeze_config, verify_frozen_config
from recoup.experiment.sweep import run_sweep
from recoup.llm.client import LLMClient
from recoup.models.enums import Band
from recoup.orchestrate.runner import RunConfig
from recoup.report.render import write_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--cohort-size", type=int, default=200)
    parser.add_argument("--run-id", default="recoup")
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--freeze", action="store_true", help="write the configuration hash")
    parser.add_argument("--verify-frozen", action="store_true",
                        help="refuse to run if the config changed")
    parser.add_argument("--no-llm", action="store_true",
                        help="run with the deterministic planner only")
    args = parser.parse_args(argv)

    config = RunConfig(
        run_id=args.run_id,
        seed=args.seed,
        band=Band.MID,
        cohort_size=args.cohort_size,
        start_at=datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc),
    )

    frozen_path = args.out_dir / "frozen_config.json"
    if args.freeze:
        print(f"frozen configuration hash: {freeze_config(config, frozen_path)}")
    if args.verify_frozen:
        print(f"configuration verified unchanged: {verify_frozen_config(config, frozen_path)}")

    client = None if args.no_llm else LLMClient(args.out_dir / "llm_cache.json")
    sweep = run_sweep(config, args.out_dir / "audit", client)
    written = write_bundle(sweep, args.out_dir)

    for finding in sweep.findings:
        verdict = "survives" if finding.survives else "does not survive"
        print(f"{finding.name}: {verdict} "
              f"(low {finding.low}, mid {finding.mid}, high {finding.high})")
    print()
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
