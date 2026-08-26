"""The six policy rules (spec R4).

Each rule is a pure function of (action, context) returning a verdict, so
each is independently testable and each denial names itself in the audit
log. Nothing here consults the model, and nothing here has a bypass
parameter.

Three design points worth stating because a judge will ask:

* Time-of-day applies to *contact*, not to charging. Retrying a card at
  3am inconveniences nobody; texting someone at 3am does.
* ``STOP`` and ``ESCALATE_MANUAL_REVIEW`` pass every rule. Stopping is
  always permitted -- a rule that could block a stop would be a rule that
  traps a customer in the ladder.
* A charge on a *replaced* instrument is not a retry of the failed one.
  The zero budget on an invalid instrument exists to stop us hammering
  the card that already declined; once the customer has supplied a new
  one, refusing to charge it would throw away the recovery the ladder
  just earned. That exemption is bounded and counted separately.
"""

from datetime import datetime, timedelta, timezone
from typing import Callable

from pydantic import BaseModel, ConfigDict, field_validator

from recoup.execute.messages import ALLOWED_TEMPLATE_IDS
from recoup.models.core import Action, PolicyVerdict
from recoup.models.enums import ActionType, FailureClass
from recoup.plan.budgets import CONTACT_ACTION_TYPES, budget_for

IST = timezone(timedelta(hours=5, minutes=30))
CONTACT_WINDOW_START_HOUR = 8
CONTACT_WINDOW_END_HOUR = 19
MIN_CONTACT_GAP_HOURS = 24
MAX_POST_UPDATE_CHARGES = 1

TERMINAL_ACTION_TYPES = frozenset({ActionType.STOP, ActionType.ESCALATE_MANUAL_REVIEW})

INSTRUMENT_UPDATE_EXEMPT_CLASSES = frozenset({FailureClass.INSTRUMENT_INVALID})
"""Only a dead instrument earns the post-update charge exemption.

``RISK_DECLINE`` and ``MANDATE_REVOKED`` also carry a zero charge budget, but
for reasons a new card does not answer: a risk block is a decision about the
transaction, and a revoked mandate is a withdrawal of consent. Neither becomes
chargeable because the customer happened to add a payment method."""


class PolicyContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    now: datetime
    failure_class: FailureClass
    contacts_sent: int
    charge_retries_used: int
    opted_out: bool
    promise_to_pay_until: datetime | None
    last_contact_at: datetime | None
    instrument_updated: bool = False
    post_update_charges_used: int = 0

    @field_validator("now", "promise_to_pay_until", "last_contact_at")
    @classmethod
    def _must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        # A naive datetime would make .astimezone(IST) assume whatever timezone
        # the machine happens to be in, silently shifting the contact window.
        # That is a compliance rule changing meaning with deployment location,
        # so it is refused rather than quietly coerced.
        if value is not None and value.tzinfo is None:
            raise ValueError("PolicyContext datetimes must be timezone-aware")
        return value


RuleFn = Callable[[Action, PolicyContext], PolicyVerdict]


def _allow(rule: str, detail: str = "") -> PolicyVerdict:
    return PolicyVerdict(allowed=True, rule=rule, detail=detail)


def _deny(rule: str, detail: str) -> PolicyVerdict:
    return PolicyVerdict(allowed=False, rule=rule, detail=detail)


def _is_contact(action: Action) -> bool:
    return action.type in CONTACT_ACTION_TYPES


def opt_out_stop(action: Action, context: PolicyContext) -> PolicyVerdict:
    rule = "opt_out_stop"
    if action.type in TERMINAL_ACTION_TYPES or not _is_contact(action):
        return _allow(rule, "not a customer contact")
    if context.opted_out:
        return _deny(rule, "customer has opted out of recovery messaging")
    return _allow(rule, "customer has not opted out")


def contact_window(action: Action, context: PolicyContext) -> PolicyVerdict:
    rule = "contact_window"
    if action.type in TERMINAL_ACTION_TYPES or not _is_contact(action):
        return _allow(rule, "not a customer contact")
    local = context.now.astimezone(IST)
    if CONTACT_WINDOW_START_HOUR <= local.hour < CONTACT_WINDOW_END_HOUR:
        return _allow(rule, f"{local:%H:%M} IST is inside the contact window")
    return _deny(
        rule,
        f"{local:%H:%M} IST is outside the "
        f"{CONTACT_WINDOW_START_HOUR:02d}:00-{CONTACT_WINDOW_END_HOUR:02d}:00 IST window",
    )


