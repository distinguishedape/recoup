"""The payment rail: a protocol, and the simulated implementation behind it.

Razorpay test mode can only be told "succeed" or "fail" from the Dashboard;
it cannot be made to decline for a *specific reason*, and it exposes no
manual-retry API for domestic cards (spec F1, F2). So the recovery outcomes
in the experiment are simulated against the published probability bands.

``SimulatedRail`` knows each subject's true root cause. The classifier does
not, and must infer it from the reason string like it would in production.
Keeping the ground truth here and nowhere else is what makes the accuracy
numbers mean anything.

The real Razorpay-backed implementation satisfies the same ``PaymentRail``
protocol and is delivered by the ingestion plan.
"""

import hashlib
import random
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from recoup.execute.probabilities import (
    POST_UPDATE_CHARGE_SUCCESS,
    pay_now_conversion_probability,
    retry_success_probability,
    update_conversion_probability,
)
from recoup.models.enums import Band, FailureClass

_DECLINES: dict[FailureClass, tuple[str, str, str]] = {
    FailureClass.INSUFFICIENT_FUNDS: ("insufficient_funds", "bank", "payment_authorization"),
    FailureClass.INSTRUMENT_INVALID: ("card_expired", "issuer", "payment_authentication"),
    FailureClass.MANDATE_REVOKED: ("subscription_cancelled", "business", "payment_initiation"),
    FailureClass.TRANSIENT_ISSUER: ("bank_technical_error", "bank", "payment_authorization"),
    FailureClass.RISK_DECLINE: ("payment_risk_check_failed", "gateway", "payment_authorization"),
    FailureClass.UNCLASSIFIED: ("card_declined", "issuer", "payment_authorization"),
}


def canonical_decline(failure_class: FailureClass) -> tuple[str, str, str]:
    """The (reason, source, step) triple a subject of this class declines with."""
    return _DECLINES[failure_class]


#: The full vocabulary a cause can decline with, drawn from Razorpay's published
#: reason list, with the share of that cause each string accounts for.
#:
#: A cohort that emits one canonical string per cause cannot test a classifier.
#: Every subject maps unambiguously, accuracy is perfect by construction, and
#: the ambiguous strings -- which were *every* real decline observed against a
#: live account -- never appear at all. These weights make the reason string a
#: property of the subject rather than of its label, so classification becomes
#: something the experiment measures instead of something it assumes.
REASON_MIX: dict[FailureClass, dict[str, float]] = {
    FailureClass.INSUFFICIENT_FUNDS: {
        "insufficient_funds": 0.70,
        "payment_failed": 0.20,  # ambiguous: only the model can place it
        "card_declined": 0.07,  # ambiguous
        "funds_blocked_by_mandate": 0.03,
    },
    FailureClass.INSTRUMENT_INVALID: {
        "card_expired": 0.42,
        "debit_instrument_blocked": 0.16,
        "card_disabled_for_online_payments": 0.14,
        "debit_instrument_inactive": 0.10,
        "card_not_enrolled": 0.06,
        "card_declined": 0.09,  # ambiguous
        "payment_failed": 0.03,  # ambiguous
    },
    FailureClass.TRANSIENT_ISSUER: {
        "bank_technical_error": 0.34,
        "gateway_technical_error": 0.24,
        "issuer_technical_error": 0.16,
        "bank_not_available": 0.10,
        "payment_declined_due_to_high_traffic": 0.05,
        "bank_cutoff_in_progress": 0.03,
        "payment_failed": 0.08,  # ambiguous
    },
    FailureClass.RISK_DECLINE: {
        "payment_risk_check_failed": 0.72,
        "compliance_violation": 0.16,
        "card_declined": 0.12,  # ambiguous
    },
    FailureClass.MANDATE_REVOKED: {
        # Synthesised from subscription state rather than sent as an error, so
        # this one genuinely has no variety.
        "subscription_cancelled": 1.0,
    },
    FailureClass.UNCLASSIFIED: {
        "authentication_failed": 0.30,
        "payment_timed_out": 0.20,
        "incorrect_cvv": 0.15,
        "transaction_limit_exceeded": 0.12,
        "payment_cancelled": 0.10,
        "otp_attempts_exceeded": 0.08,
        "some_reason_razorpay_added_last_tuesday": 0.05,  # never seen before
    },
}

