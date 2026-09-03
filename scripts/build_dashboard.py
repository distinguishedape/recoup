"""Build the evidence dashboard -- one static page showing a real test run.

    python -m scripts.build_dashboard --run tests/classify tests/policy
    python -m scripts.build_dashboard --run            # the whole suite, ~120s
    python -m scripts.build_dashboard                  # reuse the last junit.xml

The point is that the test panel is not a screenshot. Every status on the page
comes from the JUnit XML pytest itself wrote, so the only way to make the page
say a test passes is for that test to pass. A run that covered part of the suite
says so: the tests outside it are marked "not in this run" rather than shown
green, because a page that quietly implies coverage it does not have is worse
than no page.

The money figures are read from the committed bundle rather than recomputed, so
this script cannot flatter the experiment -- same contract as build_console.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

EVIDENCE = Path("evidence")
BANDS = ("low", "mid", "high")

def bands_from_sweep(sweep: dict[str, Any]) -> dict[str, Any]:
    """Lift the arm metrics the page charts, and nothing else."""
    out: dict[str, Any] = {}
    for band in BANDS:
        cell = sweep["results"][band]
        arms: dict[str, Any] = {}
        for arm in ("control", "treatment"):
            a = cell[arm]
            arms[arm] = {
                "recovered": a["recovered"],
                "rate": a["recovery_rate"] * 100,
                "gross": a["gross_recovered_paise"],
                "cost": a["total_cost_paise"],
                "net": a["net_recovered_paise"],
                "attempts": a["charge_attempts"],
                "wasted": a["wasted_attempts"],
                "apr": a["attempts_per_recovery"],
            }
        arms["treatment"]["mech"] = cell["treatment"]["money_by_mechanism"]
        out[band] = arms
    return out


ABLATION_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*\+([\d.]+)%\s*\|\s*\+([\d.]+)%\s*\|", re.M)


def ablation_from_report(text: str) -> list[dict[str, Any]]:
    """Parse the committed ablation table. Loud on drift rather than silently empty."""
    rows = [
        {"seed": int(seed), "shipped": float(shipped), "matched": float(matched)}
        for seed, shipped, matched in ABLATION_ROW.findall(text)
    ]
    if not rows:
        raise SystemExit(
            "no cohort rows found in evidence/schedule-ablation.md -- the table format "
            "changed, so the dashboard would publish an empty chart. Fix ABLATION_ROW."
        )
    return rows


def collect_inventory() -> list[tuple[str, str, str]]:
    """Every test the suite defines, whether or not this run touched it.

    Without this the page could only show what ran, which is how a subset run
    ends up looking like a full one.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-2000:] + proc.stderr[-2000:])
        raise SystemExit("pytest --collect-only failed; cannot build a truthful inventory")

    items = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if "::" not in line or not line.startswith("tests/"):
            continue
        path, _, name = line.partition("::")
        parts = path.split("/")
        pkg = parts[1] if len(parts) > 2 else "root"
        items.append((pkg, parts[-1], name))
    if not items:
        raise SystemExit("pytest --collect-only returned no tests")
    return items


def status_of(case: ElementTree.Element) -> str:
    """JUnit reports an xfail as a skip with a type; the two are not the same thing."""
    if case.find("failure") is not None:
        return "failed"
    if case.find("error") is not None:
        return "error"
    skipped = case.find("skipped")
    if skipped is not None:
        return "xfailed" if "xfail" in (skipped.get("type") or "") else "skipped"
    return "passed"


def results_from_junit(path: Path) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    root = ElementTree.parse(path).getroot()
    suite = root.find("testsuite") if root.tag == "testsuites" else root
    if suite is None:
        raise SystemExit(f"{path} has no <testsuite> element")

    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for case in suite.iter("testcase"):
        module = (case.get("classname") or "").split(".")
        file = module[-1] + ".py" if module else ""
        seen[(file, case.get("name") or "")] = {
            "status": status_of(case),
            "seconds": float(case.get("time") or 0.0),
        }

    stamp = suite.get("timestamp") or ""
    try:
        at = datetime.fromisoformat(stamp).astimezone().strftime("%d %b %Y, %H:%M %Z")
    except ValueError:
        at = stamp or "unknown"

    return seen, {
        "seconds": float(suite.get("time") or 0.0),
        "at": at,
        "failed": int(suite.get("failures") or 0),
        "errors": int(suite.get("errors") or 0),
    }