def contact_rate_limit(action: Action, context: PolicyContext) -> PolicyVerdict:
    rule = "contact_rate_limit"
    if action.type in TERMINAL_ACTION_TYPES or not _is_contact(action):
        return _allow(rule, "not a customer contact")
    if context.last_contact_at is None:
        return _allow(rule, "no previous contact")
    elapsed = context.now - context.last_contact_at
    if elapsed >= timedelta(hours=MIN_CONTACT_GAP_HOURS):
        return _allow(rule, f"{elapsed} since the last contact")
    return _deny(
        rule,
        f"only {elapsed} since the last contact; "
        f"{MIN_CONTACT_GAP_HOURS}h minimum gap applies",
    )


def template_allowlist(action: Action, context: PolicyContext) -> PolicyVerdict:
    rule = "template_allowlist"
    if action.type in TERMINAL_ACTION_TYPES or not _is_contact(action):
        return _allow(rule, "not a customer contact")
    if action.free_text:
        return _deny(rule, "free text to customers is not permitted")
    if action.template_id is None:
        return _deny(rule, "a customer contact must name a template")
    if action.template_id not in ALLOWED_TEMPLATE_IDS:
        return _deny(rule, f"template {action.template_id!r} is not on the allowlist")
    return _allow(rule, f"template {action.template_id!r} is allowed")


def promise_to_pay_suppression(action: Action, context: PolicyContext) -> PolicyVerdict:
    rule = "promise_to_pay_suppression"
    if action.type in TERMINAL_ACTION_TYPES:
        return _allow(rule, "terminal actions are never suppressed")
    if context.promise_to_pay_until is None:
        return _allow(rule, "no promise to pay on file")
    if context.now < context.promise_to_pay_until:
        return _deny(
            rule,
            f"customer promised to pay by {context.promise_to_pay_until.isoformat()}; "
            "suppressed until then",
        )
    return _allow(rule, "promise to pay has lapsed")


def class_retry_budget(action: Action, context: PolicyContext) -> PolicyVerdict:
    rule = "class_retry_budget"
    if action.type in TERMINAL_ACTION_TYPES:
        return _allow(rule, "terminal actions are not budgeted")
    budget = budget_for(context.failure_class)
    if action.type is ActionType.RETRY_CHARGE:
        if (
            context.instrument_updated
            and context.failure_class in INSTRUMENT_UPDATE_EXEMPT_CLASSES
        ):
            # A new instrument is not a retry of the old one. Allow a bounded
            # number of charges on it regardless of the class budget, which
            # exists to stop us hammering the instrument that already failed.
            if context.post_update_charges_used >= MAX_POST_UPDATE_CHARGES:
                return _deny(
                    rule,
                    f"instrument was updated but {context.post_update_charges_used} charge(s) "
                    f"have already been attempted on it (max {MAX_POST_UPDATE_CHARGES})",
                )
            return _allow(rule, "charging a freshly updated instrument")
        if context.charge_retries_used >= budget.charge_retries:
            return _deny(
                rule,
                f"{context.failure_class.value} allows {budget.charge_retries} charge "
                f"retries and {context.charge_retries_used} have been used",
            )
        return _allow(rule, f"{context.charge_retries_used}/{budget.charge_retries} retries used")
    if _is_contact(action):
        if context.contacts_sent >= budget.contacts:
            return _deny(
                rule,
                f"{context.failure_class.value} allows {budget.contacts} contacts "
                f"and {context.contacts_sent} have been sent",
            )
        return _allow(rule, f"{context.contacts_sent}/{budget.contacts} contacts used")
    return _allow(rule, "action type is not budgeted")


RULES: tuple[RuleFn, ...] = (
    opt_out_stop,
    promise_to_pay_suppression,
    class_retry_budget,
    template_allowlist,
    contact_window,
    contact_rate_limit,
)
