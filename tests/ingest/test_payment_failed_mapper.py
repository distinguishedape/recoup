"""Mapping a standalone ``payment.failed`` event.

Razorpay gates Subscriptions behind full account activation, so an account that
has not completed KYC answers 401 on ``/v1/plans`` and ``/v1/subscriptions``
while every other product answers 200. Such an account can never emit
``subscription.pending``, but it can emit a real, signed ``payment.failed``
carrying the same error triple the classifier needs. These tests cover that
path so the live ingestion slice is exercisable on the account we actually have.
"""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from recoup.classify.engine import classify
from recoup.ingest.webhook_mapper import (
    MAPPERS,
    UNKNOWN_REASON,
    map_payment_failed,
    map_subscription_pending,
)
from recoup.models.enums import FailureClass

RECEIVED_AT = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent.parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "payment_failed.json").read_text(encoding="utf-8"))


def payload() -> dict:
    return copy.deepcopy(FIXTURE)


def test_both_event_types_are_registered():
    assert MAPPERS["subscription.pending"] is map_subscription_pending
    assert MAPPERS["payment.failed"] is map_payment_failed


def test_the_error_triple_is_carried_through_verbatim():
    event = map_payment_failed(payload(), RECEIVED_AT)
    assert event.error_reason == "card_expired"
    assert event.error_source == "issuer"
    assert event.error_step == "payment_authentication"


def test_the_order_stands_in_as_the_subject():
    # A standalone payment has no subscription, so the order identifies the
    # subject. Downstream never asks which producer a record came from, which
    # is the property that makes this substitution possible at all.
    assert map_payment_failed(payload(), RECEIVED_AT).subscription_id == "order_TEST0001"


def test_a_payment_with_no_order_still_gets_a_stable_subject():
    raw = payload()
    del raw["payload"]["payment"]["entity"]["order_id"]
    event = map_payment_failed(raw, RECEIVED_AT)
    assert event.subscription_id == "pay_subject:pay_FAILED0001"


def test_the_payment_id_identifies_the_event_for_deduplication():
    assert map_payment_failed(payload(), RECEIVED_AT).event_id == "pay_FAILED0001"


def test_it_is_marked_as_coming_from_the_webhook():
    assert map_payment_failed(payload(), RECEIVED_AT).source == "webhook"


def test_the_event_time_uses_the_payloads_created_at():
    event = map_payment_failed(payload(), RECEIVED_AT)
    assert event.occurred_at == datetime.fromtimestamp(1756110000, tz=timezone.utc)


def test_a_payload_with_no_created_at_falls_back_to_receipt_time():
    raw = payload()
    del raw["created_at"]
    assert map_payment_failed(raw, RECEIVED_AT).occurred_at == RECEIVED_AT


def test_a_missing_error_reason_falls_back_rather_than_raising():
    raw = payload()
    del raw["payload"]["payment"]["entity"]["error_reason"]
    assert map_payment_failed(raw, RECEIVED_AT).error_reason == UNKNOWN_REASON


def test_an_empty_payload_does_not_raise():
    event = map_payment_failed({}, RECEIVED_AT)
    assert event.error_reason == UNKNOWN_REASON
    assert event.subscription_id.startswith("pay_subject:")


def test_the_classifier_reads_it_exactly_as_it_reads_a_subscription_failure():
    event = map_payment_failed(payload(), RECEIVED_AT)
    assert classify(event, None).failure_class is FailureClass.INSTRUMENT_INVALID


def test_both_mappers_produce_the_identical_shape():
    subscription_fixture = json.loads(
        (FIXTURES / "subscription_pending.json").read_text(encoding="utf-8")
    )
    from_subscription = map_subscription_pending(subscription_fixture, RECEIVED_AT)
    from_payment = map_payment_failed(payload(), RECEIVED_AT)
    assert set(from_subscription.model_dump()) == set(from_payment.model_dump())
