"""Detection from the command line: what scan() and at_risk_paise() find.

    python -m scripts.scan

READ-ONLY. Builds a RazorpayReadClient from load_config() and calls scan() --
nothing here creates, modifies or cancels anything. recoup/razorpay/client.py
exposes no write verb at all (spec: detection must be safe to run on a schedule
against a live account without anyone having to read the code to be sure), and
this script inherits that guarantee by construction rather than adding one of
its own.

Every RiskSignal scan() finds is printed, including the ones it cannot act on.
An abandoned checkout nobody ever attempted has no decline to classify -- that
is not a defect, and hiding it here would be the same fabrication the scanner
itself refuses to commit by inventing a cause it does not have.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from recoup.detect.scanner import RiskSignal, at_risk_paise, scan
from recoup.razorpay.client import RazorpayReadClient
from recoup.razorpay.config import load_config
from recoup.report.render import format_rupees

UNHANDLED_LABEL = "detected, unhandled -- no decline to classify"


def format_age(age: timedelta) -> str:
    hours = age.total_seconds() / 3600
    return f"{hours:.1f}h"


def format_signal(signal: RiskSignal) -> str:
    status = "actionable" if signal.actionable else UNHANDLED_LABEL
    return (
        f"[{signal.kind.value}] {signal.entity_id}  "
        f"{format_rupees(signal.amount_paise)}  "
        f"age {format_age(signal.age)}  "
        f"({status})  {signal.detail}"
    )


def render_signals(signals: list[RiskSignal]) -> list[str]:
    lines = [format_signal(signal) for signal in signals]
    lines.append(f"total at risk: {format_rupees(at_risk_paise(signals))}")
    return lines


def scan_and_render(client: RazorpayReadClient, now: datetime) -> list[str]:
    return render_signals(scan(client, now))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        # format_rupees prints U+20B9 (RUPEE SIGN); a Windows console's default
        # cp1252 stdout cannot encode it and would crash a real run.
        sys.stdout.reconfigure(encoding="utf-8")

    config = load_config()
    client = RazorpayReadClient(config)
    for line in scan_and_render(client, datetime.now(timezone.utc)):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
