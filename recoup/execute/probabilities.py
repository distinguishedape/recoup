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


class TimingProfile(BaseModel):
    """How a cause's recoverability moves with elapsed time since the failure.

    Without this the model could not see the product's central claim. Recovery
    probability depended on attempt count alone, so a retry six hours after an
    outage and a retry a day later were literally the same event. Recoup's
    advantage lives almost entirely in *when* it retries and its cost lives in
    *how many* times, so a model blind to timing measured the cost and none of
    the benefit.

    ``ceiling`` is the multiplier the cause approaches given unlimited time,
    and ``half_life_hours`` is how long it takes to cover half the distance
    from ``floor`` to ``ceiling``. A cause that recovers by waiting has a
    ceiling above one; a cause where waiting achieves nothing has both set to
    one and behaves exactly as before.
    """

    model_config = ConfigDict(frozen=True)

    floor: float
    ceiling: float
    half_life_hours: float


TIMING: dict[FailureClass, TimingProfile] = {
    # A shortfall resolves when money arrives, which for most salaried
    # customers means a pay cycle rather than an hour. Retrying immediately is
    # close to pointless; retrying after a few days is when it pays.
    FailureClass.INSUFFICIENT_FUNDS: TimingProfile(
        floor=0.45, ceiling=1.55, half_life_hours=60.0
    ),
    # An issuer or gateway outage clears on its own and fast. Most of the
    # benefit is available within hours, which is why the fast retry exists.
    FailureClass.TRANSIENT_ISSUER: TimingProfile(
        floor=0.35, ceiling=1.25, half_life_hours=5.0
    ),
    # Waiting does not repair a dead card, undo a revocation, or change a risk
    # decision, and it does not clarify a cause nobody identified. Flat.
    FailureClass.INSTRUMENT_INVALID: TimingProfile(floor=1.0, ceiling=1.0, half_life_hours=1.0),
    FailureClass.MANDATE_REVOKED: TimingProfile(floor=1.0, ceiling=1.0, half_life_hours=1.0),
    FailureClass.RISK_DECLINE: TimingProfile(floor=1.0, ceiling=1.0, half_life_hours=1.0),
    # Unknown cause, so assume a mild version of the commonest one rather than
    # claiming waiting is worthless.
    FailureClass.UNCLASSIFIED: TimingProfile(
        floor=0.70, ceiling=1.20, half_life_hours=48.0
    ),
}


def timing_multiplier(failure_class: FailureClass, hours_since_failure: float) -> float:
    """How much a cause's recoverability has moved by this point in time.

    Saturating exponential: starts at ``floor``, approaches ``ceiling``, half
    the distance covered every ``half_life_hours``. Chosen because both
    mechanisms it represents are saturating -- a salary lands once and an
    outage ends once, and neither keeps improving forever.
    """
    if hours_since_failure < 0:
        raise ValueError(f"hours_since_failure must be >= 0, got {hours_since_failure}")
    profile = TIMING[failure_class]
    progress = 1.0 - 0.5 ** (hours_since_failure / profile.half_life_hours)
    return profile.floor + (profile.ceiling - profile.floor) * progress


class BandProbabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    retry_success: dict[FailureClass, float]
    update_request_conversion: float
    pay_now_conversion: float


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
        pay_now_conversion=0.12,
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
        pay_now_conversion=0.22,
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
        pay_now_conversion=0.34,
    ),
}


def retry_success_probability(
    failure_class: FailureClass,
    band: Band,
    attempt_index: int,
    hours_since_failure: float | None = None,
) -> float:
    """Probability this charge attempt succeeds.

    Two forces, pulling opposite ways. Each successive attempt on the same
    subject is worth less than the last, because a card that just declined is
    likely to decline again. But for causes that heal, waiting makes the next
    attempt worth more.

    ``hours_since_failure`` of ``None`` keeps the old attempt-only behaviour,
    so every caller that has no clock still works and the timing model can be
    turned off for comparison.
    """
    if attempt_index < 0:
        raise ValueError(f"attempt_index must be >= 0, got {attempt_index}")
    base = BANDS[band].retry_success[failure_class]
    probability = base * (RETRY_DECAY**attempt_index)
    if hours_since_failure is not None:
        probability *= timing_multiplier(failure_class, hours_since_failure)
    return max(0.0, min(1.0, probability))


def update_conversion_probability(band: Band) -> float:
    return BANDS[band].update_request_conversion


def pay_now_conversion_probability(band: Band) -> float:
    """How often a customer offered another way to pay actually pays.

    Declared assumption, swept like every other one. Set below
    ``update_request_conversion`` deliberately: a dead card is a *method*
    problem and a new method solves it outright, whereas a shortfall is a
    *money* problem and a different route to pay does not create funds. The
    link helps the subset who have the money somewhere else, which is real but
    smaller."""
    return BANDS[band].pay_now_conversion


def expected_recovery(
    failure_class: FailureClass,
    band: Band,
    retry_delays_hours: list[float],
    asks_for_new_instrument: bool = False,
) -> float:
    """Chance a schedule of retries recovers the payment at least once.

    The point of scoring a schedule rather than counting its attempts is that
    *when* the attempts fall decides most of the outcome, and counting cannot
    see that. Two plans with three retries each are not equivalent: one placed
    to catch a pay cycle and one placed on a flat daily rhythm differ by more
    than the difference between two attempts and three.

    Attempts are treated as independent given their timing, which is a
    simplification -- a card that just declined is correlated with the same
    card declining again -- but the decay factor already carries most of that
    correlation, and the purpose here is to rank two schedules rather than to
    predict a rate.
    """
    surviving = 1.0
    for index, hours in enumerate(retry_delays_hours):
        surviving *= 1.0 - retry_success_probability(failure_class, band, index, hours)
    if asks_for_new_instrument:
        # For a dead card this is the entire remedy, and a schedule of retries
        # is worth nothing beside it. Scoring only retries made every plan for
        # that cause tie at zero, so a comparison could not tell a working plan
        # from a broken one.
        converts = update_conversion_probability(band)
        surviving *= 1.0 - converts * POST_UPDATE_CHARGE_SUCCESS
    return 1.0 - surviving
