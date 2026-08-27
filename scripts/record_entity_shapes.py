"""Record the *shape* of the live Razorpay entities this project reads.

    python -m scripts.record_entity_shapes            # write tests/fixtures/entity_shapes.json
    python -m scripts.record_entity_shapes --print    # show it without writing

Why this exists. ``_amount_for`` and ``_customer_for`` were first written
against a guessed subscription entity -- a nested ``amount`` and a nested
``customer`` object, neither of which a real subscription has. The unit tests
passed, because the fakes encoded the same guess, so they agreed with the wrong
assumption and proved nothing. The shape was corrected against the live account,
and the correction was recorded in a commit message and a code comment.

That is exactly the failure mode of D61: a claim that was true when written,
checkable by nobody afterwards, and silently invalidated later. So the shape is
recorded here as an artefact instead, and ``tests/razorpay/test_entity_shape_contract.py``
holds the hand-written fakes to it.

**Names and types only, never values.** The recording is committed, and the
account is real; an entity's ``customer_email``, ``customer_contact``, ids and
amounts are nobody's business here and the fakes assert nothing about them. What
matters -- the whole content of the claim being made -- is which keys exist.

Every call is a GET through the read-only client, which a test proves contains
no write verb.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from recoup.razorpay.client import RazorpayReadClient
from recoup.razorpay.config import load_config

FIXTURE = Path("tests/fixtures/entity_shapes.json")


def shape_of(entity: dict[str, Any]) -> dict[str, Any]:
    """Key names and value types, one level into nested dicts. No values."""
    shape: dict[str, Any] = {}
    for key in sorted(entity):
        value = entity[key]
        if isinstance(value, dict):
            shape[key] = shape_of(value)
        elif isinstance(value, list):
            shape[key] = "list"
        elif value is None:
            shape[key] = "null"
        else:
            shape[key] = type(value).__name__
    return shape


def record(client: RazorpayReadClient) -> dict[str, Any]:
    recorded: dict[str, Any] = {}

    subscriptions = client.subscriptions(count=1)
    if not subscriptions:
        raise SystemExit(
            "the account has no subscription to record. Create one with "
            "`python -m scripts.setup_test_subscription` first -- a recording of nothing "
            "would be a fixture that asserts nothing."
        )
    subscription = subscriptions[0]
    recorded["subscription"] = shape_of(subscription)

    plan_id = subscription.get("plan_id")
    if plan_id:
        recorded["plan"] = shape_of(client.plan(str(plan_id)))

    links = client.payment_links(count=1)
    if links:
        recorded["payment_link"] = shape_of(links[0])

    return recorded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=FIXTURE)
    parser.add_argument("--print", action="store_true", help="print instead of writing")
    args = parser.parse_args(argv)

    recorded = record(RazorpayReadClient(load_config()))
    payload = {
        "_comment": (
            "Field names and value types of the live Razorpay entities this project "
            "reads, recorded by scripts/record_entity_shapes.py against a test-mode "
            "account. Values are deliberately absent. Regenerate to re-check the claim; "
            "tests/razorpay/test_entity_shape_contract.py holds the test fakes to it."
        ),
        "entities": recorded,
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.print:
        print(text)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({', '.join(sorted(recorded))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
