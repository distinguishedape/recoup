import pytest

from recoup.execute.probabilities import (
    BANDS,
    POST_UPDATE_CHARGE_SUCCESS,
    retry_success_probability,
    update_conversion_probability,
)
from recoup.models.enums import Band, FailureClass


def test_every_band_scores_every_failure_class():
    for band in Band:
        assert set(BANDS[band].retry_success) == set(FailureClass)


def test_spec_mid_band_values_are_reproduced_exactly():
    mid = BANDS[Band.MID].retry_success
    assert mid[FailureClass.INSUFFICIENT_FUNDS] == 0.45
    assert mid[FailureClass.INSTRUMENT_INVALID] == 0.01
    assert mid[FailureClass.MANDATE_REVOKED] == 0.0
    assert mid[FailureClass.TRANSIENT_ISSUER] == 0.70
    assert mid[FailureClass.RISK_DECLINE] == 0.015
    assert mid[FailureClass.UNCLASSIFIED] == 0.30


def test_bands_are_ordered_low_below_mid_below_high():
    for failure_class in FailureClass:
        low = BANDS[Band.LOW].retry_success[failure_class]
        mid = BANDS[Band.MID].retry_success[failure_class]
        high = BANDS[Band.HIGH].retry_success[failure_class]
        assert low <= mid <= high


def test_mandate_revoked_is_hopeless_in_every_band():
    for band in Band:
        assert BANDS[band].retry_success[FailureClass.MANDATE_REVOKED] == 0.0


def test_update_conversion_matches_the_spec_bands():
    assert update_conversion_probability(Band.LOW) == 0.20
    assert update_conversion_probability(Band.MID) == 0.35
    assert update_conversion_probability(Band.HIGH) == 0.50


def test_later_retries_are_worth_less_than_the_first():
    first = retry_success_probability(FailureClass.INSUFFICIENT_FUNDS, Band.MID, 0)
    second = retry_success_probability(FailureClass.INSUFFICIENT_FUNDS, Band.MID, 1)
    third = retry_success_probability(FailureClass.INSUFFICIENT_FUNDS, Band.MID, 2)
    assert first == 0.45
    assert second < first
    assert third < second


def test_decay_cannot_push_a_probability_below_zero():
    assert retry_success_probability(FailureClass.INSUFFICIENT_FUNDS, Band.MID, 50) >= 0.0


def test_a_hopeless_class_stays_hopeless_under_decay():
    assert retry_success_probability(FailureClass.MANDATE_REVOKED, Band.HIGH, 0) == 0.0


def test_a_negative_attempt_index_is_rejected():
    with pytest.raises(ValueError):
        retry_success_probability(FailureClass.INSUFFICIENT_FUNDS, Band.MID, -1)


def test_a_fresh_instrument_is_very_likely_to_charge():
    assert 0.9 <= POST_UPDATE_CHARGE_SUCCESS <= 1.0
