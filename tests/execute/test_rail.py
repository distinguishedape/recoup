import random
from datetime import datetime, timezone

import pytest

from recoup.execute.rail import SimSubject, SimulatedRail, canonical_decline
from recoup.models.enums import Band, FailureClass

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def subject(failure_class: FailureClass, sub_id: str = "sub_1") -> SimSubject:
    reason, source, step = canonical_decline(failure_class)
    return SimSubject(
        subscription_id=sub_id,
        latent_class=failure_class,
        plan_amount_paise=99900,
        declined_reason=reason,
        error_source=source,
        error_step=step,
        first_failure_at=NOW,
    )


def rail(*subjects: SimSubject, band: Band = Band.MID, seed: int = 7) -> SimulatedRail:
    return SimulatedRail(
        subjects={s.subscription_id: s for s in subjects},
        band=band,
        rng=random.Random(seed),
    )


def test_canonical_decline_gives_a_real_razorpay_reason_string_per_class():
    assert canonical_decline(FailureClass.INSUFFICIENT_FUNDS)[0] == "insufficient_funds"
    assert canonical_decline(FailureClass.TRANSIENT_ISSUER)[0] == "bank_technical_error"
    assert canonical_decline(FailureClass.RISK_DECLINE)[0] == "payment_risk_check_failed"
    assert canonical_decline(FailureClass.MANDATE_REVOKED)[0] == "subscription_cancelled"


def test_a_mandate_revoked_subject_can_never_be_charged():
    s = subject(FailureClass.MANDATE_REVOKED)
    r = rail(s)
    for _ in range(50):
        assert r.charge("sub_1", NOW).succeeded is False


def test_a_failed_charge_reports_the_subjects_decline_reason():
    s = subject(FailureClass.INSUFFICIENT_FUNDS)
    r = rail(s)
    result = r.charge("sub_1", NOW)
    if not result.succeeded:
        assert result.error_reason == "insufficient_funds"
        assert result.error_step == "payment_authorization"


def test_a_successful_charge_carries_no_error():
    # A fresh rail per seed: charging one rail repeatedly decays the probability
    # toward zero, which would make this assertion depend on the seed rather
    # than on the behaviour under test.
    successes = []
    for seed in range(20):
        s = subject(FailureClass.TRANSIENT_ISSUER, sub_id=f"sub_{seed}")
        result = rail(s, seed=seed).charge(f"sub_{seed}", NOW)
        if result.succeeded:
            successes.append(result)
    assert successes, "a transient-issuer subject should succeed on at least one of 20 seeds"
    assert all(x.error_reason == "" for x in successes)
    assert all(x.error_source == "" and x.error_step == "" for x in successes)


def test_charging_increments_the_subjects_attempt_counter():
    s = subject(FailureClass.INSUFFICIENT_FUNDS)
    r = rail(s)
    r.charge("sub_1", NOW)
    r.charge("sub_1", NOW)
    assert s.attempts_made == 2


def test_the_same_seed_produces_the_same_outcomes():
    def run(seed: int) -> list[bool]:
        s = subject(FailureClass.INSUFFICIENT_FUNDS)
        r = rail(s, seed=seed)
        return [r.charge("sub_1", NOW).succeeded for _ in range(20)]

    assert run(42) == run(42)


def test_different_seeds_produce_different_outcomes():
    def run(seed: int) -> list[bool]:
        s = subject(FailureClass.INSUFFICIENT_FUNDS)
        r = rail(s, seed=seed)
        return [r.charge("sub_1", NOW).succeeded for _ in range(20)]

    assert run(1) != run(2)


def test_the_high_band_recovers_more_often_than_the_low_band():
    def rate(band: Band) -> float:
        wins = 0
        for i in range(400):
            s = subject(FailureClass.INSUFFICIENT_FUNDS, sub_id=f"sub_{i}")
            r = rail(s, band=band, seed=i)
            wins += r.charge(f"sub_{i}", NOW).succeeded
        return wins / 400

    assert rate(Band.HIGH) > rate(Band.LOW)


def test_an_update_request_sometimes_converts_and_flips_the_flag():
    converted = False
    for i in range(200):
        s = subject(FailureClass.INSTRUMENT_INVALID, sub_id=f"sub_{i}")
        r = rail(s, seed=i)
        if r.deliver_update_request(f"sub_{i}", NOW):
            assert s.instrument_updated is True
            converted = True
            break
    assert converted


def test_an_updated_instrument_charges_successfully_almost_always():
    wins = 0
    for i in range(200):
        s = subject(FailureClass.INSTRUMENT_INVALID, sub_id=f"sub_{i}")
        s.instrument_updated = True
        r = rail(s, seed=i)
        wins += r.charge(f"sub_{i}", NOW).succeeded
    assert wins / 200 > 0.85


def test_an_unknown_subscription_is_a_loud_error_not_a_silent_decline():
    r = rail(subject(FailureClass.INSUFFICIENT_FUNDS))
    with pytest.raises(KeyError):
        r.charge("sub_does_not_exist", NOW)


def test_a_pay_now_link_sometimes_converts_and_sometimes_does_not():
    """Twenty fresh rails, because one seed proves nothing either way."""
    outcomes = []
    for seed in range(20):
        s = subject(FailureClass.INSUFFICIENT_FUNDS, sub_id="sub_0000")
        r = rail(s, seed=seed)
        outcomes.append(r.deliver_pay_now_link("sub_0000", NOW))
    assert any(outcomes), "no subject ever paid a link"
    assert not all(outcomes), "every subject paid a link"


def test_a_converted_pay_now_subject_stays_converted():
    s = subject(FailureClass.INSUFFICIENT_FUNDS, sub_id="sub_0000")
    r = rail(s, seed=3)
    first = r.deliver_pay_now_link("sub_0000", NOW)
    if first:
        assert r.deliver_pay_now_link("sub_0000", NOW) is True


def test_a_simulated_pay_now_link_can_never_be_mistaken_for_a_real_one():
    s = subject(FailureClass.INSUFFICIENT_FUNDS, sub_id="sub_0000")
    r = rail(s, seed=3)
    url = r.create_pay_now_link("sub_0000", NOW)
    assert ".invalid" in url


def test_the_pay_now_draw_does_not_consume_the_charge_stream():
    """Purpose-scoped streams: an earlier bug had one arm's conversion roll
    consume the other arm's charge draw, silently unpairing the experiment.

    Paired mode is what actually exercises purpose-scoping (unpaired mode
    routes every draw through one shared stream regardless of purpose), so
    this builds ``SimulatedRail`` directly with a ``paired_seed`` rather than
    through the unpaired ``rail()`` helper above.
    """
    subjects_a = {"sub_0000": subject(FailureClass.INSUFFICIENT_FUNDS, sub_id="sub_0000")}
    a = SimulatedRail(subjects_a, band=Band.MID, rng=random.Random(3), paired_seed=3)
    subjects_b = {"sub_0000": subject(FailureClass.INSUFFICIENT_FUNDS, sub_id="sub_0000")}
    b = SimulatedRail(subjects_b, band=Band.MID, rng=random.Random(3), paired_seed=3)
    a.deliver_pay_now_link("sub_0000", NOW)
    assert a.charge("sub_0000", NOW).succeeded == b.charge("sub_0000", NOW).succeeded
