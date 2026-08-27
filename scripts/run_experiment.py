"""Run the whole experiment and write the evidence bundle.

    python -m scripts.run_experiment --cohort-size 200 --seed 3 --out-dir artifacts

Add --freeze before the held-out run to record the configuration hash, and
--verify-frozen on the held-out run itself; the second refuses to proceed if
anything about the configuration changed in between.
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from recoup.experiment.harness import freeze_config, verify_frozen_config
from recoup.experiment.replication import run_replication
from recoup.experiment.sweep import run_sweep
from recoup.llm.client import LLMClient
from recoup.models.enums import Band
from recoup.orchestrate.runner import RunConfig
from recoup.razorpay.config import load_dotenv
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
    parser.add_argument("--allow-fallback", action="store_true",
                        help="publish even if some prompts never reached a model")
    parser.add_argument("--replicate", default="",
                        help="comma-separated extra seeds; a finding replicates only if it "
                             "survives the band sweep in every cohort")
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

    # Without this the runner sees no API key, every cache miss falls back to
    # UNCLASSIFIED, and the "with model" arm quietly becomes a partial one. An
    # entire evidence bundle was published that way: 930 of 1542 ambiguous
    # classifications never reached a model, and nothing said so.
    for key, value in load_dotenv().items():
        os.environ.setdefault(key, value)

    client = None if args.no_llm else LLMClient(args.out_dir / "llm_cache.json")
    sweep = run_sweep(config, args.out_dir / "audit", client)

    replication = None
    if args.replicate:
        seeds = [args.seed] + [int(s) for s in args.replicate.split(",") if s.strip()]
        replication = run_replication(config, seeds, args.out_dir / "replication", client)

    if client is not None and client.unserved and not args.allow_fallback:
        print(
            f"refusing to write the bundle: {client.unserved} prompts reached neither "
            "the cache nor a model, so those subjects were classified by fallback "
            "rather than by the model this run claims to measure.\n"
            "Configure a provider key to fill the cache, or pass --allow-fallback "
            "to publish a deliberately degraded run.",
            file=sys.stderr,
        )
        return 1

    written = write_bundle(sweep, args.out_dir, replication)

    for finding in sweep.findings:
        verdict = "survives" if finding.survives else "does not survive"
        print(f"{finding.name}: {verdict} "
              f"(low {finding.low}, mid {finding.mid}, high {finding.high})")

    if replication is not None:
        print()
        print(f"replication across {len(replication.seeds)} cohorts:")
        for finding in replication.findings:
            verdict = "REPLICATES" if finding.replicates else "does not replicate"
            print(f"  {finding.name}: {verdict} "
                  f"(survived in {len(finding.survived_in)}/{len(replication.seeds)})")
    print()
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
