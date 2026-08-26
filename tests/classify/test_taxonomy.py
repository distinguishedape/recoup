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


def test_the_ambiguous_reasons_are_deferred_to_the_llm():
    # Every one of these is a decline whose real cause Razorpay's own docs say
    # is not disclosed to the merchant. They are the only strings that reach
    # the model, and the set stays small on purpose.
    assert {"card_declined", "payment_failed"} <= AMBIGUOUS_REASONS
    for reason in AMBIGUOUS_REASONS:
        assert classify_by_table(event(reason)) is None


def test_the_model_is_asked_about_only_a_handful_of_reasons():
    # If this ever grows large, the deterministic table has stopped doing the
    # work and the model has quietly become load-bearing.
    assert len(AMBIGUOUS_REASONS) <= 6
    assert len(AMBIGUOUS_REASONS) < len(TABLE) / 3


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


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        # Issuer and gateway outages. These carry the highest recovery
        # probability of any class and want a fast retry, so misfiling them as
        # unclassified costs both the recovery and the timing.
        ("issuer_technical_error", FailureClass.TRANSIENT_ISSUER),
        ("bank_not_available", FailureClass.TRANSIENT_ISSUER),
        ("bank_cutoff_in_progress", FailureClass.TRANSIENT_ISSUER),
        ("payment_declined_due_to_high_traffic", FailureClass.TRANSIENT_ISSUER),
        ("server_error", FailureClass.TRANSIENT_ISSUER),
        ("capture_failed", FailureClass.TRANSIENT_ISSUER),
        # The instrument cannot succeed as it stands.
        ("card_type_invalid", FailureClass.INSTRUMENT_INVALID),
        ("card_number_invalid", FailureClass.INSTRUMENT_INVALID),
        # A decision about the transaction.
        ("compliance_violation", FailureClass.RISK_DECLINE),
        # Funds.
        ("funds_blocked_by_mandate", FailureClass.INSUFFICIENT_FUNDS),
        # Real reasons that genuinely do not identify a root cause.
        ("transaction_daily_limit_exceeded", FailureClass.UNCLASSIFIED),
        ("transaction_daily_count_exceeded", FailureClass.UNCLASSIFIED),
        ("otp_attempts_exceeded", FailureClass.UNCLASSIFIED),
    ],
)
def test_reasons_from_razorpays_published_list_are_classified(reason, expected):
    result = classify_by_table(event(reason))
    assert result is not None
    assert result.failure_class is expected


@pytest.mark.parametrize("reason", ["payment_declined", "debit_declined"])
def test_razorpays_other_undisclosed_declines_go_to_the_model(reason):
    # Razorpay's own wording is that the exact reason "is not shared", which is
    # the definition of ambiguous.
    assert classify_by_table(event(reason)) is None


def test_the_issuer_outage_family_is_covered_not_just_the_two_it_started_with():
    outages = {r for r, c in TABLE.items() if c is FailureClass.TRANSIENT_ISSUER}
    assert len(outages) >= 8
    assert {"bank_technical_error", "gateway_technical_error"} <= outages


def test_every_mapped_reason_is_lowercase_snake_case_as_razorpay_sends_them():
    for reason in TABLE:
        assert reason == reason.lower()
        assert " " not in reason
