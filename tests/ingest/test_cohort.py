from datetime import datetime, timezone

from recoup.classify.taxonomy import classify_by_table
from recoup.ingest.cohort import CLASS_WEIGHTS, PLAN_AMOUNTS_PAISE, CohortSpec, generate_cohort
from recoup.models.enums import FailureClass

START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def test_the_cohort_is_the_requested_size():
    cohort = generate_cohort(CohortSpec(size=10, seed=1), START)
    assert len(cohort.subscriptions) == 10
    assert len(cohort.events) == 10
    assert len(cohort.subjects) == 10


def test_the_class_weights_sum_to_one():
    assert abs(sum(CLASS_WEIGHTS.values()) - 1.0) < 1e-9


def test_the_same_seed_reproduces_the_identical_cohort():
    first = generate_cohort(CohortSpec(size=25, seed=7), START)
    second = generate_cohort(CohortSpec(size=25, seed=7), START)
    assert first.subscriptions == second.subscriptions
    assert first.events == second.events


def test_a_different_seed_produces_a_different_cohort():
    first = generate_cohort(CohortSpec(size=25, seed=1), START)
    second = generate_cohort(CohortSpec(size=25, seed=2), START)
    assert first.events != second.events


def test_every_event_is_marked_as_coming_from_the_cohort_generator():
    cohort = generate_cohort(CohortSpec(size=10, seed=1), START)
    assert all(event.source == "cohort" for event in cohort.events)


def test_every_subject_has_a_matching_subscription_and_event():
    cohort = generate_cohort(CohortSpec(size=10, seed=1), START)
    ids = {s.subscription_id for s in cohort.subscriptions}
    assert {e.subscription_id for e in cohort.events} == ids
    assert set(cohort.subjects) == ids


def test_a_large_cohort_contains_every_failure_class():
    cohort = generate_cohort(CohortSpec(size=500, seed=3), START)
    seen = {subject.latent_class for subject in cohort.subjects.values()}
    assert seen == set(FailureClass)


def test_an_unambiguous_reason_string_still_identifies_its_cause():
    # Every string except the deliberately ambiguous ones must map back to the
    # cause that produced it, or the cohort is generating nonsense.
    from recoup.execute.rail import AMBIGUOUS_STRINGS

    cohort = generate_cohort(CohortSpec(size=400, seed=5), START)
    for event in cohort.events:
        if event.error_reason in AMBIGUOUS_STRINGS:
            continue
        classified = classify_by_table(event)
        assert classified is not None
        subject = cohort.subjects[event.subscription_id]
        if subject.latent_class is not FailureClass.UNCLASSIFIED:
            assert classified.failure_class is subject.latent_class


def test_a_cause_produces_more_than_one_reason_string():
    # The point of the change. One canonical string per cause made accuracy
    # perfect by construction and measured nothing.
    from collections import defaultdict

    cohort = generate_cohort(CohortSpec(size=1000, seed=5), START)
    by_class = defaultdict(set)
    for event in cohort.events:
        by_class[cohort.subjects[event.subscription_id].latent_class].add(event.error_reason)
    varied = [c for c, reasons in by_class.items() if len(reasons) > 1]
    assert len(varied) >= 4


def test_the_cohort_produces_genuinely_ambiguous_declines():
    # Every real decline observed against a live Razorpay account was
    # ambiguous. A cohort that never generates them cannot test the path built
    # for them.
    from recoup.execute.rail import AMBIGUOUS_STRINGS

    cohort = generate_cohort(CohortSpec(size=1000, seed=5), START)
    ambiguous = [e for e in cohort.events if e.error_reason in AMBIGUOUS_STRINGS]
    assert len(ambiguous) > 50
    assert all(classify_by_table(e) is None for e in ambiguous)


def test_the_table_alone_can_no_longer_be_perfect():
    # If this ever returns to 100% the cohort has stopped testing anything.
    cohort = generate_cohort(CohortSpec(size=1000, seed=5), START)
    correct = sum(
        classify_by_table(e) is not None
        and classify_by_table(e).failure_class
        is cohort.subjects[e.subscription_id].latent_class
        for e in cohort.events
    )
    accuracy = correct / len(cohort.events)
    assert 0.6 < accuracy < 1.0


def test_source_and_step_are_a_second_signal_not_a_constant():
    cohort = generate_cohort(CohortSpec(size=500, seed=5), START)
    assert len({e.error_source for e in cohort.events}) > 2
    assert len({e.error_step for e in cohort.events}) > 1


def test_plan_amounts_are_drawn_from_the_declared_distribution():
    cohort = generate_cohort(CohortSpec(size=100, seed=1), START)
    assert all(s.plan_amount_paise in PLAN_AMOUNTS_PAISE for s in cohort.subscriptions)


def test_first_failures_are_spread_over_time_not_all_at_one_instant():
    cohort = generate_cohort(CohortSpec(size=50, seed=1), START)
    assert len({event.occurred_at for event in cohort.events}) > 1


def test_no_first_failure_predates_the_run_start():
    cohort = generate_cohort(CohortSpec(size=50, seed=1), START)
    assert all(event.occurred_at >= START for event in cohort.events)
