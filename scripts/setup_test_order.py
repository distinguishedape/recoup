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
<p>Pay with a card Razorpay declines on purpose, so the failure is real rather
than simulated:</p>
<ul>
  <li><strong>4000 0000 0000 0002</strong> - declined by the issuer</li>
  <li>Any future expiry, any CVV, any name.</li>
</ul>
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
    print("Pay with 4000 0000 0000 0002 to produce a real declined payment,")
    print("which fires payment.failed at your registered webhook URL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
