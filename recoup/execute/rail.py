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


CHARGE_DRAWS = "charge"
CONVERSION_DRAWS = "convert"


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
    instrument_version: int = 0
    """Bumped each time the customer supplies a replacement. Zero means the
    original instrument, the one that already failed."""


class PaymentRail(Protocol):
    def charge(self, subscription_id: str, now: datetime) -> ChargeResult: ...

    def deliver_update_request(self, subscription_id: str, now: datetime) -> bool: ...


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
            probability = retry_success_probability(
                subject.latent_class, self._band, subject.attempts_made
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