#: Where each reason string is reported from. Razorpay's ``source`` and ``step``
#: are a second, independent signal, and the resolver prompt leans on them.
_SOURCE_STEP: dict[str, tuple[str, str]] = {
    "insufficient_funds": ("bank", "payment_authorization"),
    "funds_blocked_by_mandate": ("bank", "payment_authorization"),
    "card_expired": ("issuer", "payment_authentication"),
    "card_not_enrolled": ("issuer", "payment_authentication"),
    "card_disabled_for_online_payments": ("issuer", "payment_authorization"),
    "debit_instrument_inactive": ("issuer", "payment_authorization"),
    "debit_instrument_blocked": ("issuer", "payment_authorization"),
    "subscription_cancelled": ("business", "payment_initiation"),
    "bank_technical_error": ("bank", "payment_authorization"),
    "issuer_technical_error": ("issuer", "payment_authorization"),
    "bank_not_available": ("bank", "payment_authorization"),
    "bank_cutoff_in_progress": ("bank", "payment_authorization"),
    "gateway_technical_error": ("gateway", "payment_authorization"),
    "payment_declined_due_to_high_traffic": ("gateway", "payment_authorization"),
    "payment_risk_check_failed": ("gateway", "payment_authorization"),
    "compliance_violation": ("gateway", "payment_authorization"),
    "authentication_failed": ("issuer", "payment_authentication"),
    "incorrect_cvv": ("issuer", "payment_authentication"),
    "otp_attempts_exceeded": ("issuer", "payment_authentication"),
    "transaction_limit_exceeded": ("issuer", "payment_authorization"),
    "payment_timed_out": ("gateway", "payment_authorization"),
    "payment_cancelled": ("customer", "payment_authentication"),
}

_AMBIGUOUS_SOURCE_STEP: dict[FailureClass, tuple[str, str]] = {
    # The ambiguous strings carry no cause of their own, so source and step are
    # the only evidence left. They point where the true cause would.
    FailureClass.INSUFFICIENT_FUNDS: ("bank", "payment_authorization"),
    FailureClass.INSTRUMENT_INVALID: ("issuer", "payment_authorization"),
    FailureClass.TRANSIENT_ISSUER: ("gateway", "payment_authorization"),
    FailureClass.RISK_DECLINE: ("gateway", "payment_authorization"),
    FailureClass.MANDATE_REVOKED: ("business", "payment_initiation"),
    FailureClass.UNCLASSIFIED: ("issuer", "payment_authorization"),
}

AMBIGUOUS_STRINGS = frozenset({"card_declined", "payment_failed", "payment_declined", "debit_declined"})


def sample_decline(
    failure_class: FailureClass, rng: random.Random
) -> tuple[str, str, str]:
    """Draw a (reason, source, step) triple this cause could really produce."""
    mix = REASON_MIX[failure_class]
    reason = rng.choices(list(mix), weights=list(mix.values()), k=1)[0]
    if reason in AMBIGUOUS_STRINGS:
        source, step = _AMBIGUOUS_SOURCE_STEP[failure_class]
    else:
        source, step = _SOURCE_STEP.get(reason, ("unknown", "unknown"))
    return reason, source, step


CHARGE_DRAWS = "charge"
CONVERSION_DRAWS = "convert"
PAY_NOW_DRAWS = "paynow"


def subject_stream(
    paired_seed: int, subscription_id: str, purpose: str = CHARGE_DRAWS
) -> random.Random:
    """A random stream belonging to one subject, for one kind of decision.

    Paired comparison needs subject *n* to face identical luck in both arms. A
    single shared stream cannot do that: the arms consume draws in different
    orders and different quantities, so by the tenth subject the two arms are
    comparing different dice. Deriving the stream from
    ``(seed, subscription_id)`` makes the sequence a property of the subject
    rather than of the order the loop happened to visit them.

    Scoping by ``purpose`` is the other half, and the half that was missing.
    With one stream per subject, the treatment arm's conversion roll consumed
    the draw the control arm was about to spend on its first charge, so the two
    arms faced *offset* charge luck for exactly the class where they differ
    most. Separate streams per kind of decision mean a subject's charge
    outcomes are identical across arms no matter what else either arm did to
    it.
    """
    material = f"{paired_seed}:{subscription_id}:{purpose}".encode("utf-8")
    return random.Random(int.from_bytes(hashlib.sha256(material).digest()[:8], "big"))


class ChargeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    succeeded: bool
    error_reason: str = ""
    error_source: str = ""
    error_step: str = ""


