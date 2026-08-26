"""The message template allowlist.

Recoup never sends free text. Every customer-facing message is one of
these templates, and the ``template_allowlist`` policy rule refuses any
action that names a template outside this dict or that carries free text.
That is what stops an LLM-authored plan from inventing a threat, a
discount, or a promise the business has not agreed to.

Tier and channel are declared per template so the escalation ladder and
the policy engine can check that a T3 action is not quietly sending a T1
message, or vice versa.
"""

from pydantic import BaseModel, ConfigDict

from recoup.models.enums import Tier


class MessageTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    template_id: str
    tier: Tier
    channel: str
    subject: str
    body: str


class RenderedMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    template_id: str
    channel: str
    subject: str
    body: str


TEMPLATES: dict[str, MessageTemplate] = {
    "t1_notify_email": MessageTemplate(
        template_id="t1_notify_email",
        tier=Tier.T1_NOTIFY,
        channel="email",
        subject="We could not collect your subscription payment",
        body=(
            "Hello,\n\n"
            "We tried to collect Rs {amount_inr} for your subscription and the payment "
            "did not go through. We will try again automatically in a day, so there is "
            "nothing you need to do if funds are available by then.\n\n"
            "Reference: {customer_id}\n"
        ),
    ),
    "t2_update_instrument_email": MessageTemplate(
        template_id="t2_update_instrument_email",
        tier=Tier.T2_REQUEST_ACTION,
        channel="email",
        subject="Please update your payment method",
        body=(
            "Hello,\n\n"
            "Your saved card can no longer be charged, so retrying it will not help. "
            "To keep your subscription active, please add a new payment method here:\n"
            "{update_link}\n\n"
            "The amount due is Rs {amount_inr}.\n"
            "Reference: {customer_id}\n"
        ),
    ),
    "t2_update_instrument_sms": MessageTemplate(
        template_id="t2_update_instrument_sms",
        tier=Tier.T2_REQUEST_ACTION,
        channel="sms",
        subject="",
        body=(
            "Your saved card can no longer be charged. Update your payment method to keep "
            "your subscription active: {update_link} (Rs {amount_inr}, ref {customer_id})"
        ),
    ),
    "t3_final_notice_email": MessageTemplate(
        template_id="t3_final_notice_email",
        tier=Tier.T3_FINAL_NOTICE,
        channel="email",
        subject="Final reminder about your subscription",
        body=(
            "Hello,\n\n"
            "We still have not been able to collect Rs {amount_inr}. This is the last "
            "reminder we will send. If we cannot collect, your subscription will simply "
            "stop at the end of the current period.\n\n"
            "You can update your payment method here: {update_link}\n"
            "Reference: {customer_id}\n"
        ),
    ),
    "t3_final_notice_sms": MessageTemplate(
        template_id="t3_final_notice_sms",
        tier=Tier.T3_FINAL_NOTICE,
        channel="sms",
        subject="",
        body=(
            "Last reminder: we could not collect Rs {amount_inr} for your subscription. "
            "Update your payment method here to continue: {update_link} (ref {customer_id})"
        ),
    ),
}

ALLOWED_TEMPLATE_IDS: frozenset[str] = frozenset(TEMPLATES)


def render(template_id: str, context: dict[str, str]) -> RenderedMessage:
    if template_id not in TEMPLATES:
        raise KeyError(f"template {template_id!r} is not on the allowlist")
    template = TEMPLATES[template_id]
    try:
        body = template.body.format(**context)
        subject = template.subject.format(**context)
    except KeyError as exc:
        raise ValueError(
            f"template {template_id!r} needs context value {exc.args[0]!r}"
        ) from exc
    return RenderedMessage(
        template_id=template_id,
        channel=template.channel,
        subject=subject,
        body=body,
    )
