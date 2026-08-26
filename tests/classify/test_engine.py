import json
from datetime import datetime, timezone

from recoup.classify.engine import classify
from recoup.llm.client import LLMClient
from recoup.models.core import FailureEvent
from recoup.models.enums import FailureClass

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def event(reason: str) -> FailureEvent:
    return FailureEvent(
        event_id="evt_1",
        subscription_id="sub_1",
        invoice_id="inv_1",
        error_reason=reason,
        error_source="issuer",
        error_step="payment_authorization",
        attempt_number=1,
        occurred_at=NOW,
        source="cohort",
    )


def test_an_unambiguous_reason_never_reaches_the_model(tmp_path):
    def explode(model, system, user, max_tokens):
        raise AssertionError("the table should have answered this")

    client = LLMClient(tmp_path / "cache.json", transport=explode)
    result = classify(event("insufficient_funds"), client)
    assert result.failure_class is FailureClass.INSUFFICIENT_FUNDS
    assert result.method == "table"


def test_an_ambiguous_reason_is_escalated_to_the_model(tmp_path):
    answer = json.dumps(
        {"failure_class": "RISK_DECLINE", "confidence": 0.8, "rationale": "gateway block"}
    )
    client = LLMClient(tmp_path / "cache.json", transport=lambda m, s, u, t: answer)
    result = classify(event("card_declined"), client)
    assert result.failure_class is FailureClass.RISK_DECLINE
    assert result.method == "llm"


def test_with_no_client_at_all_an_ambiguous_reason_still_gets_a_class():
    result = classify(event("payment_failed"), None)
    assert result.failure_class is FailureClass.UNCLASSIFIED
    assert result.method == "fallback"


def test_classify_always_returns_a_classification_for_every_reason(tmp_path):
    client = LLMClient(tmp_path / "cache.json", transport=lambda m, s, u, t: "garbage")
    for reason in ["insufficient_funds", "card_declined", "who_knows", "card_expired"]:
        assert classify(event(reason), client) is not None
