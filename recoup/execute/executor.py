"""The executor: the only thing that touches the outside world.

Its signature is the enforcement point. ``execute`` accepts an
``AuthorizedAction`` and nothing else, and ``AuthorizedAction`` can only
come from the policy engine, so there is no code path -- present or future
-- that charges a card or messages a customer without a recorded verdict.

Every execution costs money and every execution is audited. Those two
facts are what turn a demo into a measurable experiment: net recovered
rupees is gross recovery minus the sum of these costs across every
subject, including the ones that never recovered.
"""

from datetime import datetime
from typing import Protocol

from recoup.audit.log import AuditLog, new_record
from recoup.clock.virtual import VirtualClock
from recoup.execute.messages import RenderedMessage, render
from recoup.execute.rail import PaymentRail
from recoup.models.core import Action, ExecutionResult, Subscription
from recoup.models.enums import ActionType
from recoup.policy.authorized import AuthorizedAction

CHARGE_ATTEMPT_COST_PAISE = 300
"""Rs 3.00 per charge attempt: gateway plus processing. A declared assumption."""

CHANNEL_COST_PAISE: dict[str, int] = {"email": 20, "sms": 25}
"""Rs 0.20 per email, Rs 0.25 per SMS. Declared assumptions."""

UPDATE_LINK_BASE = "https://recoup.example/update"


def render_context_for(subscription: Subscription) -> dict[str, str]:
    rupees = subscription.plan_amount_paise / 100
    return {
        "customer_id": subscription.customer_id,
        "amount_inr": f"{rupees:.2f}",
        "update_link": f"{UPDATE_LINK_BASE}/{subscription.subscription_id}",
    }


def cost_of(action: Action) -> int:
    if action.type is ActionType.RETRY_CHARGE:
        return CHARGE_ATTEMPT_COST_PAISE
    if action.type in {
        ActionType.SEND_MESSAGE,
        ActionType.REQUEST_INSTRUMENT_UPDATE,
        ActionType.PAY_NOW_LINK,
    }:
        return CHANNEL_COST_PAISE.get(action.channel or "", 0)
    return 0


class MessageDispatcher(Protocol):
    def send(
        self,
        subscription_id: str,
        channel: str,
        message: RenderedMessage,
        now: datetime,
    ) -> bool: ...


class SimulatedDispatcher:
    """Records what would have been sent. Delivery always succeeds."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, RenderedMessage]] = []

    def send(
        self,
        subscription_id: str,
        channel: str,
        message: RenderedMessage,
        now: datetime,
    ) -> bool:
        self.sent.append((subscription_id, channel, message))
        return True


class Executor:
    def __init__(
        self,
        rail: PaymentRail,
        dispatcher: MessageDispatcher,
        audit: AuditLog,
        clock: VirtualClock,
    ) -> None:
        self._rail = rail
        self._dispatcher = dispatcher
        self._audit = audit
        self._clock = clock

    def execute(
        self, authorized: AuthorizedAction, render_context: dict[str, str]
    ) -> ExecutionResult:
        if not isinstance(authorized, AuthorizedAction):
            raise TypeError(
                "Executor.execute requires an AuthorizedAction minted by the policy engine"
            )
        action = authorized.action
        now = self._clock.now
        succeeded, detail = self._perform(action, render_context, now)
        result = ExecutionResult(
            action_id=action.action_id,
            subscription_id=action.subscription_id,
            succeeded=succeeded,
            detail=detail,
            cost_paise=cost_of(action),
            occurred_at=now,
        )
        self._audit.append(
            new_record(
                action.subscription_id,
                now,
                "execute",
                {
                    "action_id": action.action_id,
                    "action_type": action.type.value,
                    "tier": int(action.tier),
                    "channel": action.channel,
                    "template_id": action.template_id,
                    "action_reason": action.reason,
                    "verdict_rule": authorized.verdict.rule,
                    "verdict_detail": authorized.verdict.detail,
                    "succeeded": succeeded,
                    "detail": detail,
                    "cost_paise": result.cost_paise,
                },
            )
        )
        return result

    def _perform(
        self, action: Action, render_context: dict[str, str], now: datetime
    ) -> tuple[bool, str]:
        if action.type is ActionType.RETRY_CHARGE:
            charge = self._rail.charge(action.subscription_id, now)
            if charge.succeeded:
                return True, "charge succeeded"
            return False, f"charge declined: {charge.error_reason}"

        if action.type is ActionType.REQUEST_INSTRUMENT_UPDATE:
            message = render(action.template_id or "", render_context)
            delivered = self._dispatcher.send(
                action.subscription_id, action.channel or "", message, now
            )
            if not delivered:
                # The return value used to be discarded here. In simulation the
                # dispatcher always succeeds so it never showed; against a live
                # transport it meant a customer could be recorded as having
                # updated their card in response to a message that was never
                # sent. You cannot convert on a request that did not arrive.
                return False, f"update request {action.template_id} was not delivered"
            converted = self._rail.deliver_update_request(action.subscription_id, now)
            if converted:
                return True, "customer updated their payment instrument"
            return False, "update request delivered, instrument not yet updated"

        if action.type is ActionType.PAY_NOW_LINK:
            # Create first: the message body contains the URL, so rendering
            # before the link exists would always emit a placeholder.
            url = self._rail.create_pay_now_link(action.subscription_id, now)
            if not url:
                return False, "no pay-now link could be created"
            message = render(
                action.template_id or "", {**render_context, "pay_now_url": url}
            )
            delivered = self._dispatcher.send(
                action.subscription_id, action.channel or "", message, now
            )
            if not delivered:
                # Same reasoning as the instrument-update branch: a customer
                # cannot pay a link that never reached them.
                return False, f"pay-now link {action.template_id} was not delivered"
            paid = self._rail.deliver_pay_now_link(action.subscription_id, now)
            if paid:
                return True, "customer paid via the pay-now link"
            return False, "pay-now link delivered, not yet paid"

        if action.type is ActionType.SEND_MESSAGE:
            message = render(action.template_id or "", render_context)
            delivered = self._dispatcher.send(
                action.subscription_id, action.channel or "", message, now
            )
            return delivered, f"message {action.template_id} delivered={delivered}"

        if action.type is ActionType.STOP:
            return True, "recovery stopped for this subject"

        return True, "escalated to manual review"
