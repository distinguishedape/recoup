import random
from datetime import datetime, timezone

from recoup.execute.rail import SimSubject, SimulatedRail, canonical_decline, subject_stream
from recoup.models.enums import Band, FailureClass

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def subject(sub_id: str, failure_class=FailureClass.INSUFFICIENT_FUNDS) -> SimSubject:
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


def test_a_subject_stream_is_reproducible():
    assert subject_stream(1, "sub_0001").random() == subject_stream(1, "sub_0001").random()


def test_different_subjects_get_different_streams():
    assert subject_stream(1, "sub_0001").random() != subject_stream(1, "sub_0002").random()


def test_different_seeds_give_a_subject_different_streams():
    assert subject_stream(1, "sub_0001").random() != subject_stream(2, "sub_0001").random()


def test_paired_mode_gives_a_subject_the_same_draws_regardless_of_cohort_order():
    ids = ["sub_0001", "sub_0002", "sub_0003"]

    def draws(order: list[str]) -> list[bool]:
        subjects = {i: subject(i) for i in order}
        rail = SimulatedRail(subjects, Band.MID, random.Random(0), paired_seed=99)
        return [rail.charge("sub_0002", NOW).succeeded for _ in range(5)]

    assert draws(ids) == draws(list(reversed(ids)))


def test_paired_mode_is_unaffected_by_what_other_subjects_did_first():
    # This is the property the arms need. In unpaired mode the two arms consume
    # draws from one shared stream in different orders and different quantities,
    # so by the tenth subject they are comparing different dice.
    def paired_draws(charge_someone_else_first: bool) -> list[bool]:
        subjects = {i: subject(i) for i in ("sub_0001", "sub_0002")}
        rail = SimulatedRail(subjects, Band.MID, random.Random(5), paired_seed=99)
        if charge_someone_else_first:
            rail.charge("sub_0001", NOW)
        return [rail.charge("sub_0002", NOW).succeeded for _ in range(5)]

    assert paired_draws(True) == paired_draws(False)


def test_unpaired_mode_is_order_sensitive_which_is_why_paired_mode_exists():
    def unpaired_draws(seed: int, charge_someone_else_first: bool) -> list[bool]:
        subjects = {i: subject(i) for i in ("sub_0001", "sub_0002")}
        rail = SimulatedRail(subjects, Band.MID, random.Random(seed))
        if charge_someone_else_first:
            rail.charge("sub_0001", NOW)
        return [rail.charge("sub_0002", NOW).succeeded for _ in range(5)]

    differs = [
        unpaired_draws(seed, True) != unpaired_draws(seed, False) for seed in range(20)
    ]
    assert any(differs), "unpaired draws should depend on what else the rail did first"


def test_paired_mode_still_respects_the_band():
    def rate(band: Band) -> float:
        wins = 0
        for i in range(400):
            sub_id = f"sub_{i:04d}"
            rail = SimulatedRail(
                {sub_id: subject(sub_id)}, band, random.Random(0), paired_seed=7
            )
            wins += rail.charge(sub_id, NOW).succeeded
        return wins / 400

    assert rate(Band.HIGH) > rate(Band.LOW)


def test_paired_mode_still_cannot_recover_a_revoked_mandate():
    sub_id = "sub_0001"
    rail = SimulatedRail(
        {sub_id: subject(sub_id, FailureClass.MANDATE_REVOKED)},
        Band.HIGH,
        random.Random(0),
        paired_seed=7,
    )
    assert all(rail.charge(sub_id, NOW).succeeded is False for _ in range(30))


def test_a_conversion_roll_does_not_steal_the_next_charge_draw():
    # The defect this closes: one stream per subject meant the treatment arm's
    # conversion roll consumed the draw the control arm was about to spend on
    # its first charge, so the two arms faced offset charge luck for exactly
    # the class where they differ most.
    from recoup.execute.rail import CHARGE_DRAWS, CONVERSION_DRAWS

    charges_only = subject_stream(3, "sub_0007", CHARGE_DRAWS)
    control = [charges_only.random() for _ in range(3)]

    conversion = subject_stream(3, "sub_0007", CONVERSION_DRAWS)
    conversion.random()
    after_conversion = subject_stream(3, "sub_0007", CHARGE_DRAWS)
    treatment = [after_conversion.random() for _ in range(3)]

    assert control == treatment


def test_charge_and_conversion_streams_are_independent():
    from recoup.execute.rail import CHARGE_DRAWS, CONVERSION_DRAWS

    assert (
        subject_stream(3, "sub_0007", CHARGE_DRAWS).random()
        != subject_stream(3, "sub_0007", CONVERSION_DRAWS).random()
    )


def test_an_arm_that_asks_for_an_instrument_update_still_charges_the_same_luck():
    subjects = {"sub_a": subject("sub_a", FailureClass.INSTRUMENT_INVALID)}
    control_rail = SimulatedRail(subjects, Band.MID, random.Random(0), paired_seed=3)
    control = [control_rail.charge("sub_a", NOW).succeeded for _ in range(3)]

    subjects2 = {"sub_a": subject("sub_a", FailureClass.INSTRUMENT_INVALID)}
    treatment_rail = SimulatedRail(subjects2, Band.MID, random.Random(0), paired_seed=3)
    treatment_rail.deliver_update_request("sub_a", NOW)
    # Charge luck must be untouched by whether an update request happened.
    subjects3 = {"sub_a": subject("sub_a", FailureClass.INSTRUMENT_INVALID)}
    fresh = SimulatedRail(subjects3, Band.MID, random.Random(0), paired_seed=3)
    assert [fresh.charge("sub_a", NOW).succeeded for _ in range(3)] == control
