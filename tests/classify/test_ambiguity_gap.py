"""The gap between the reason mix the classifier is measured on and the one
Razorpay actually emits.

The classifier's headline is 84.5% to 99.4%, measured over a cohort drawing
from a 25-string reason mix. On Razorpay's real rails that mix does not exist:
all eight of the documented error-scenario test cards were paid, each confirmed
by ``last4``, and every one came back as the same generic
``payment_failed / gateway`` -- ``evidence/error-card-walk.md``.

So the two findings this project reports separately are one finding. The model's
money contribution does not replicate *because* the evidence it is given carries
no cause, and the cohort has to inject causes precisely because the platform
withholds them. This test pins both halves so neither can drift quietly: the
share of the measured distribution that is ambiguous, and what happens to the
one string production actually sends.
"""

from datetime import datetime, timezone

from recoup.classify.engine import classify
from recoup.classify.taxonomy import classify_by_table
from recoup.ingest.cohort import CohortSpec, generate_cohort
from recoup.models.core import FailureEvent
from recoup.models.enums import FailureClass

COHORT_SIZE = 2000
SEED = 3
START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def razorpay_generic_decline() -> FailureEvent:
    """Exactly what the live rail delivers, for all eight documented scenarios."""
    return FailureEvent(
        event_id="evt_generic",
        subscription_id="sub_generic",
        invoice_id="inv_generic",
        occurred_at=START,
        error_reason="payment_failed",
        error_source="gateway",
        error_step="payment_authorization",
        attempt_number=1,
        source="webhook",
    )


def test_the_table_cannot_resolve_the_only_string_razorpay_sends():
    assert classify_by_table(razorpay_generic_decline()) is None


def test_a_generic_decline_with_no_model_is_unclassified_not_guessed():
    """The failure mode that would matter is a confident wrong answer.

    ``UNCLASSIFIED`` runs the conservative ladder -- notify, retry, offer a link.
    Resolving this string to ``TRANSIENT_ISSUER`` instead would retry silently
    and never ask for a new card, which is the one behaviour this project claims
    it never does. Asserted here so that claim is checked rather than believed.
    """
    classification = classify(razorpay_generic_decline(), None)
    assert classification.failure_class is FailureClass.UNCLASSIFIED


def test_the_measured_distribution_is_far_less_ambiguous_than_production():
    """~15% ambiguous in the cohort against 100% on the real rails.

    The band is wide because the exact share is a property of the cohort mix and
    may legitimately move; what must not happen silently is the mix drifting to
    somewhere the LLM path is barely exercised, or to somewhere it dominates.
    """
    cohort = generate_cohort(CohortSpec(size=COHORT_SIZE, seed=SEED), START)
    ambiguous = sum(1 for event in cohort.events if classify_by_table(event) is None)
    share = ambiguous / len(cohort.events)

    assert 0.10 < share < 0.22, (
        f"{share:.1%} of the measured cohort is ambiguous; the eight-card walk puts "
        "production at 100%, and this test exists to keep that distance visible"
    )
