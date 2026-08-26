"""Create a Razorpay test-mode plan and subscription, and print the auth link.

Run this once before the demo. The link it prints is what a human opens to
authorise the mandate with a test card; after that, failing a charge from
the Razorpay Dashboard fires the ``subscription.pending`` webhook that the
receiver ingests. See docs/runbooks/razorpay-test-mode.md for the full
click-path.

Usage:
    python -m scripts.setup_test_subscription --amount-paise 99900
"""

import argparse
import sys
from dataclasses import dataclass
from typing import Any

from recoup.razorpay.config import load_config


@dataclass(frozen=True)
class SetupResult:
    plan_id: str
    subscription_id: str
    auth_url: str


def create_test_subscription(
    client: Any, plan_amount_paise: int, total_count: int = 12
) -> SetupResult:
    if plan_amount_paise <= 0:
        raise ValueError(f"plan_amount_paise must be positive, got {plan_amount_paise}")

    plan = client.plan.create(
        {
            "period": "monthly",
            "interval": 1,
            "item": {
                "name": "Recoup demo subscription",
                "amount": plan_amount_paise,
                "currency": "INR",
                "description": "Buildathon demo plan for AI revenue recovery",
            },
        }
    )
    subscription = client.subscription.create(
        {
            "plan_id": plan["id"],
            "total_count": total_count,
            "customer_notify": 1,
        }
    )
    return SetupResult(
        plan_id=str(plan["id"]),
        subscription_id=str(subscription["id"]),
        auth_url=str(subscription.get("short_url", "")),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--amount-paise", type=int, default=99900)
    parser.add_argument("--total-count", type=int, default=12)
    args = parser.parse_args(argv)

    config = load_config()
    from recoup.execute.razorpay_rail import build_client

    result = create_test_subscription(build_client(config), args.amount_paise, args.total_count)
    print(f"plan_id:         {result.plan_id}")
    print(f"subscription_id: {result.subscription_id}")
    print(f"authorise here:  {result.auth_url or '(no short_url returned)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
