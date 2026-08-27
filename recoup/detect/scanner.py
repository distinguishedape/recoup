"""Going and looking for revenue at risk, instead of waiting to be told.

The track's problem statement names three sources -- payment failures,
checkout abandonment, overdue receivables -- and until now this project
handled one, reactively. A webhook is a push: it tells you about a failure the
moment it happens, and it tells you about nothing else. Abandonment and
receivables have no webhook to wait for, because *nothing happened* is not an
event. They can only be found by asking.

Polling is also what production systems do for correctness rather than
novelty. Webhooks get missed -- endpoints go down, signatures rotate, a deploy
drops a minute of traffic -- and a merchant who only listens quietly loses the
failures that arrived during the gap. Push for latency, poll for completeness.

**What this does not do is invent a cause.** An order the customer attempted
and failed to pay carries real Razorpay payments, with the same
``error_reason``, ``error_source`` and ``error_step`` the classifier already
consumes, so it converts into a ``FailureEvent`` and the existing agent handles
it properly. An order with zero attempts carries no failure at all -- nobody
declined anything, the customer simply left -- so it is emitted as a signal and
explicitly *not* given a failure class. Inventing one would be the exact
mistake this codebase keeps refusing to make.
"""

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from recoup.models.core import FailureEvent
from recoup.razorpay.client import RazorpayReadClient

ABANDONED_AFTER = timedelta(minutes=30)
"""How long an unpaid order sits before it counts as abandoned.

Short enough that a nudge still lands while intent is warm, long enough that
someone reading their card number off a physical card is not chased mid-
checkout."""

OVERDUE_GRACE = timedelta(hours=0)
"""Receivables are overdue the moment ``due_by`` passes. Any grace period is a
merchant's commercial decision, not ours to assume."""

UNPAID_ORDER_STATES = frozenset({"created", "attempted"})
UNPAID_INVOICE_STATES = frozenset({"issued", "partially_paid", "expired"})
AT_RISK_SUBSCRIPTION_STATES = frozenset({"pending", "halted"})


class RiskKind(str, Enum):
    PAYMENT_FAILURE = "payment_failure"
    CHECKOUT_ABANDONMENT = "checkout_abandonment"
    OVERDUE_RECEIVABLE = "overdue_receivable"


class RiskSignal(BaseModel):
    """One piece of revenue found to be at risk, and what is known about why."""

    model_config = ConfigDict(frozen=True)

    kind: RiskKind
    entity_id: str
    amount_paise: int = Field(ge=0)
    detected_at: datetime
    age: timedelta
    actionable: bool
    """Whether this converts into a ``FailureEvent`` the existing agent can act on.

    False is not a defect. An abandoned cart nobody ever attempted to pay has
    no decline to classify, and the intervention it wants -- a recovery link,
    an alternative method -- is a different menu from the one built here. It is
    surfaced honestly as detected-but-unhandled rather than shoehorned into a
    failure class it does not have."""
    detail: str
    failure_event: FailureEvent | None = None


