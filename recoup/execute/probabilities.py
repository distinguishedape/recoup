"""The recovery probability model and its sensitivity bands (spec section 7).

Recoup cannot force real declines in Razorpay test mode, so recovery
outcomes are simulated. The honest way to do that is to publish the
numbers, source them, and run the whole experiment at three settings.

Low / Mid / High are the sensitivity sweep. The reporting rule is stated
in the spec and enforced in the experiment harness: a lift that survives
only at the High band is reported as not surviving.

Values are taken verbatim from the spec's band table, which cites public
dunning benchmarks. They are assumptions, not measurements, and the
generated report says so.
"""

from pydantic import BaseModel, ConfigDict

from recoup.models.enums import Band, FailureClass

RETRY_DECAY = 0.6
"""Each successive retry on the same subject is worth this fraction of the
previous one. A second attempt at a card that just declined is not as good
as the first; modelling it as equal would inflate Recoup's measured lift."""

POST_UPDATE_CHARGE_SUCCESS = 0.95
"""Once a customer has actually updated their instrument, the charge almost
always goes through -- the root cause is gone."""


class BandProbabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    retry_success: dict[FailureClass, float]
    update_request_conversion: float


BANDS: dict[Band, BandProbabilities] = {
    Band.LOW: BandProbabilities(
        retry_success={
            FailureClass.INSUFFICIENT_FUNDS: 0.30,
            FailureClass.INSTRUMENT_INVALID: 0.0,
            FailureClass.MANDATE_REVOKED: 0.0,
            FailureClass.TRANSIENT_ISSUER: 0.55,
            FailureClass.RISK_DECLINE: 0.0,
            FailureClass.UNCLASSIFIED: 0.20,
        },
        update_request_conversion=0.20,
    ),
    Band.MID: BandProbabilities(
        retry_success={
            FailureClass.INSUFFICIENT_FUNDS: 0.45,
            FailureClass.INSTRUMENT_INVALID: 0.01,
            FailureClass.MANDATE_REVOKED: 0.0,
            FailureClass.TRANSIENT_ISSUER: 0.70,
            FailureClass.RISK_DECLINE: 0.015,
            FailureClass.UNCLASSIFIED: 0.30,
        },
        update_request_conversion=0.35,
    ),
    Band.HIGH: BandProbabilities(
        retry_success={
            FailureClass.INSUFFICIENT_FUNDS: 0.60,
            FailureClass.INSTRUMENT_INVALID: 0.02,
            FailureClass.MANDATE_REVOKED: 0.0,
            FailureClass.TRANSIENT_ISSUER: 0.80,
            FailureClass.RISK_DECLINE: 0.03,
            FailureClass.UNCLASSIFIED: 0.40,
        },
        update_request_conversion=0.50,
    ),
}


def retry_success_probability(
    failure_class: FailureClass, band: Band, attempt_index: int
) -> float:
    if attempt_index < 0:
        raise ValueError(f"attempt_index must be >= 0, got {attempt_index}")
    base = BANDS[band].retry_success[failure_class]
    return max(0.0, base * (RETRY_DECAY**attempt_index))


def update_conversion_probability(band: Band) -> float:
    return BANDS[band].update_request_conversion
