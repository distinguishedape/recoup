"""Deterministic reason-string classification (spec R2).

Most Razorpay decline reasons say exactly what went wrong. A lookup table
handles those: it is free, instant, auditable and cannot hallucinate. Only
two reason strings -- ``card_declined`` and ``payment_failed`` -- are
genuinely uninformative, and only those go to the LLM.

``MANDATE_REVOKED`` is not a payment error in Razorpay; it is a
subscription state (spec F6). Ingestion synthesises the reason string
``subscription_cancelled`` when it sees that state, so the taxonomy stays
a single flat mapping.

The mapping is drawn from Razorpay's published error-reason list rather than
from the handful of strings the design happened to name. An earlier version
covered fifteen, which meant six documented issuer-outage reasons --
``issuer_technical_error``, ``bank_not_available``, ``bank_cutoff_in_progress``
and friends -- fell through to ``UNCLASSIFIED`` and were handed the slow
generic ladder. Those are the *best* failures to hold: the highest recovery
probability of any class and the one strategy that wants a fast retry rather
than a patient one. Missing them was the exact mistake this product exists to
avoid, committed by the product itself.

Anything still unmapped remains ``UNCLASSIFIED`` on purpose. The bucket is a
real class with a real budget, not a formality, and Razorpay documents roughly
a hundred reasons of which most cannot arise on a stored-mandate auto-debit.
"""

from recoup.models.core import Classification, FailureEvent
from recoup.models.enums import FailureClass

SUBSCRIPTION_CANCELLED_REASON = "subscription_cancelled"

AMBIGUOUS_REASONS: frozenset[str] = frozenset(
    {
        "card_declined",
        "payment_failed",
        # Razorpay's own wording for both of these is that the issuer or gateway
        # declined and "the exact reason in this case is not shared". That is the
        # definition of ambiguous, so they go to the model like the other two.
        "payment_declined",
        "debit_declined",
    }
)

TABLE: dict[str, FailureClass] = {
    # --- the money was not there ---
    "insufficient_funds": FailureClass.INSUFFICIENT_FUNDS,
    "funds_blocked_by_mandate": FailureClass.INSUFFICIENT_FUNDS,
    # --- the instrument cannot succeed as it stands ---
    "card_expired": FailureClass.INSTRUMENT_INVALID,
    "card_not_enrolled": FailureClass.INSTRUMENT_INVALID,
    "card_disabled_for_online_payments": FailureClass.INSTRUMENT_INVALID,
    "debit_instrument_inactive": FailureClass.INSTRUMENT_INVALID,
    "debit_instrument_blocked": FailureClass.INSTRUMENT_INVALID,
    "card_type_invalid": FailureClass.INSTRUMENT_INVALID,
    "card_number_invalid": FailureClass.INSTRUMENT_INVALID,
    # --- authorisation withdrawn (synthesised from subscription state, see F6) ---
    SUBSCRIPTION_CANCELLED_REASON: FailureClass.MANDATE_REVOKED,
    # --- somebody else's outage, which is why these are worth retrying fast ---
    "bank_technical_error": FailureClass.TRANSIENT_ISSUER,
    "gateway_technical_error": FailureClass.TRANSIENT_ISSUER,
    "issuer_technical_error": FailureClass.TRANSIENT_ISSUER,
    "bank_not_available": FailureClass.TRANSIENT_ISSUER,
    "bank_cutoff_in_progress": FailureClass.TRANSIENT_ISSUER,
    "payment_declined_due_to_high_traffic": FailureClass.TRANSIENT_ISSUER,
    "server_error": FailureClass.TRANSIENT_ISSUER,
    "capture_failed": FailureClass.TRANSIENT_ISSUER,
    # --- a decision about the transaction, not the instrument ---
    "payment_risk_check_failed": FailureClass.RISK_DECLINE,
    "compliance_violation": FailureClass.RISK_DECLINE,
    # --- real reasons that genuinely do not tell us the root cause ---
    "authentication_failed": FailureClass.UNCLASSIFIED,
    "incorrect_cvv": FailureClass.UNCLASSIFIED,
    "transaction_limit_exceeded": FailureClass.UNCLASSIFIED,
    "transaction_daily_limit_exceeded": FailureClass.UNCLASSIFIED,
    "transaction_daily_count_exceeded": FailureClass.UNCLASSIFIED,
    "otp_attempts_exceeded": FailureClass.UNCLASSIFIED,
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
