"""The live demo: a real failed payment becomes a real Razorpay payment link.

    python -m scripts.demo                      # newest failed payment on the account
    python -m scripts.demo --subscription sub_X # a specific one
    python -m scripts.demo --dry-run            # replay the recorded transcript, no network

Every line printed is either returned by the live API or read back out of the
audit log after the fact. The narration cannot say more than the log recorded,
which is the same rule the report and the console already follow.

The charge path is not exercised and cannot be: `RazorpayTestRail.charge`
raises, because Razorpay exposes no manual-retry API for subscription invoices.
What runs here is the part that is real -- read the decline, classify it, plan
the intervention, create the link -- and the demo says so rather than skipping
past it.
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recoup.audit.log import AuditLog
from recoup.detect.scanner import failure_from_payment
from recoup.execute.razorpay_rail import RazorpayTestRail, build_client
from recoup.live.agent import LiveAgent
from recoup.razorpay.client import RazorpayReadClient
from recoup.razorpay.config import load_config
from recoup.razorpay.payment_links import PaymentLinkWriter

TRANSCRIPT = Path("evidence/demo-transcript.md")
DASHBOARD = "https://dashboard.razorpay.com/app/payment-links"

STAGE_LABEL = {
    "classify": "cause identified",
    "plan": "intervention planned",
    "execute": "action executed",
    "policy_block": "action denied by policy",
    "ladder_block": "action withheld by the ladder",
    "pay_now_link_created": "payment link created in Razorpay",
    "pay_now_link_unavailable": "no link created -- refused to guess",
    "pay_now_link_payment_unknown": "payment not asserted; conversion arrives by reconciliation",
}


def find_failed_payment(
    client: RazorpayReadClient, subscription_id: str | None
) -> tuple[dict[str, Any], str]:
    """The newest real failed payment on the account, and a subscription to act on.

    Never invents either. An account with nothing failed is a setup problem the
    operator must fix, not something to paper over with a synthetic event.
    """
    failed = [p for p in client.payments(count=25) if str(p.get("status")) == "failed"]
    if not failed:
        raise SystemExit(
            "no failed payment on this account. Create one first: "
            "`python -m scripts.setup_test_subscription`, authorise the mandate, then "
            "fail a charge from the Razorpay Dashboard. "
            "See docs/runbooks/razorpay-test-mode.md."
        )
    payment = failed[0]

    if subscription_id is None:
        subscriptions = [
            s for s in client.subscriptions(count=25)
            if str(s.get("status")) in {"halted", "pending"}
        ]
        if not subscriptions:
            raise SystemExit(
                "no halted or pending subscription to act on. "
                "See docs/runbooks/razorpay-test-mode.md."
            )
        subscription_id = str(subscriptions[0]["id"])
    return payment, subscription_id


def narrate_stages(audit: AuditLog, subscription_id: str) -> list[str]:
    """The stages the log actually recorded, in order. The narration's only source."""
    return [record.stage for record in audit.reconstruct(subscription_id)]


def narrate(audit: AuditLog, subscription_id: str) -> list[str]:
    lines: list[str] = []
    for record in audit.reconstruct(subscription_id):
        label = STAGE_LABEL.get(record.stage)
        if label is None:
            continue
        payload = record.payload
        detail = (
            payload.get("failure_class")
            or payload.get("action_type")
            or payload.get("short_url")
            or payload.get("detail")
            or ""
        )
        rule = payload.get("rule") or payload.get("verdict_rule")
        suffix = f"  [rule: {rule}]" if rule else ""
        lines.append(f"{label}: {detail}{suffix}")
    return lines


def run_demo(
    read_client: RazorpayReadClient,
    rail: Any,
    audit: AuditLog,
    subscription_id: str | None,
    now: datetime,
) -> list[str]:
    payment, sub_id = find_failed_payment(read_client, subscription_id)
    event = failure_from_payment(payment, sub_id, now)
    agent = LiveAgent(audit=audit, rail=rail)
    agent.handle(event, amount_paise=int(payment.get("amount") or 0))
    return narrate(audit, sub_id)


def _print_slowly(lines: list[str], pause: float) -> None:
    for line in lines:
        print(f"  {line}")
        time.sleep(pause)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="replay the recorded transcript; no network, no credentials")
    parser.add_argument("--pause", type=float, default=0.6,
                        help="seconds between narration lines, so a watcher can read them")
    parser.add_argument("--audit-db", type=Path, default=Path("artifacts/demo.db"))
    parser.add_argument("--record", type=Path, default=None,
                        help="write the transcript of this run to a file")
    args = parser.parse_args(argv)

    if args.dry_run:
        if not TRANSCRIPT.exists():
            print(f"no recorded transcript at {TRANSCRIPT}", file=sys.stderr)
            return 1
        print(TRANSCRIPT.read_text(encoding="utf-8"))
        return 0

    config = load_config()
    audit = AuditLog(args.audit_db)
    try:
        rail = RazorpayTestRail(
            build_client(config), config, links=PaymentLinkWriter(config), audit=audit
        )
        print("Recoup -- live, against Razorpay test mode\n")
        lines = run_demo(
            RazorpayReadClient(config), rail, audit, args.subscription,
            datetime.now(timezone.utc),
        )
        _print_slowly(lines, args.pause)
        print(f"\n  open {DASHBOARD} -- the link above is there now.")
    finally:
        audit.close()

    if args.record:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(f"  {line}" for line in lines)
        args.record.write_text(
            "# Recorded demo transcript\n\n"
            "A recording of one real run of `python -m scripts.demo` against Razorpay "
            "test mode. Replay it with `python -m scripts.demo --dry-run`, which needs "
            "no credentials and no network. It is a recording, not a live result.\n\n"
            f"Recorded {datetime.now(timezone.utc).isoformat()}\n\n"
            f"```\n{body}\n```\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.record}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
