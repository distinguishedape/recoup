"""Razorpay ``subscription.pending`` payload -> ``FailureEvent``.

This is the only module in Recoup that knows Razorpay's payload shape.
Everything downstream sees the domain model, which is what lets the same
pipeline run on live webhooks and on the synthetic cohort.

Two decisions worth stating:

* **The subscription state beats the payment error.** A cancelled
  subscription means the customer withdrew authorisation (spec F6), and
  that is decisive regardless of what the last payment attempt happened
  to say. Recoup synthesises the reason string ``subscription_cancelled``
  so the flat taxonomy table can classify it.
* **Nothing here raises on a surprising payload.** Razorpay can add,
  rename or omit fields at any time, and a webhook that raises is a
  webhook that is gone forever. Missing values become explicit
  ``unknown`` markers, which classify as ``UNCLASSIFIED`` -- a real class
  with a defined budget -- rather than crashing the receiver.
"""

from datetime import datetime, timezone
from typing import Any

from recoup.classify.taxonomy import SUBSCRIPTION_CANCELLED_REASON
from recoup.models.core import FailureEvent

UNKNOWN_REASON = "unknown_error"
UNKNOWN_FIELD = "unknown"
REVOKED_SUBSCRIPTION_STATES = frozenset({"cancelled", "expired"})


def _entity(payload: dict[str, Any], name: str) -> dict[str, Any]:
    container = payload.get("payload", {})
    if not isinstance(container, dict):
        return {}
    wrapper = container.get(name, {})
    if not isinstance(wrapper, dict):
        return {}
    entity = wrapper.get("entity", {})
    return entity if isinstance(entity, dict) else {}


def _text(entity: dict[str, Any], key: str, default: str) -> str:
    value = entity.get(key)
    if value is None or not str(value).strip():
        return default
    return str(value).strip()


def map_subscription_pending(payload: dict[str, Any], received_at: datetime) -> FailureEvent:
    subscription = _entity(payload, "subscription")
    payment = _entity(payload, "payment")
    invoice = _entity(payload, "invoice")

    subscription_id = _text(subscription, "id", UNKNOWN_FIELD)
    status = _text(subscription, "status", UNKNOWN_FIELD).lower()

    if status in REVOKED_SUBSCRIPTION_STATES:
        error_reason = SUBSCRIPTION_CANCELLED_REASON
    else:
        error_reason = _text(payment, "error_reason", UNKNOWN_REASON)

    invoice_id = _text(payment, "invoice_id", "")
    if not invoice_id:
        invoice_id = _text(invoice, "id", f"inv_unknown:{subscription_id}")

    created_at = payload.get("created_at")
    if isinstance(created_at, (int, float)):
        occurred_at = datetime.fromtimestamp(created_at, tz=timezone.utc)
    else:
        occurred_at = received_at

    paid_count = subscription.get("paid_count")
    attempt_number = paid_count + 1 if isinstance(paid_count, int) and paid_count >= 0 else 1

    return FailureEvent(
        event_id=_text(payment, "id", f"evt_unknown:{subscription_id}"),
        subscription_id=subscription_id,
        invoice_id=invoice_id,
        error_reason=error_reason,
        error_source=_text(payment, "error_source", UNKNOWN_FIELD),
        error_step=_text(payment, "error_step", UNKNOWN_FIELD),
        attempt_number=attempt_number,
        occurred_at=occurred_at,
        source="webhook",
    )


def map_payment_failed(payload: dict[str, Any], received_at: datetime) -> FailureEvent:
    """A standalone ``payment.failed`` event, mapped to the same shape.

    Razorpay gates Subscriptions behind full account activation, so an account
    that has not completed KYC cannot create a plan, cannot create a
    subscription, and will never emit ``subscription.pending`` -- its
    ``/v1/plans`` and ``/v1/subscriptions`` endpoints answer 401 while every
    other product answers 200.

    A failed payment on an ordinary order carries exactly the signal the
    classifier needs: the same ``error_reason``, ``error_source`` and
    ``error_step`` triple, signed by Razorpay and delivered over the same
    webhook. It is a different event, not a weaker one, and mapping it here
    means the real ingestion path can be exercised for real on an account that
    cannot yet run subscriptions.

    A standalone payment has no subscription, so the order id stands in as the
    subject identifier. Nothing downstream cares which producer a record came
    from, which is the property that makes this substitution possible at all.
    """
    payment = _entity(payload, "payment")
    order = _entity(payload, "order")

    payment_id = _text(payment, "id", UNKNOWN_FIELD)
    order_id = _text(payment, "order_id", "") or _text(order, "id", "")
    subject_id = order_id or f"pay_subject:{payment_id}"

    created_at = payload.get("created_at")
    if isinstance(created_at, (int, float)):
        occurred_at = datetime.fromtimestamp(created_at, tz=timezone.utc)
    else:
        occurred_at = received_at

    return FailureEvent(
        event_id=payment_id,
        subscription_id=subject_id,
        invoice_id=_text(payment, "invoice_id", "") or f"inv_unknown:{subject_id}",
        error_reason=_text(payment, "error_reason", UNKNOWN_REASON),
        error_source=_text(payment, "error_source", UNKNOWN_FIELD),
        error_step=_text(payment, "error_step", UNKNOWN_FIELD),
        attempt_number=1,
        occurred_at=occurred_at,
        source="webhook",
    )


#: Event types the receiver handles, and the mapper for each. Anything absent
#: is acknowledged and ignored rather than retried forever.
MAPPERS = {
    "subscription.pending": map_subscription_pending,
    "payment.failed": map_payment_failed,
}
