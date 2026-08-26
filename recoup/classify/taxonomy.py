"""Deterministic reason-string classification (spec R2).

Most Razorpay decline reasons say exactly what went wrong. A lookup table
handles those: it is free, instant, auditable and cannot hallucinate. Only
two reason strings -- ``card_declined`` and ``payment_failed`` -- are
genuinely uninformative, and only those go to the LLM.

``MANDATE_REVOKED`` is not a payment error in Razorpay; it is a
subscription state (spec F6). Ingestion synthesises the reason string
``subscription_cancelled`` when it sees that state, so the taxonomy stays
a single flat mapping.
"""

from recoup.models.core import Classification, FailureEvent
from recoup.models.enums import FailureClass

SUBSCRIPTION_CANCELLED_REASON = "subscription_cancelled"

AMBIGUOUS_REASONS: frozenset[str] = frozenset({"card_declined", "payment_failed"})

TABLE: dict[str, FailureClass] = {
    "insufficient_funds": FailureClass.INSUFFICIENT_FUNDS,
    "card_expired": FailureClass.INSTRUMENT_INVALID,
    "card_not_enrolled": FailureClass.INSTRUMENT_INVALID,
    "card_disabled_for_online_payments": FailureClass.INSTRUMENT_INVALID,
    "debit_instrument_inactive": FailureClass.INSTRUMENT_INVALID,
    "debit_instrument_blocked": FailureClass.INSTRUMENT_INVALID,
    SUBSCRIPTION_CANCELLED_REASON: FailureClass.MANDATE_REVOKED,
    "bank_technical_error": FailureClass.TRANSIENT_ISSUER,
    "gateway_technical_error": FailureClass.TRANSIENT_ISSUER,
    "payment_risk_check_failed": FailureClass.RISK_DECLINE,
    "authentication_failed": FailureClass.UNCLASSIFIED,
    "incorrect_cvv": FailureClass.UNCLASSIFIED,
    "transaction_limit_exceeded": FailureClass.UNCLASSIFIED,
    "payment_timed_out": FailureClass.UNCLASSIFIED,
    "payment_cancelled": FailureClass.UNCLASSIFIED,
}

TABLE_CONFIDENCE = 0.99
UNMAPPED_CONFIDENCE = 0.40


def normalise(reason: str) -> str:
    return reason.strip().lower()


def classify_by_table(event: FailureEvent) -> Classification | None:
    """Classify by lookup. ``None`` means "ambiguous -- ask the LLM"."""
    reason = normalise(event.error_reason)
    if reason in AMBIGUOUS_REASONS:
        return None
    if reason in TABLE:
        return Classification(
            failure_class=TABLE[reason],
            method="table",
            confidence=TABLE_CONFIDENCE,
            rationale=f"reason string {reason!r} maps directly to this class",
        )
    return Classification(
        failure_class=FailureClass.UNCLASSIFIED,
        method="table",
        confidence=UNMAPPED_CONFIDENCE,
        rationale=(
            f"reason string {reason!r} is not in the taxonomy; "
            "treated as unclassified and given the baseline retry budget"
        ),
    )
