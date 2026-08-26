"""Create a real Razorpay test-mode order and print a payment page for it.

Use this when Subscriptions is not available on the account. Razorpay gates
Subscriptions behind full account activation, so an account that has not
completed KYC answers 401 on ``/v1/plans`` and ``/v1/subscriptions`` while
every other product answers 200. Such an account can never emit
``subscription.pending``.

A failed payment on an ordinary order emits ``payment.failed``, which carries
the same error reason, source and step the classifier needs, signed by Razorpay
and delivered over the same webhook. It exercises the real ingestion path for
real: signature verification against the exact bytes Razorpay sent, mapping,
classification, and the audit trail.

Usage:
    python -m scripts.setup_test_order --amount-paise 99900

Then open the printed HTML file and pay with a card Razorpay fails on
deliberately (see the runbook), which fires the webhook.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from recoup.razorpay.config import RazorpayConfig, load_config

CHECKOUT_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>Recoup - fail a test payment</title>
<body style="font-family: system-ui; max-width: 40rem; margin: 4rem auto; line-height: 1.6">
<h1>Fail a test payment</h1>
<p>Order <code>{order_id}</code> for Rs {amount_inr}.</p>
<p>Pay with one of Razorpay's documented error-scenario cards, so the decline is
real and carries a specific reason the classifier can act on. Use any future
expiry and any random CVV, then <strong>choose Failure</strong> on the mock bank
page that follows.</p>
<table cellpadding="6" style="border-collapse:collapse">
  <tr><th align="left">Card number</th><th align="left">Reason it produces</th><th align="left">Recoup classifies as</th></tr>
  <tr><td><code>4100 2800 0008 0001</code></td><td>insufficient_funds</td><td>INSUFFICIENT_FUNDS</td></tr>
  <tr><td><code>4100 2800 0003 0006</code></td><td>card_disabled_for_online_payments</td><td>INSTRUMENT_INVALID</td></tr>
  <tr><td><code>4100 2800 0002 0007</code></td><td>gateway_technical_error</td><td>TRANSIENT_ISSUER</td></tr>
  <tr><td><code>4100 2800 0006 0003</code></td><td>card_declined</td><td>ambiguous, goes to the model</td></tr>
  <tr><td><code>4100 2800 0000 0009</code></td><td>authentication_failed</td><td>UNCLASSIFIED</td></tr>
</table>
<p style="color:#666">Card numbers are from Razorpay's published test-card page.
Each one exercises a different branch of the classifier on a genuinely real,
signed webhook.</p>
<button id="pay" style="font-size:1.1rem;padding:.75rem 1.5rem">Pay Rs {amount_inr}</button>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
document.getElementById('pay').onclick = function () {{
  new Razorpay({{
    key: "{key_id}",
    order_id: "{order_id}",
    amount: {amount_paise},
    currency: "INR",
    name: "Recoup",
    description: "Deliberately failing payment for webhook testing",
    handler: function (r) {{ document.body.innerHTML += '<p>Succeeded: ' + r.razorpay_payment_id + '. Use a declining card to produce a failure.</p>'; }},
    modal: {{ ondismiss: function () {{ document.body.innerHTML += '<p>Dismissed without paying.</p>'; }} }}
  }}).open();
}};
</script>
</body>
"""


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    amount_paise: int
    checkout_path: Path


def create_test_order(client: Any, amount_paise: int, receipt: str = "recoup-demo") -> dict:
    if amount_paise <= 0:
        raise ValueError(f"amount_paise must be positive, got {amount_paise}")
    return client.order.create(
        {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "payment_capture": 1,
        }
    )


def write_checkout_page(
    order: dict, config: RazorpayConfig, out_path: Path
) -> OrderResult:
    amount_paise = int(order["amount"])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        CHECKOUT_TEMPLATE.format(
            order_id=order["id"],
            amount_paise=amount_paise,
            amount_inr=f"{amount_paise / 100:.2f}",
            key_id=config.key_id,
        ),
        encoding="utf-8",
    )
    return OrderResult(
        order_id=str(order["id"]), amount_paise=amount_paise, checkout_path=out_path
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--amount-paise", type=int, default=99900)
    parser.add_argument("--out", type=Path, default=Path("artifacts/pay.html"))
    args = parser.parse_args(argv)

    config = load_config()
    from recoup.execute.razorpay_rail import build_client

    order = create_test_order(build_client(config), args.amount_paise)
    result = write_checkout_page(order, config, args.out)

    print(f"order_id:  {result.order_id}")
    print(f"amount:    Rs {result.amount_paise / 100:.2f}")
    print(f"open this: {result.checkout_path.resolve()}")
    print()
    print("Serve it over http rather than opening the file directly --")
    print("Razorpay Checkout needs a real origin:")
    print(f"    python -m http.server 8080 --directory {result.checkout_path.parent}")
    print(f"    then open http://localhost:8080/{result.checkout_path.name}")
    print()
    print("Pay with 4100 2800 0008 0001 and choose Failure on the bank page to")
    print("produce a real insufficient_funds decline, which fires payment.failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