def _epoch(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _failure_from_payment(
    payment: dict[str, Any], entity_id: str, detected_at: datetime
) -> FailureEvent:
    return FailureEvent(
        event_id=str(payment.get("id") or f"scan_{entity_id}"),
        subscription_id=entity_id,
        invoice_id=str(payment.get("order_id") or entity_id),
        error_reason=str(payment.get("error_reason") or "payment_failed"),
        error_source=str(payment.get("error_source") or "unknown"),
        error_step=str(payment.get("error_step") or "unknown"),
        attempt_number=1,
        occurred_at=_epoch(payment.get("created_at")) or detected_at,
        source="webhook",
    )


def scan_orders(
    client: RazorpayReadClient,
    now: datetime,
    abandoned_after: timedelta = ABANDONED_AFTER,
) -> list[RiskSignal]:
    """Unpaid orders older than the threshold.

    ``attempts`` is the cause signal Razorpay gives away for free: zero means
    the customer never tried, non-zero means they tried and something declined
    them. Those are different problems wanting different answers, which is this
    product's whole thesis applied to a second surface.
    """
    signals: list[RiskSignal] = []
    for order in client.orders():
        status = str(order.get("status", ""))
        created = _epoch(order.get("created_at"))
        if status not in UNPAID_ORDER_STATES or created is None:
            continue
        age = now - created
        if age < abandoned_after:
            continue

        order_id = str(order.get("id"))
        amount = int(order.get("amount") or 0)
        attempts = int(order.get("attempts") or 0)

        if attempts == 0:
            signals.append(
                RiskSignal(
                    kind=RiskKind.CHECKOUT_ABANDONMENT,
                    entity_id=order_id,
                    amount_paise=amount,
                    detected_at=now,
                    age=age,
                    actionable=False,
                    detail=(
                        "checkout abandoned without an attempt; no decline exists "
                        "to classify, so no intervention is chosen here"
                    ),
                )
            )
            continue

        failed = [p for p in client.payments_for_order(order_id) if p.get("status") == "failed"]
        if not failed:
            continue
        latest = max(failed, key=lambda p: int(p.get("created_at") or 0))
        signals.append(
            RiskSignal(
                kind=RiskKind.CHECKOUT_ABANDONMENT,
                entity_id=order_id,
                amount_paise=amount,
                detected_at=now,
                age=age,
                actionable=True,
                detail=(
                    f"{attempts} attempt(s), last declined "
                    f"{latest.get('error_reason') or 'unknown'}"
                ),
                failure_event=_failure_from_payment(latest, order_id, now),
            )
        )
    return signals


def scan_invoices(
    client: RazorpayReadClient, now: datetime, grace: timedelta = OVERDUE_GRACE
) -> list[RiskSignal]:
    """Invoices past their due date and not paid."""
    signals: list[RiskSignal] = []
    for invoice in client.invoices():
        status = str(invoice.get("status", ""))
        due = _epoch(invoice.get("due_by"))
        if status not in UNPAID_INVOICE_STATES or due is None:
            continue
        if now < due + grace:
            continue
        signals.append(
            RiskSignal(
                kind=RiskKind.OVERDUE_RECEIVABLE,
                entity_id=str(invoice.get("id")),
                amount_paise=int(invoice.get("amount") or 0)
                - int(invoice.get("amount_paid") or 0),
                detected_at=now,
                age=now - due,
                actionable=False,
                detail=(
                    f"invoice {status}, due {due.date().isoformat()}; a reminder "
                    "ladder for receivables is not built here"
                ),
            )
        )
    return signals


def scan_subscriptions(client: RazorpayReadClient, now: datetime) -> list[RiskSignal]:
    """Subscriptions Razorpay has already moved into an at-risk state.

    This is the same revenue the webhook reports, found without one. Running
    both is the point: the poll catches whatever the push dropped.
    """
    signals: list[RiskSignal] = []
    for subscription in client.subscriptions():
        status = str(subscription.get("status", ""))
        if status not in AT_RISK_SUBSCRIPTION_STATES:
            continue
        sub_id = str(subscription.get("id"))
        changed = _epoch(subscription.get("charge_at")) or _epoch(
            subscription.get("created_at")
        )
        signals.append(
            RiskSignal(
                kind=RiskKind.PAYMENT_FAILURE,
                entity_id=sub_id,
                amount_paise=0,
                detected_at=now,
                age=(now - changed) if changed else timedelta(0),
                actionable=False,
                detail=(
                    f"subscription {status}; fetch the failed invoice's payment to "
                    "classify the decline"
                ),
            )
        )
    return signals


def scan(
    client: RazorpayReadClient,
    now: datetime | None = None,
    abandoned_after: timedelta = ABANDONED_AFTER,
) -> list[RiskSignal]:
    """Every risk surface, in one pass."""
    now = now or datetime.now(timezone.utc)
    return [
        *scan_subscriptions(client, now),
        *scan_orders(client, now, abandoned_after),
        *scan_invoices(client, now),
    ]


def at_risk_paise(signals: Iterable[RiskSignal]) -> int:
    return sum(s.amount_paise for s in signals)


PAID_LINK_STATES = frozenset({"paid"})


class LinkOutcome(BaseModel):
    """Whether one payment link the agent created was actually paid.

    ``reference_id`` carries the subscription id set at link creation, which
    is what ties a payment back to the subject that earned it."""

    model_config = ConfigDict(frozen=True)

    link_id: str
    reference_id: str
    status: str
    amount_paise: int = Field(ge=0)
    paid: bool


def reconcile_pay_now_links(
    client: RazorpayReadClient, now: datetime
) -> list[LinkOutcome]:
    """Which of the links we created were actually paid.

    The rail cannot know this at send time and deliberately refuses to guess,
    so this is where a pay-now recovery is confirmed. ``reference_id`` carries
    the subscription id we set at creation, which is what ties a payment back
    to the subject that earned it.
    """
    outcomes: list[LinkOutcome] = []
    for link in client.payment_links():
        reference = str(link.get("reference_id") or "")
        if not reference:
            continue
        status = str(link.get("status", ""))
        outcomes.append(
            LinkOutcome(
                link_id=str(link.get("id")),
                reference_id=reference,
                status=status,
                amount_paise=int(link.get("amount_paid") or 0),
                paid=status in PAID_LINK_STATES,
            )
        )
    return outcomes
