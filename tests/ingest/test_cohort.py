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


def test_the_reason_string_on_the_event_reflects_the_latent_class():
    cohort = generate_cohort(CohortSpec(size=200, seed=5), START)
    for event in cohort.events:
        subject = cohort.subjects[event.subscription_id]
        if subject.latent_class is not FailureClass.UNCLASSIFIED:
            classified = classify_by_table(event)
            assert classified is not None
            assert classified.failure_class is subject.latent_class


def test_plan_amounts_are_drawn_from_the_declared_distribution():
    cohort = generate_cohort(CohortSpec(size=100, seed=1), START)
    assert all(s.plan_amount_paise in PLAN_AMOUNTS_PAISE for s in cohort.subscriptions)


def test_first_failures_are_spread_over_time_not_all_at_one_instant():
    cohort = generate_cohort(CohortSpec(size=50, seed=1), START)
    assert len({event.occurred_at for event in cohort.events}) > 1


def test_no_first_failure_predates_the_run_start():
    cohort = generate_cohort(CohortSpec(size=50, seed=1), START)
    assert all(event.occurred_at >= START for event in cohort.events)
