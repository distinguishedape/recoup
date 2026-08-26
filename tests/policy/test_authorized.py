"""The non-bypassability claim, tested as an attack rather than asserted.

A code review of this module found two routes that defeated an earlier
version: a public ``mint()`` helper that turned any self-made verdict into
an authorisation, and ``dataclasses.replace``, which rebuilt the object
while copying the private token across by identity. Both are covered here
so they cannot come back.
"""

import copy
import dataclasses
from datetime import datetime, timezone

import pytest

from recoup.models.core import Action, PolicyVerdict
from recoup.models.enums import ActionType, FailureClass, Tier
from recoup.policy import authorized as authorized_module
from recoup.policy.authorized import AuthorizedAction
from recoup.policy.engine import authorize
from recoup.policy.rules import IST, PolicyContext

NOW = datetime(2026, 8, 25, 10, 0, tzinfo=IST).astimezone(timezone.utc)

BENIGN = Action(
    action_id="act_1",
    subscription_id="sub_1",
    type=ActionType.RETRY_CHARGE,
    scheduled_at=NOW,
    tier=Tier.T1_NOTIFY,
    channel=None,
    template_id=None,
    free_text=None,
    reason="a charge the rules do allow",
)

FORBIDDEN = Action(
    action_id="act_evil",
    subscription_id="sub_1",
    type=ActionType.SEND_MESSAGE,
    scheduled_at=NOW,
    tier=Tier.T3_FINAL_NOTICE,
    channel="sms",
    template_id="t3_final_notice_sms",
    free_text="pay up or else",
    reason="free text at a forbidden hour",
)

ALLOWING_VERDICT = PolicyVerdict(allowed=True, rule="all_rules_passed", detail="ok")


def permissive_context() -> PolicyContext:
    return PolicyContext(
        now=NOW,
        failure_class=FailureClass.INSUFFICIENT_FUNDS,
        contacts_sent=0,
        charge_retries_used=0,
        opted_out=False,
        promise_to_pay_until=None,
        last_contact_at=None,
    )


def genuine_authorization() -> AuthorizedAction:
    authorized, _ = authorize(BENIGN, permissive_context())
    assert authorized is not None
    return authorized


def test_the_policy_engine_can_produce_one():
    authorized = genuine_authorization()
    assert authorized.action is BENIGN
    assert authorized.verdict.rule == "all_rules_passed"


def test_the_constructor_always_refuses():
    with pytest.raises(PermissionError):
        AuthorizedAction(action=FORBIDDEN, verdict=ALLOWING_VERDICT)


def test_the_constructor_refuses_positional_arguments_too():
    with pytest.raises(PermissionError):
        AuthorizedAction(FORBIDDEN, ALLOWING_VERDICT)


def test_the_constructor_refuses_with_no_arguments():
    with pytest.raises(PermissionError):
        AuthorizedAction()


def test_dataclasses_replace_cannot_swap_the_action_of_a_real_authorization():
    # The route a reviewer found: replace() rebuilds through __init__ and used
    # to carry the private token across, producing a valid authorisation for an
    # action no rule ever saw. This is the one that could happen by accident --
    # replacing a field of an immutable object is ordinary Python.
    real = genuine_authorization()
    with pytest.raises(Exception) as excinfo:
        dataclasses.replace(real, action=FORBIDDEN)
    assert not isinstance(excinfo.value, AssertionError)


def test_there_is_no_public_helper_that_authorizes_an_arbitrary_verdict():
    # An earlier version exported mint(), so anyone who wrote their own
    # PolicyVerdict(allowed=True) got an authorisation with no rule running.
    assert not hasattr(authorized_module, "mint")
    public = [name for name in vars(authorized_module) if not name.startswith("_")]
    assert "AuthorizedAction" in public
    assert not any(
        name.lower() in {"mint", "construct", "make", "build", "create"} for name in public
    )


def test_a_real_authorization_cannot_be_mutated():
    real = genuine_authorization()
    with pytest.raises(Exception):
        real.action = FORBIDDEN


def test_copying_an_authorization_does_not_let_it_be_repointed():
    real = genuine_authorization()
    duplicate = copy.deepcopy(real)
    assert duplicate.action == real.action
    with pytest.raises(Exception):
        duplicate.action = FORBIDDEN


def test_a_denying_verdict_can_never_become_an_authorization():
    denial = PolicyVerdict(allowed=False, rule="contact_window", detail="03:00 IST")
    with pytest.raises(ValueError):
        authorized_module._construct(BENIGN, denial)


def test_the_denied_path_returns_nothing_to_execute():
    night = PolicyContext(
        now=datetime(2026, 8, 25, 3, 0, tzinfo=IST).astimezone(timezone.utc),
        failure_class=FailureClass.INSUFFICIENT_FUNDS,
        contacts_sent=0,
        charge_retries_used=0,
        opted_out=False,
        promise_to_pay_until=None,
        last_contact_at=None,
    )
    authorized, verdict = authorize(FORBIDDEN, night)
    assert authorized is None
    assert verdict.allowed is False
