"""Drive real Razorpay declines through the classifier, one error card each.

Spike finding F1 said test mode cannot inject decline reasons, and every real
failure this project had collected agreed: four payments, four identical
``payment_failed`` strings. F1 turned out to be too strong. Razorpay publishes
**error-scenario test cards** that produce eight distinct error codes on the
order checkout path -- which is the path this project uses, because
Subscriptions was gated behind full account activation.

That matters because it closes the last honest gap in the evidence: the
classifier has only ever been exercised on synthetic reason strings, and these
cards let five of its six classes be driven by declines Razorpay actually
produced.

Two steps, because one of them needs a human:

    python -m scripts.error_card_walk --create
    # open the printed page, pay each card, choose Failure on the bank screen
    python -m scripts.error_card_walk --verify

``--verify`` fetches whatever actually arrived and runs it through the real
classifier, printing the reason Razorpay sent against the class Recoup derived.
It asserts nothing about what *should* happen: if a card produces a different
string than documented, the table it prints is the finding.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recoup.classify.engine import classify
from recoup.llm.client import LLMClient
from recoup.models.core import FailureEvent
from recoup.razorpay.client import RazorpayReadClient
from recoup.razorpay.config import load_config, load_dotenv

# From Razorpay's published test-card table. The instruction that makes them
# work is easy to miss: after initiating payment you must choose *Failure* on
# the mock bank screen, or the card behaves like any other.
ERROR_CARDS: tuple[tuple[str, str, str], ...] = (
    ("4100280000080001", "insufficient_fund", "INSUFFICIENT_FUNDS"),
    ("4100280000030006", "card_disabled_for_online_payments", "INSTRUMENT_INVALID"),
    ("4100280000010008", "card_number_invalid", "INSTRUMENT_INVALID"),
    ("4100280000020007", "gateway_technical_error", "TRANSIENT_ISSUER"),
    ("4100280000060003", "card_declined", "ambiguous -> model"),
    ("4100280000000009", "authentication_failed", "UNCLASSIFIED"),
    ("4100280000090000", "payment_timed_out", "UNCLASSIFIED"),
    ("4100280000070002", "payment_cancelled", "UNCLASSIFIED"),
)

RECEIPT_PREFIX = "recoup-errorcard"
RATE_LIMIT_PAUSE_SECONDS = 3.0
RATE_LIMIT_RETRIES = 4


def _create_one(client, amount_paise: int, reason: str, card: str):
    """One order, retrying the rate limiter rather than losing the walk."""
    delay = RATE_LIMIT_PAUSE_SECONDS
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            return client.order.create(
                {
                    "amount": amount_paise,
                    "currency": "INR",
                    "receipt": f"{RECEIPT_PREFIX}-{reason}",
                    "notes": {"expected_error_reason": reason, "card": card},
                }
            )
        except Exception as exc:
            if "Too many requests" not in str(exc) or attempt == RATE_LIMIT_RETRIES - 1:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")

PAGE = """<meta charset="utf-8">
<title>Recoup error-card walk</title>
<style>
 body{{font:15px/1.6 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 20px}}
 h1{{font-size:22px}} table{{border-collapse:collapse;width:100%;margin-top:18px}}
 th,td{{text-align:left;padding:9px 10px;border-bottom:1px solid #ddd;font-size:14px}}
 code{{font-family:ui-monospace,monospace;font-size:13px}}
 button{{font:inherit;padding:6px 12px;cursor:pointer}}
 .note{{background:#fffbe6;border:1px solid #f0e0a0;padding:12px;border-radius:4px}}
</style>
<h1>Recoup error-card walk</h1>
<p class="note"><b>After you press Pay, choose <i>Failure</i> on the mock bank
screen.</b> Without that the card behaves like any other and you get the generic
error. Any future expiry, any CVV.</p>
<table><tr><th>Card</th><th>Expected reason</th><th></th></tr>
{rows}
</table>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
function pay(orderId, card, amount) {{
  new Razorpay({{
    key: "{key_id}", amount: amount, currency: "INR", order_id: orderId,
    name: "Recoup error-card walk", description: card,
    handler: function (r) {{ alert("Succeeded -- choose Failure next time: " + r.razorpay_payment_id); }},
    modal: {{ ondismiss: function () {{}} }}
  }}).open();
}}
</script>
"""

ROW = (
    '<tr><td><code>{card}</code></td><td><code>{reason}</code></td>'
    '<td><button onclick="pay(\'{order_id}\',\'{card}\',{amount})">Pay</button></td></tr>'
)


def create(client: Any, amount_paise: int, out: Path, key_id: str) -> Path:
    rows = []
    created = []
    for index, (card, reason, _expected) in enumerate(ERROR_CARDS):
        if index:
            # Razorpay rate-limits burst order creation on test accounts and
            # answers "Too many requests" rather than queueing.
            time.sleep(RATE_LIMIT_PAUSE_SECONDS)
        order = _create_one(client, amount_paise, reason, card)
        created.append({"order_id": order["id"], "card": card, "expected": reason})
        rows.append(
            ROW.format(card=card, reason=reason, order_id=order["id"], amount=amount_paise)
        )
        print(f"  {order['id']}  {card}  -> expects {reason}", file=sys.stderr)

    out.write_text(
        PAGE.format(rows="\n".join(rows), key_id=key_id), encoding="utf-8"
    )
    (out.parent / "error_card_orders.json").write_text(
        json.dumps(created, indent=2), encoding="utf-8"
    )
    return out


def verify(read: RazorpayReadClient, manifest: Path, llm: LLMClient | None) -> int:
    if not manifest.exists():
        print(f"no manifest at {manifest}; run --create first", file=sys.stderr)
        return 1
    entries = json.loads(manifest.read_text(encoding="utf-8"))

    print(f"{'expected reason':36} {'actual reason':30} {'source':10} -> classified")
    print("-" * 104)
    seen = 0
    for entry in entries:
        payments = [
            p for p in read.payments_for_order(entry["order_id"]) if p.get("status") == "failed"
        ]
        if not payments:
            print(f"{entry['expected']:36} {'(not attempted yet)':30}")
            continue
        seen += 1
        payment = max(payments, key=lambda p: int(p.get("created_at") or 0))
        event = FailureEvent(
            event_id=str(payment["id"]),
            subscription_id=entry["order_id"],
            invoice_id=entry["order_id"],
            error_reason=str(payment.get("error_reason") or "unknown"),
            error_source=str(payment.get("error_source") or "unknown"),
            error_step=str(payment.get("error_step") or "unknown"),
            attempt_number=1,
            occurred_at=datetime.now(timezone.utc),
            source="webhook",
        )
        result = classify(event, llm)
        match = "" if payment.get("error_reason") == entry["expected"] else "  <- differs"
        print(
            f"{entry['expected']:36} {event.error_reason:30} {event.error_source:10} -> "
            f"{result.failure_class.value} ({result.method}, {result.confidence}){match}"
        )
    print(f"\n{seen}/{len(entries)} cards produced a failed payment.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true", help="create one order per error card")
    parser.add_argument("--verify", action="store_true", help="classify whatever arrived")
    parser.add_argument("--amount-paise", type=int, default=99900)
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts"))
    args = parser.parse_args(argv)

    if not (args.create or args.verify):
        parser.error("pass --create or --verify")

    for key, value in load_dotenv().items():
        os.environ.setdefault(key, value)
    config = load_config()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.create:
        from recoup.execute.razorpay_rail import build_client

        page = create(
            build_client(config), args.amount_paise, args.out_dir / "error_cards.html",
            config.key_id,
        )
        print(f"\nopen {page} and pay each card, choosing Failure on the bank screen")
        return 0

    return verify(
        RazorpayReadClient(config),
        args.out_dir / "error_card_orders.json",
        LLMClient(cache_path=args.out_dir / "llm_cache.json"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