def build_suite(inventory, seen, meta, command, source) -> dict[str, Any]:
    packages: dict[str, dict[str, list[dict[str, Any]]]] = {}
    tally = {"passed": 0, "failed": 0, "error": 0, "xfailed": 0, "skipped": 0, "notrun": 0}

    for pkg, file, name in inventory:
        hit = seen.get((file, name))
        entry = hit or {"status": "notrun", "seconds": 0.0}
        tally[entry["status"]] += 1
        packages.setdefault(pkg, {}).setdefault(file, []).append({"name": name, **entry})

    ran = len(inventory) - tally["notrun"]
    return {
        "total": len(inventory),
        "run": {
            "command": command,
            "source": source,
            "count": ran,
            "passed": tally["passed"],
            "failed": tally["failed"],
            "errors": tally["error"],
            "xfailed": tally["xfailed"],
            "skipped": tally["skipped"],
            "seconds": meta["seconds"],
            "at": meta["at"],
        },
        "packages": [
            {"pkg": pkg, "files": [{"file": f, "tests": t} for f, t in files.items()]}
            for pkg, files in sorted(packages.items())
        ],
    }


def run_pytest(targets: list[str], junit: Path) -> str:
    """Run pytest for real, streaming its output so a demo shows the actual run."""
    argv = [sys.executable, "-m", "pytest", "-q", *targets, f"--junitxml={junit}"]
    shown = " ".join(["pytest", "-q", *targets])
    print(f"$ {shown}\n", flush=True)
    junit.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    code = subprocess.call(argv)
    print(f"\npytest exited {code} after {time.monotonic() - started:.1f}s", flush=True)
    return shown


def head_commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    )
    return proc.stdout.strip() or "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        nargs="*",
        metavar="TARGET",
        help="run pytest before building. No targets means the whole suite.",
    )
    parser.add_argument("--junit", type=Path, default=EVIDENCE / "junit.xml")
    parser.add_argument("--out", type=Path, default=Path("site/index.html"))
    args = parser.parse_args(argv)

    # Only claim a command when this process actually ran it; a reused junit.xml
    # came from a command we cannot know, and inventing one would be a lie.
    command, source = str(args.junit), "From"
    if args.run is not None:
        command, source = run_pytest(args.run, args.junit), "Command"
    elif not args.junit.exists():
        raise SystemExit(
            f"{args.junit} does not exist. Run the tests first:\n"
            f"    python -m scripts.build_dashboard --run"
        )

    print("collecting the full inventory...", file=sys.stderr)
    inventory = collect_inventory()
    seen, meta = results_from_junit(args.junit)

    unknown = set(seen) - {(f, n) for _, f, n in inventory}
    if unknown:
        # A junit.xml from a different tree would silently under-report coverage.
        print(
            f"warning: {len(unknown)} result(s) in {args.junit} match no collected test "
            "-- is the file stale?",
            file=sys.stderr,
        )

    data = {
        "commit": head_commit(),
        "generated": datetime.now(timezone.utc).strftime("%d %b %Y"),
        "bands": bands_from_sweep(json.loads((EVIDENCE / "sweep.json").read_text("utf-8"))),
        "ablation": ablation_from_report(
            (EVIDENCE / "schedule-ablation.md").read_text("utf-8")
        ),
        "suite": build_suite(inventory, seen, meta, command, source),
    }

    template = (Path(__file__).parent / "dashboard_template.html").read_text("utf-8")
    if "/*__DATA__*/null" not in template:
        raise SystemExit("dashboard_template.html has no /*__DATA__*/null placeholder")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        template.replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":"))),
        encoding="utf-8",
    )

    run = data["suite"]["run"]
    print(
        f"wrote {args.out} -- {run['count']} of {data['suite']['total']} tests ran, "
        f"{run['passed']} passed, {run['failed']} failed, {run['xfailed']} xfailed",
        file=sys.stderr,
    )
    return 1 if run["failed"] or run["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
