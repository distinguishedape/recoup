"""How often the classifier is right, measured against known truth.

Every other test here asks whether a specific string maps to a specific class.
None of them answered the question a reader actually has -- *how often is it
right* -- so the accuracy claim lived in a scratch script and nowhere in the
repo. This is that claim, asserted.

The cohort generator draws reason strings from the full 25-string mix, so a
subject's latent cause and the string the classifier sees are genuinely
different things and the comparison means something. The model runs entirely
from the committed cache, so this needs no API key and cannot drift.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from recoup.classify.engine import classify
from recoup.classify.taxonomy import AMBIGUOUS_REASONS
from recoup.ingest.cohort import CohortSpec, generate_cohort
from recoup.llm.client import LLMClient

COHORT_SIZE = 2000
SEED = 3
START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
CACHE = Path("evidence/llm_cache.json")
CACHED_MODEL = "openai/gpt-oss-120b"
"""The model the committed cache was recorded against.

Cache keys are hashed over the model name, so a client that resolves a
different model misses every entry and silently falls back. Pinning it here
means this test needs no API key and fails loudly if the cache is ever
rebuilt against something else."""


def cached_client() -> LLMClient:
    return LLMClient(cache_path=CACHE, model=CACHED_MODEL, env={})


@pytest.fixture(scope="module")
def cohort():
    return generate_cohort(CohortSpec(size=COHORT_SIZE, seed=SEED), START)


def score(cohort, client) -> tuple[float, float]:
    """Returns (overall accuracy, accuracy on the ambiguous-reason subset)."""
    hits = ambiguous_hits = ambiguous = 0
    for event in cohort.events:
        truth = cohort.subjects[event.subscription_id].latent_class
        correct = classify(event, client).failure_class is truth
        hits += correct
        if event.error_reason in AMBIGUOUS_REASONS:
            ambiguous += 1
            ambiguous_hits += correct
    return hits / len(cohort.events), ambiguous_hits / ambiguous


def test_the_table_alone_is_right_about_five_times_in_six(cohort):
    overall, _ = score(cohort, None)
    assert 0.83 <= overall <= 0.86, overall


def test_the_table_alone_never_resolves_an_ambiguous_reason(cohort):
    # Not a failure. Four reason strings carry no cause, and the table is
    # supposed to refuse them rather than guess -- they land in UNCLASSIFIED
    # at 0.30 confidence, which is the honest answer without a model.
    _, ambiguous = score(cohort, None)
    assert ambiguous == 0.0


def test_the_model_resolves_almost_all_of_what_the_table_refuses(cohort):
    client = cached_client()
    overall, ambiguous = score(cohort, client)
    assert overall >= 0.99, overall
    assert ambiguous >= 0.95, ambiguous


def test_the_model_earns_its_place_in_the_pipeline(cohort):
    """The whole argument for a model being here at all, as a number."""
    without, _ = score(cohort, None)
    with_model, _ = score(cohort, cached_client())
    assert with_model - without >= 0.14, with_model - without


def test_the_residual_error_is_the_generators_doing_not_the_classifiers(cohort):
    """Every miss is RISK_DECLINE read as TRANSIENT_ISSUER, and it is unfixable.

    ``_AMBIGUOUS_SOURCE_STEP`` hands both classes ("gateway",
    "payment_authorization"), so on an ambiguous string the two carry byte-
    identical evidence. No classifier can separate them. If this test starts
    failing, the generator changed -- check that it was changed for a reason
    about Razorpay and not to make this number look better.
    """
    from recoup.execute.rail import _AMBIGUOUS_SOURCE_STEP
    from recoup.models.enums import FailureClass

    assert (
        _AMBIGUOUS_SOURCE_STEP[FailureClass.RISK_DECLINE]
        == _AMBIGUOUS_SOURCE_STEP[FailureClass.TRANSIENT_ISSUER]
    )

    client = cached_client()
    misses = [
        (cohort.subjects[e.subscription_id].latent_class, classify(e, client).failure_class)
        for e in cohort.events
        if classify(e, client).failure_class
        is not cohort.subjects[e.subscription_id].latent_class
    ]
    assert misses
    assert all(
        truth is FailureClass.RISK_DECLINE and got is FailureClass.TRANSIENT_ISSUER
        for truth, got in misses
    ), misses[:5]
