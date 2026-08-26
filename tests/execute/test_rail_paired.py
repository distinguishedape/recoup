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
