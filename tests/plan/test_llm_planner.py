import json
from datetime import datetime, timedelta, timezone

from recoup.llm.client import LLMClient
from recoup.models.core import Classification, FailureEvent
from recoup.models.enums import ActionType, FailureClass
from recoup.execute.messages import ALLOWED_TEMPLATE_IDS
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
                for h in (1, 2, 3, 4, 5, 6)
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


def test_free_text_never_reaches_a_customer(tmp_path):
    # The spec asks for substitution rather than rejection here: send the
    # approved template, keep the model's copy in the record. What must never
    # happen is the model's own words going out, and that is what this asserts.
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
    result = propose_plan(event(), classification(), client_returning(bad, tmp_path), NOW)
    assert result is not None
    assert all(a.free_text is None for a in result.actions)
    assert result.actions[0].template_id in ALLOWED_TEMPLATE_IDS


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


def test_a_plan_that_stops_and_then_acts_is_rejected(tmp_path):
    # The model really produced this: a stop at hour zero followed by retries
    # the next day. Every action was individually permissible, so the policy
    # gate would have run each one; the sequence was still nonsense.
    incoherent = json.dumps(
        {
            "actions": [
                {
                    "type": "retry_charge",
                    "delay_hours": 24,
                    "tier": 1,
                    "channel": None,
                    "template_id": None,
                    "reason": "retry tomorrow",
                },
                {
                    "type": "stop",
                    "delay_hours": 0,
                    "tier": 4,
                    "channel": None,
                    "template_id": None,
                    "reason": "and also stop now",
                },
            ]
        }
    )
    assert propose_plan(event(), classification(), client_returning(incoherent, tmp_path), NOW) is None


def test_a_stop_after_everything_else_is_fine(tmp_path):
    coherent = json.dumps(
        {
            "actions": [
                {
                    "type": "retry_charge",
                    "delay_hours": 24,
                    "tier": 1,
                    "channel": None,
                    "template_id": None,
                    "reason": "retry",
                },
                {
                    "type": "stop",
                    "delay_hours": 48,
                    "tier": 4,
                    "channel": None,
                    "template_id": None,
                    "reason": "then give up",
                },
            ]
        }
    )
    result = propose_plan(event(), classification(), client_returning(coherent, tmp_path), NOW)
    assert result is not None
    assert len(result.actions) == 2


def test_a_plan_of_only_a_stop_is_still_accepted(tmp_path):
    only_stop = json.dumps(
        {
            "actions": [
                {
                    "type": "stop",
                    "delay_hours": 0,
                    "tier": 4,
                    "channel": None,
                    "template_id": None,
                    "reason": "nothing to be done",
                }
            ]
        }
    )
    result = propose_plan(
        event(),
        classification(FailureClass.MANDATE_REVOKED),
        client_returning(only_stop, tmp_path),
        NOW,
    )
    assert result is not None
    assert [a.type for a in result.actions] == [ActionType.STOP]


def _schedule(*delays_hours):
    return json.dumps(
        {
            "actions": [
                {
                    "type": "retry_charge",
                    "delay_hours": h,
                    "tier": 1,
                    "channel": None,
                    "template_id": None,
                    "reason": "retry",
                }
                for h in delays_hours
            ]
        }
    )


def test_a_plan_that_buries_the_remedy_behind_a_notice_is_rejected(tmp_path):
    # The failure this closes, and it cost the whole class. When a card cannot
    # be charged, asking for a different one is the only thing that recovers
    # the payment. The model put a generic notice at tier one and the request
    # at tier two, behind it. Every action was permitted and within budget.
    # Whenever the notice fell outside the contact window the request never
    # ran, and the cause recovered nobody at all: sixty-three recoveries to
    # zero, on the same number of attempts.
    buried = json.dumps(
        {
            "actions": [
                {
                    "type": "send_message",
                    "delay_hours": 0,
                    "tier": 1,
                    "channel": "email",
                    "template_id": "t1_notify_email",
                    "reason": "let them know first",
                },
                {
                    "type": "request_instrument_update",
                    "delay_hours": 2,
                    "tier": 2,
                    "channel": "email",
                    "template_id": "t2_update_instrument_email",
                    "reason": "then ask for a new card",
                },
            ]
        }
    )
    assert propose_plan(
        event(),
        classification(FailureClass.INSTRUMENT_INVALID),
        client_returning(buried, tmp_path),
        NOW,
    ) is None


def test_asking_for_the_new_card_first_is_accepted(tmp_path):
    remedy_first = json.dumps(
        {
            "actions": [
                {
                    "type": "request_instrument_update",
                    "delay_hours": 0,
                    "tier": 2,
                    "channel": "email",
                    "template_id": "t2_update_instrument_email",
                    "reason": "the card cannot be charged, so ask for another",
                },
                {
                    "type": "send_message",
                    "delay_hours": 48,
                    "tier": 3,
                    "channel": "email",
                    "template_id": "t3_final_notice_email",
                    "reason": "final notice",
                },
            ]
        }
    )
    result = propose_plan(
        event(),
        classification(FailureClass.INSTRUMENT_INVALID),
        client_returning(remedy_first, tmp_path),
        NOW,
    )
    assert result is not None
    assert result.actions[0].type is ActionType.REQUEST_INSTRUMENT_UPDATE


