import json
from datetime import datetime, timedelta, timezone

from recoup.llm.client import LLMClient
from recoup.models.core import Classification, FailureEvent
from recoup.models.enums import ActionType, FailureClass
from recoup.plan.llm_planner import PLANNER_SYSTEM, plan, propose_plan

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def event() -> FailureEvent:
    return FailureEvent(
        event_id="evt_1",
        subscription_id="sub_1",
        invoice_id="inv_1",
        error_reason="insufficient_funds",
        error_source="bank",
        error_step="payment_authorization",
        attempt_number=1,
        occurred_at=NOW,
        source="cohort",
    )


def classification(failure_class=FailureClass.INSUFFICIENT_FUNDS) -> Classification:
    return Classification(
        failure_class=failure_class, method="table", confidence=0.99, rationale="test"
    )


def client_returning(text: str, tmp_path) -> LLMClient:
    return LLMClient(tmp_path / "cache.json", transport=lambda m, s, u, t: text)


GOOD = json.dumps(
    {
        "actions": [
            {
                "type": "send_message",
                "delay_hours": 0,
                "tier": 1,
                "channel": "email",
                "template_id": "t1_notify_email",
                "reason": "let the customer know before we retry",
            },
            {
                "type": "retry_charge",
                "delay_hours": 30,
                "tier": 1,
                "channel": None,
                "template_id": None,
                "reason": "retry after the likely salary credit",
            },
        ]
    }
)


def test_the_system_prompt_states_the_budget_and_the_allowlist():
    assert "t1_notify_email" in PLANNER_SYSTEM
    assert "budget" in PLANNER_SYSTEM.lower()
    assert "free text" in PLANNER_SYSTEM.lower()


def test_a_valid_proposal_is_turned_into_a_plan(tmp_path):
    result = propose_plan(event(), classification(), client_returning(GOOD, tmp_path), NOW)
    assert result is not None
    assert [a.type for a in result.actions] == [
        ActionType.SEND_MESSAGE,
        ActionType.RETRY_CHARGE,
    ]
    assert result.actions[1].scheduled_at == NOW + timedelta(hours=30)


def test_a_proposal_over_budget_is_rejected_so_the_fallback_is_used(tmp_path):
    greedy = json.dumps(
        {
            "actions": [
                {
                    "type": "retry_charge",
                    "delay_hours": h,
                    "tier": 1,
                    "channel": None,
                    "template_id": None,
                    "reason": "again",
                }
                for h in (1, 2, 3, 4, 5)
            ]
        }
    )
    assert propose_plan(event(), classification(), client_returning(greedy, tmp_path), NOW) is None


def test_a_proposal_naming_a_template_outside_the_allowlist_is_rejected(tmp_path):
    bad = json.dumps(
        {
            "actions": [
                {
                    "type": "send_message",
                    "delay_hours": 0,
                    "tier": 1,
                    "channel": "email",
                    "template_id": "t9_scary_letter",
                    "reason": "no",
                }
            ]
        }
    )
    assert propose_plan(event(), classification(), client_returning(bad, tmp_path), NOW) is None


def test_a_proposal_carrying_free_text_is_rejected(tmp_path):
    bad = json.dumps(
        {
            "actions": [
                {
                    "type": "send_message",
                    "delay_hours": 0,
                    "tier": 1,
                    "channel": "email",
                    "template_id": "t1_notify_email",
                    "free_text": "pay up or else",
                    "reason": "no",
                }
            ]
        }
    )
    assert propose_plan(event(), classification(), client_returning(bad, tmp_path), NOW) is None


def test_an_invented_action_type_is_rejected(tmp_path):
    bad = json.dumps(
        {
            "actions": [
                {
                    "type": "call_the_customers_mother",
                    "delay_hours": 0,
                    "tier": 1,
                    "channel": None,
                    "template_id": None,
                    "reason": "no",
                }
            ]
        }
    )
    assert propose_plan(event(), classification(), client_returning(bad, tmp_path), NOW) is None


def test_an_absurd_delay_is_rejected(tmp_path):
    bad = json.dumps(
        {
            "actions": [
                {
                    "type": "retry_charge",
                    "delay_hours": 9000,
                    "tier": 1,
                    "channel": None,
                    "template_id": None,
                    "reason": "eventually",
                }
            ]
        }
    )
    assert propose_plan(event(), classification(), client_returning(bad, tmp_path), NOW) is None


def test_a_negative_delay_is_rejected(tmp_path):
    bad = json.dumps(
        {
            "actions": [
                {
                    "type": "retry_charge",
                    "delay_hours": -5,
                    "tier": 1,
                    "channel": None,
                    "template_id": None,
                    "reason": "time travel",
                }
            ]
        }
    )
    assert propose_plan(event(), classification(), client_returning(bad, tmp_path), NOW) is None


def test_unparseable_output_is_rejected(tmp_path):
    assert propose_plan(event(), classification(), client_returning("nope", tmp_path), NOW) is None


def test_plan_falls_back_to_the_deterministic_planner_when_the_proposal_is_unusable(tmp_path):
    result = plan(event(), classification(), client_returning("nope", tmp_path), NOW)
    assert [a.type for a in result.actions] == [
        ActionType.SEND_MESSAGE,
        ActionType.RETRY_CHARGE,
        ActionType.RETRY_CHARGE,
    ]


def test_plan_falls_back_when_there_is_no_client_at_all():
    result = plan(event(), classification(), None, NOW)
    assert result.actions


def test_a_zero_budget_class_cannot_be_talked_into_acting(tmp_path):
    greedy = json.dumps(
        {
            "actions": [
                {
                    "type": "retry_charge",
                    "delay_hours": 1,
                    "tier": 1,
                    "channel": None,
                    "template_id": None,
                    "reason": "just once more",
                },
                {
                    "type": "send_message",
                    "delay_hours": 1,
                    "tier": 1,
                    "channel": "email",
                    "template_id": "t1_notify_email",
                    "reason": "just one note",
                },
            ]
        }
    )
    assert (
        propose_plan(
            event(),
            classification(FailureClass.MANDATE_REVOKED),
            client_returning(greedy, tmp_path),
            NOW,
        )
        is None
    )


def test_a_proposal_exactly_on_budget_is_accepted(tmp_path):
    exact = json.dumps(
        {
            "actions": [
                {
                    "type": "retry_charge",
                    "delay_hours": h,
                    "tier": 1,
                    "channel": None,
                    "template_id": None,
                    "reason": "within budget",
                }
                for h in (24, 72)
            ]
        }
    )
    result = propose_plan(event(), classification(), client_returning(exact, tmp_path), NOW)
    assert result is not None
    assert len(result.actions) == 2
