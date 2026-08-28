"""The live demo: a real failed payment becomes a real Razorpay payment link.

    python -m scripts.demo                      # newest failed payment on the account
    python -m scripts.demo --subscription sub_X # a specific one
    python -m scripts.demo --dry-run            # replay the recorded transcript, no network

Every line printed is either returned by the live API or read back out of the
audit log after the fact. The narration cannot say more than the log recorded,
which is the same rule the report and the console already follow.

The charge path cannot succeed: `RazorpayTestRail.charge` raises, because
Razorpay exposes no manual-retry API for subscription invoices. When the plan
schedules a retry, the demo still lets it come due and lets the rail refuse it
for real -- recorded as `execute_unsupported`, not skipped past.

Some plans (`INSUFFICIENT_FUNDS`, `UNCLASSIFIED`) schedule the pay-now link
hours after the first notify, not at the moment the failure is classified --
that is the timing this project's own experiment measured and it is a
registered measurement input (see `recoup/experiment/inputs.py`), not
something a demo gets to quietly move. `LiveAgent.due()` exists precisely so a
caller can advance to a subject's own recorded schedule and run what has come
due -- "the same tick that looks for newly at-risk revenue is the natural
place to also advance subjects already in the ladder" -- so `run_demo` reads
the pay-now link's `scheduled_at` off the subject's own `plan` audit record
and calls `due()` at that time. It narrates the jump explicitly: a live
deployment would wait that long for real, and saying so out loud is the
honest version of the wait, not a trick to make the link appear sooner than
the plan says it should.
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recoup.audit.log import AuditLog, new_record
from recoup.detect.scanner import failure_from_payment
from recoup.execute.razorpay_rail import RazorpayTestRail, build_client
from recoup.live.agent import LiveAgent
from recoup.razorpay.client import RazorpayReadClient
from recoup.razorpay.config import load_config
from recoup.razorpay.payment_links import PaymentLinkWriter

TRANSCRIPT = Path("evidence/demo-transcript.md")
DASHBOARD = "https://dashboard.razorpay.com/app/payment-links"
PAY_NOW_LINK_ACTION_TYPE = "pay_now_link"
MAX_CONTACT_WINDOW_ADVANCES = 5
"""How many contact-window reschedules run_demo will follow before giving up.

Bounded by the same MAX_RESCHEDULES the policy gate itself enforces
(recoup/policy/rules.py); this just stops the demo from looping past what the
gate would ever actually allow."""

STAGE_LABEL = {
    "classify": "cause identified",
    "plan": "intervention planned",
    "execute": "action executed",
    "execute_unsupported": "action refused by the rail -- no manual-retry API exists",
    "policy_block": "action denied by policy",
    "ladder_block": "action withheld by the ladder",
    "contact_rescheduled": "action rescheduled to stay inside the contact window",
    "advance_clock": "advancing the clock to the plan's own schedule",
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


def _scheduled_time(
    audit: AuditLog, subscription_id: str, action_type: str
) -> tuple[datetime, datetime] | None:
    """(planned_at, scheduled_at) for the subject's own recorded plan.

    Read off the ``plan`` audit record rather than recomputed here, so the
    demo follows whatever the planner actually decided. If the planner's
    delay for this class ever changes, this follows it rather than drifting
    from a number hardcoded in a script.

    ``planned_at`` is the ``plan`` record's own ``virtual_time`` -- the real
    clock reading ``LiveAgent.handle()`` actually scheduled against -- not
    the ``now`` a caller passed into ``run_demo``, which is only ever the
    failure's own ``occurred_at`` and can legitimately be far from the
    moment the plan was built.
    """
    for record in audit.reconstruct(subscription_id):
        if record.stage != "plan":
            continue
        for raw_action in record.payload.get("actions", []):
            if raw_action.get("type") == action_type:
                return record.virtual_time, datetime.fromisoformat(raw_action["scheduled_at"])
    return None


def _advance_to(
    agent: LiveAgent, audit: AuditLog, subscription_id: str, action_type: str, when: datetime
) -> None:
    """Run the ladder forward to and through one action, the way a live
    scheduler would -- including following a contact-window reschedule to its
    new time rather than executing the action before the gate permits it.
    """
    for _ in range(MAX_CONTACT_WINDOW_ADVANCES):
        agent.due(when)
        latest = [
            r
            for r in audit.reconstruct(subscription_id)
            if r.payload.get("action_type") == action_type
        ]
        if not latest or latest[-1].stage != "contact_rescheduled":
            return
        when = datetime.fromisoformat(latest[-1].payload["rescheduled_to"])


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

    schedule = _scheduled_time(audit, sub_id, PAY_NOW_LINK_ACTION_TYPE)
    if schedule is not None:
        planned_at, pay_now_at = schedule
        offset_hours = (pay_now_at - planned_at).total_seconds() / 3600
        audit.append(
            new_record(
                sub_id,
                # A fresh real-clock reading, not `planned_at`: everything the
                # T0 handle() call already executed was stamped a hair after
                # `planned_at` too (Executor reads the clock again per
                # action), so anchoring here on `planned_at` itself would
                # sort this line before those, out of the order it actually
                # happened in.
                agent.clock.now,
                "advance_clock",
                {
                    "detail": (
                        f"advancing to T+{offset_hours:.0f}h -- the point this plan "
                        "schedules the pay-now link"
                    ),
                    "advanced_to": pay_now_at.isoformat(),
                },
            )
        )
        _advance_to(agent, audit, sub_id, PAY_NOW_LINK_ACTION_TYPE, pay_now_at)

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