def test_a_dead_card_plan_is_scored_on_the_remedy_not_on_retries(tmp_path):
    # Scoring only retries made every plan for this cause tie at zero, so the
    # comparison could not tell a working plan from a broken one.
    from recoup.execute.probabilities import expected_recovery
    from recoup.models.enums import Band

    with_remedy = expected_recovery(FailureClass.INSTRUMENT_INVALID, Band.MID, [], True)
    without = expected_recovery(FailureClass.INSTRUMENT_INVALID, Band.MID, [], False)
    assert with_remedy > without
    assert without == 0.0


def test_a_better_schedule_is_adopted(tmp_path):
    # Upside-only: when the model finds something stronger than the
    # hand-written schedule, it is used.
    from recoup.execute.probabilities import expected_recovery
    from recoup.models.enums import Band
    from recoup.plan.fallback import build_plan as deterministic

    ours = deterministic(event(), classification(FailureClass.TRANSIENT_ISSUER), NOW)
    our_delays = [
        (a.scheduled_at - NOW).total_seconds() / 3600
        for a in ours.actions
        if a.type is ActionType.RETRY_CHARGE
    ]
    better = _schedule(18, 36, 72)
    assert expected_recovery(
        FailureClass.TRANSIENT_ISSUER, Band.MID, [18, 36, 72]
    ) > expected_recovery(FailureClass.TRANSIENT_ISSUER, Band.MID, our_delays)

    result = plan(
        event(),
        classification(FailureClass.TRANSIENT_ISSUER),
        client_returning(better, tmp_path),
        NOW,
    )
    delays = [
        round((a.scheduled_at - NOW).total_seconds() / 3600)
        for a in result.actions
        if a.type is ActionType.RETRY_CHARGE
    ]
    assert delays == [18, 36, 72]


def test_a_plan_with_no_retries_is_not_scored_out_of_existence(tmp_path):
    # A revoked mandate recovers nothing by design, so both schedules score
    # zero and the model's plan is kept rather than discarded on a tie.
    only_stop = json.dumps(
        {
            "actions": [
                {
                    "type": "stop",
                    "delay_hours": 0,
                    "tier": 4,
                    "channel": None,
                    "template_id": None,
                    "reason": "nothing to be done",
                }
            ]
        }
    )
    result = plan(
        event(),
        classification(FailureClass.MANDATE_REVOKED),
        client_returning(only_stop, tmp_path),
        NOW,
    )
    assert [a.type for a in result.actions] == [ActionType.STOP]


def test_free_text_is_replaced_by_the_approved_template_and_kept_in_the_record(tmp_path):
    # Spec scenario 2. Discarding the plan loses a good sequence over one bad
    # sentence; substituting sends approved copy and preserves what the model
    # wanted to say, where a reviewer can see it.
    persuasive = json.dumps({"actions": [{
        "type": "send_message", "delay_hours": 0, "tier": 1, "channel": "email",
        "template_id": "t1_notify_email",
        "free_text": "Your account will be suspended within 24 hours.",
        "reason": "urgency"}]})
    result = propose_plan(event(), classification(), client_returning(persuasive, tmp_path), NOW)
    assert result is not None, "a persuasive message should be substituted, not discarded"
    action = result.actions[0]
    assert action.free_text is None
    assert action.template_id == "t1_notify_email"
    assert "suspended" in action.suppressed_free_text


def test_free_text_with_no_approved_template_to_substitute_is_still_rejected(tmp_path):
    no_template = json.dumps({"actions": [{
        "type": "send_message", "delay_hours": 0, "tier": 1, "channel": "email",
        "template_id": None, "free_text": "pay up", "reason": "no"}]})
    assert propose_plan(event(), classification(), client_returning(no_template, tmp_path), NOW) is None


def test_the_executor_has_no_path_to_the_suppressed_text():
    import pathlib as _p

    source = _p.Path("recoup/execute/executor.py").read_text(encoding="utf-8")
    assert "suppressed_free_text" not in source
    messages = _p.Path("recoup/execute/messages.py").read_text(encoding="utf-8")
    assert "suppressed_free_text" not in messages


def test_the_policy_rule_still_refuses_live_free_text():
    # Substitution happens at planning time. The gate is unchanged: an action
    # that still carries free text when it reaches the engine is denied.
    from recoup.models.enums import Tier
    from recoup.policy.rules import PolicyContext, template_allowlist
    from recoup.models.core import Action as _Action

    live = _Action(
        action_id="a", subscription_id="s", type=ActionType.SEND_MESSAGE,
        scheduled_at=NOW, tier=Tier.T1_NOTIFY, channel="email",
        template_id="t1_notify_email", free_text="pay up", reason="x")
    ctx = PolicyContext(
        now=NOW, failure_class=FailureClass.INSUFFICIENT_FUNDS, contacts_sent=0,
        charge_retries_used=0, opted_out=False, promise_to_pay_until=None,
        last_contact_at=None)
    assert template_allowlist(live, ctx).allowed is False
