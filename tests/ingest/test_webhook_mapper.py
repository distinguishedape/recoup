import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from recoup.classify.engine import classify
from recoup.ingest.webhook_mapper import UNKNOWN_REASON, map_subscription_pending
from recoup.models.enums import FailureClass

RECEIVED_AT = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)
FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "subscription_pending.json").read_text(
        encoding="utf-8"
    )
)


def payload() -> dict:
    return copy.deepcopy(FIXTURE)


def test_the_identifiers_come_from_the_payload():
    event = map_subscription_pending(payload(), RECEIVED_AT)
    assert event.subscription_id == "sub_TEST0001"
    assert event.invoice_id == "inv_TEST0001"
    assert event.event_id == "pay_TEST0001"


def test_the_error_triple_is_carried_through_verbatim():
    event = map_subscription_pending(payload(), RECEIVED_AT)
    assert event.error_reason == "insufficient_funds"
    assert event.error_source == "bank"
    assert event.error_step == "payment_authorization"


def test_the_event_is_marked_as_coming_from_the_webhook():
    assert map_subscription_pending(payload(), RECEIVED_AT).source == "webhook"


def test_the_attempt_number_follows_the_paid_count():
    assert map_subscription_pending(payload(), RECEIVED_AT).attempt_number == 4


def test_the_event_time_uses_the_payloads_created_at_when_present():
    event = map_subscription_pending(payload(), RECEIVED_AT)
    assert event.occurred_at == datetime.fromtimestamp(1756110000, tz=timezone.utc)


def test_a_payload_with_no_created_at_falls_back_to_the_receipt_time():
    raw = payload()
    del raw["created_at"]
    assert map_subscription_pending(raw, RECEIVED_AT).occurred_at == RECEIVED_AT


def test_a_cancelled_subscription_becomes_the_mandate_revoked_reason():
    raw = payload()
    raw["payload"]["subscription"]["entity"]["status"] = "cancelled"
    event = map_subscription_pending(raw, RECEIVED_AT)
    assert classify(event, None).failure_class is FailureClass.MANDATE_REVOKED


def test_the_subscription_state_beats_the_payment_error():
    raw = payload()
    raw["payload"]["subscription"]["entity"]["status"] = "cancelled"
    raw["payload"]["payment"]["entity"]["error_reason"] = "insufficient_funds"
    assert map_subscription_pending(raw, RECEIVED_AT).error_reason == "subscription_cancelled"


def test_a_payload_with_no_payment_entity_still_produces_an_event():
    raw = payload()
    del raw["payload"]["payment"]
    event = map_subscription_pending(raw, RECEIVED_AT)
    assert event.error_reason == UNKNOWN_REASON
    assert event.subscription_id == "sub_TEST0001"


def test_a_payload_with_no_error_reason_falls_back_rather_than_raising():
    raw = payload()
    del raw["payload"]["payment"]["entity"]["error_reason"]
    event = map_subscription_pending(raw, RECEIVED_AT)
    assert event.error_reason == UNKNOWN_REASON
    assert event.error_source == "bank"


def test_missing_source_and_step_become_unknown_not_empty():
    raw = payload()
    del raw["payload"]["payment"]["entity"]["error_source"]
    del raw["payload"]["payment"]["entity"]["error_step"]
    event = map_subscription_pending(raw, RECEIVED_AT)
    assert event.error_source == "unknown"
    assert event.error_step == "unknown"


def test_a_payload_with_no_invoice_id_uses_a_derived_placeholder():
    raw = payload()
    del raw["payload"]["payment"]["entity"]["invoice_id"]
    assert map_subscription_pending(raw, RECEIVED_AT).invoice_id == "inv_unknown:sub_TEST0001"


def test_an_unmapped_reason_still_classifies_without_error():
    raw = payload()
    raw["payload"]["payment"]["entity"]["error_reason"] = "brand_new_reason_2027"
    event = map_subscription_pending(raw, RECEIVED_AT)
    assert classify(event, None).failure_class is FailureClass.UNCLASSIFIED


def test_a_mapped_webhook_event_is_indistinguishable_from_a_cohort_event_downstream():
    from recoup.ingest.cohort import CohortSpec, generate_cohort

    webhook_event = map_subscription_pending(payload(), RECEIVED_AT)
    cohort_event = generate_cohort(CohortSpec(size=1, seed=1), RECEIVED_AT).events[0]
    assert set(webhook_event.model_dump()) == set(cohort_event.model_dump())
    assert classify(webhook_event, None).method == "table"
