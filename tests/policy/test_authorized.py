from datetime import datetime, timezone

import pytest

from recoup.models.core import Action, PolicyVerdict
from recoup.models.enums import ActionType, Tier
from recoup.policy.authorized import AuthorizedAction, mint

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)

ACTION = Action(
    action_id="act_1",
    subscription_id="sub_1",
    type=ActionType.RETRY_CHARGE,
    scheduled_at=NOW,
    tier=Tier.T1_NOTIFY,
    channel=None,
    template_id=None,
    free_text=None,
    reason="test",
)
VERDICT = PolicyVerdict(allowed=True, rule="all_rules_passed", detail="ok")


def test_an_authorized_action_cannot_be_constructed_directly():
    with pytest.raises(PermissionError):
        AuthorizedAction(action=ACTION, verdict=VERDICT, token=object())


def test_an_authorized_action_cannot_be_forged_with_a_none_token():
    with pytest.raises(PermissionError):
        AuthorizedAction(action=ACTION, verdict=VERDICT, token=None)


def test_the_policy_module_can_mint_one():
    authorized = mint(ACTION, VERDICT)
    assert authorized.action is ACTION
    assert authorized.verdict is VERDICT


def test_a_minted_authorization_is_frozen():
    authorized = mint(ACTION, VERDICT)
    with pytest.raises(Exception):
        authorized.action = ACTION


def test_minting_a_denial_is_refused():
    denial = PolicyVerdict(allowed=False, rule="contact_window", detail="22:00 IST")
    with pytest.raises(ValueError):
        mint(ACTION, denial)
