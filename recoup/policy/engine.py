"""The policy gate: the only place an ``AuthorizedAction`` comes from.

Rules run in ``RULES`` order and evaluation stops at the first denial, so
the verdict recorded in the audit log names the specific rule that
blocked the action rather than a generic refusal.
"""

from recoup.models.core import Action, PolicyVerdict
from recoup.policy.authorized import AuthorizedAction, mint
from recoup.policy.rules import RULES, PolicyContext


def authorize(
    action: Action, context: PolicyContext
) -> tuple[AuthorizedAction | None, PolicyVerdict]:
    for rule in RULES:
        verdict = rule(action, context)
        if not verdict.allowed:
            return None, verdict
    verdict = PolicyVerdict(
        allowed=True,
        rule="all_rules_passed",
        detail=f"{len(RULES)} rules evaluated, none blocked this action",
    )
    return mint(action, verdict), verdict
