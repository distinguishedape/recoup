from datetime import datetime, timezone

import pytest

from recoup.classify.taxonomy import AMBIGUOUS_REASONS, TABLE, classify_by_table
from recoup.models.core import FailureEvent
from recoup.models.enums import FailureClass

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def event(reason: str, source: str = "bank", step: str = "payment_authorization") -> FailureEvent:
    return FailureEvent(
        event_id="evt_1",
        subscription_id="sub_1",
        invoice_id="inv_1",
        error_reason=reason,
        error_source=source,
        error_step=step,
        attempt_number=1,
        occurred_at=NOW,
        source="cohort",
    )


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("insufficient_funds", FailureClass.INSUFFICIENT_FUNDS),
        ("card_expired", FailureClass.INSTRUMENT_INVALID),
        ("card_not_enrolled", FailureClass.INSTRUMENT_INVALID),
        ("card_disabled_for_online_payments", FailureClass.INSTRUMENT_INVALID),
        ("debit_instrument_inactive", FailureClass.INSTRUMENT_INVALID),
        ("debit_instrument_blocked", FailureClass.INSTRUMENT_INVALID),
        ("subscription_cancelled", FailureClass.MANDATE_REVOKED),
        ("bank_technical_error", FailureClass.TRANSIENT_ISSUER),
        ("gateway_technical_error", FailureClass.TRANSIENT_ISSUER),
        ("payment_risk_check_failed", FailureClass.RISK_DECLINE),
        ("authentication_failed", FailureClass.UNCLASSIFIED),
        ("incorrect_cvv", FailureClass.UNCLASSIFIED),
        ("transaction_limit_exceeded", FailureClass.UNCLASSIFIED),
        ("payment_timed_out", FailureClass.UNCLASSIFIED),
        ("payment_cancelled", FailureClass.UNCLASSIFIED),
    ],
)
def test_every_spec_reason_string_maps_to_its_spec_class(reason, expected):
    result = classify_by_table(event(reason))
    assert result is not None
    assert result.failure_class is expected
    assert result.method == "table"


def test_the_two_ambiguous_reasons_are_deferred_to_the_llm():
    assert AMBIGUOUS_REASONS == frozenset({"card_declined", "payment_failed"})
    assert classify_by_table(event("card_declined")) is None
    assert classify_by_table(event("payment_failed")) is None


def test_an_unmapped_reason_is_unclassified_not_a_crash():
    result = classify_by_table(event("some_reason_razorpay_invented_last_tuesday"))
    assert result is not None
    assert result.failure_class is FailureClass.UNCLASSIFIED


def test_an_unmapped_reason_is_less_confident_than_a_table_hit():
    mapped = classify_by_table(event("insufficient_funds"))
    unmapped = classify_by_table(event("brand_new_reason"))
    assert unmapped.confidence < mapped.confidence


def test_reason_strings_are_matched_case_and_whitespace_insensitively():
    result = classify_by_table(event("  INSUFFICIENT_FUNDS  "))
    assert result.failure_class is FailureClass.INSUFFICIENT_FUNDS


def test_the_rationale_names_the_reason_string_that_drove_the_decision():
    result = classify_by_table(event("card_expired"))
    assert "card_expired" in result.rationale


def test_the_table_never_maps_anything_to_a_class_outside_the_enum():
    assert set(TABLE.values()) <= set(FailureClass)
