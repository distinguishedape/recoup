"""``AuthorizedAction`` -- permission made into a type.

The executor's signature accepts only this type. It carries a token that
lives in this module's namespace and is never exported, so the only way to
obtain one is to go through the policy engine. An unauthorised action does
not merely fail a check at runtime; it cannot be constructed in the first
place, and a code path that tries to skip the policy engine will not run
even once.

This is deliberately stronger than a boolean flag or a convention. A judge
asking "what stops this from messaging someone at 3am?" gets an answer
that does not depend on anyone remembering to call a function.
"""

from dataclasses import dataclass, field
from typing import Any

from recoup.models.core import Action, PolicyVerdict

_AUTH_TOKEN = object()


@dataclass(frozen=True)
class AuthorizedAction:
    action: Action
    verdict: PolicyVerdict
    token: Any = field(repr=False, default=None)

    def __post_init__(self) -> None:
        if self.token is not _AUTH_TOKEN:
            raise PermissionError(
                "AuthorizedAction may only be constructed by the policy engine"
            )


def mint(action: Action, verdict: PolicyVerdict) -> AuthorizedAction:
    """Internal to ``recoup.policy``. Do not call from anywhere else."""
    if not verdict.allowed:
        raise ValueError("cannot authorize an action on a denying verdict")
    return AuthorizedAction(action=action, verdict=verdict, token=_AUTH_TOKEN)
