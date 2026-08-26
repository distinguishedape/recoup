import json
from datetime import datetime, timezone

from recoup.classify.llm_resolver import RESOLVER_SYSTEM, build_user_prompt, resolve
from recoup.llm.client import LLMClient
from recoup.models.core import FailureEvent
from recoup.models.enums import FailureClass

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def event(reason: str = "card_declined") -> FailureEvent:
    return FailureEvent(
        event_id="evt_1",
        subscription_id="sub_1",
        invoice_id="inv_1",
        error_reason=reason,
        error_source="issuer",
        error_step="payment_authorization",
        attempt_number=2,
        occurred_at=NOW,
        source="cohort",
    )


def client_returning(text: str, tmp_path) -> LLMClient:
    return LLMClient(tmp_path / "cache.json", transport=lambda m, s, u, t: text)


def test_the_prompt_carries_the_signals_the_model_needs(tmp_path):
    prompt = build_user_prompt(event())
    assert "card_declined" in prompt
    assert "issuer" in prompt
    assert "payment_authorization" in prompt
    assert "2" in prompt


def test_the_system_prompt_lists_the_allowed_classes():
    for failure_class in FailureClass:
        assert failure_class.value in RESOLVER_SYSTEM


def test_a_well_formed_answer_is_accepted(tmp_path):
    answer = json.dumps(
        {
            "failure_class": "INSUFFICIENT_FUNDS",
            "confidence": 0.7,
            "rationale": "issuer-sourced generic decline on a recurring debit",
        }
    )
    result = resolve(event(), client_returning(answer, tmp_path))
    assert result.failure_class is FailureClass.INSUFFICIENT_FUNDS
    assert result.method == "llm"
    assert result.confidence == 0.7


def test_json_wrapped_in_prose_or_fences_is_still_read(tmp_path):
    answer = (
        "Sure, here is my analysis:\n```json\n"
        '{"failure_class": "TRANSIENT_ISSUER", "confidence": 0.6, "rationale": "bank side"}'
        "\n```\n"
    )
    result = resolve(event(), client_returning(answer, tmp_path))
    assert result.failure_class is FailureClass.TRANSIENT_ISSUER


def test_an_invented_class_falls_back_instead_of_being_trusted(tmp_path):
    answer = json.dumps(
        {"failure_class": "CUSTOMER_IS_ON_HOLIDAY", "confidence": 0.9, "rationale": "vibes"}
    )
    result = resolve(event(), client_returning(answer, tmp_path))
    assert result.failure_class is FailureClass.UNCLASSIFIED
    assert result.method == "fallback"


def test_unparseable_output_falls_back(tmp_path):
    result = resolve(event(), client_returning("I'm afraid I can't do that", tmp_path))
    assert result.failure_class is FailureClass.UNCLASSIFIED
    assert result.method == "fallback"


def test_an_out_of_range_confidence_is_clamped_not_rejected(tmp_path):
    answer = json.dumps(
        {"failure_class": "RISK_DECLINE", "confidence": 4.2, "rationale": "very sure"}
    )
    result = resolve(event(), client_returning(answer, tmp_path))
    assert result.failure_class is FailureClass.RISK_DECLINE
    assert result.confidence == 1.0


def test_an_unreachable_model_falls_back_rather_than_raising(tmp_path):
    def explode(model, system, user, max_tokens):
        raise ConnectionError("no network")

    client = LLMClient(tmp_path / "cache.json", transport=explode)
    result = resolve(event(), client)
    assert result.failure_class is FailureClass.UNCLASSIFIED
    assert result.method == "fallback"
    assert "unavailable" in result.rationale.lower()