class SimSubject(BaseModel):
    """Ground truth for one simulated subject. Mutable: the rail updates it."""

    subscription_id: str
    latent_class: FailureClass
    plan_amount_paise: int = Field(ge=0)
    declined_reason: str
    error_source: str
    error_step: str
    first_failure_at: datetime
    attempts_made: int = 0
    instrument_updated: bool = False
    paid_via_link: bool = False
    instrument_version: int = 0
    """Bumped each time the customer supplies a replacement. Zero means the
    original instrument, the one that already failed."""


class PaymentRail(Protocol):
    def charge(self, subscription_id: str, now: datetime) -> ChargeResult: ...

    def deliver_update_request(self, subscription_id: str, now: datetime) -> bool: ...

    def create_pay_now_link(self, subscription_id: str, now: datetime) -> str | None: ...

    def deliver_pay_now_link(self, subscription_id: str, now: datetime) -> bool: ...


class SimulatedRail:
    def __init__(
        self,
        subjects: dict[str, SimSubject],
        band: Band,
        rng: random.Random,
        paired_seed: int | None = None,
    ) -> None:
        self._subjects = subjects
        self._band = band
        self._rng = rng
        self._paired_seed = paired_seed
        self._streams: dict[tuple[str, str], random.Random] = {}

    def _stream(self, subscription_id: str, purpose: str) -> random.Random:
        if self._paired_seed is None:
            return self._rng
        key = (subscription_id, purpose)
        if key not in self._streams:
            self._streams[key] = subject_stream(self._paired_seed, subscription_id, purpose)
        return self._streams[key]

    def _subject(self, subscription_id: str) -> SimSubject:
        try:
            return self._subjects[subscription_id]
        except KeyError as exc:
            raise KeyError(f"no simulated subject for {subscription_id!r}") from exc

    def charge(self, subscription_id: str, now: datetime) -> ChargeResult:
        subject = self._subject(subscription_id)
        if subject.instrument_updated:
            probability = POST_UPDATE_CHARGE_SUCCESS
        else:
            # ``now`` was accepted and ignored until the timing model existed,
            # which meant a fast retry and a patient one were the same event
            # and the whole "right time" half of the product was invisible.
            hours = max(0.0, (now - subject.first_failure_at).total_seconds() / 3600)
            probability = retry_success_probability(
                subject.latent_class, self._band, subject.attempts_made, hours
            )
        succeeded = self._stream(subscription_id, CHARGE_DRAWS).random() < probability
        subject.attempts_made += 1
        if succeeded:
            return ChargeResult(succeeded=True)
        return ChargeResult(
            succeeded=False,
            error_reason=subject.declined_reason,
            error_source=subject.error_source,
            error_step=subject.error_step,
        )

    def deliver_update_request(self, subscription_id: str, now: datetime) -> bool:
        subject = self._subject(subscription_id)
        if subject.instrument_updated:
            return True
        converted = self._stream(
            subscription_id, CONVERSION_DRAWS
        ).random() < update_conversion_probability(
            self._band
        )
        if converted:
            subject.instrument_updated = True
            subject.instrument_version += 1
        return converted

    def create_pay_now_link(self, subscription_id: str, now: datetime) -> str | None:
        # `.invalid` is a reserved TLD that can never resolve, so a simulated
        # link is impossible to mistake for a real one -- in a log, in a
        # rendered message, or by a reader of the audit export.
        return f"https://example.invalid/pay/{subscription_id}"

    def deliver_pay_now_link(self, subscription_id: str, now: datetime) -> bool:
        subject = self._subject(subscription_id)
        if subject.paid_via_link:
            return True
        paid = self._stream(
            subscription_id, PAY_NOW_DRAWS
        ).random() < pay_now_conversion_probability(self._band)
        if paid:
            subject.paid_via_link = True
        return paid

    def replacement_instrument_id(self, subscription_id: str) -> str | None:
        """Identity of the replacement instrument, or None if none was supplied.

        The policy engine needs an identity rather than a flag: a bounded
        "charges so far" counter owned by the caller can be reset by a stale
        snapshot or a retry loop, and a zero-budget cause then becomes
        unlimited. An identity cannot be reset by accident -- charging twice
        requires naming the same instrument twice, which the engine can see.
        """
        subject = self._subject(subscription_id)
        if subject.instrument_version == 0:
            return None
        return f"{subscription_id}:instrument:v{subject.instrument_version}"
