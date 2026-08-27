"""The real Razorpay rail -- deliberately narrower than the simulated one.

Spike finding F2: Razorpay exposes no manual-retry API for subscription
invoices, and its documentation states that manual charging of a domestic
card is not supported. Retrying a failed auto-debit on demand is simply
not a thing this API can do.

So ``charge`` raises. It would be trivial to have it return a plausible
``ChargeResult`` and let the numbers flow, and that is exactly why it does
not: the moment simulated outcomes can enter through the path labelled
"real", every figure in the report becomes unfalsifiable. The simulated
rail is where recovery outcomes come from, it says so on the tin, and this
class makes it impossible to blur the two.

What this rail *can* do is real and useful: read subscription state, and
produce the hosted card-change link that the instrument-update
intervention depends on (spike finding F7).
"""

from datetime import datetime
from typing import Any, Protocol

from recoup.audit.log import AuditLog, new_record
from recoup.execute.rail import ChargeResult
from recoup.razorpay.config import LiveModeRefused, RazorpayConfig
from recoup.razorpay.payment_links import PaymentLinkWriter

CARD_CHANGE_PARAM = "subscription_card_change=1"


class ManualRetryUnsupported(NotImplementedError):
    """Razorpay offers no manual-retry API; see spike finding F2."""


class SubscriptionResource(Protocol):
    def fetch(self, subscription_id: str) -> dict[str, Any]: ...


class PlanResource(Protocol):
    def fetch(self, plan_id: str) -> dict[str, Any]: ...


class RazorpayClient(Protocol):
    subscription: SubscriptionResource
    plan: PlanResource


def build_client(config: RazorpayConfig) -> RazorpayClient:
    import razorpay

    return razorpay.Client(auth=(config.key_id, config.key_secret))


class RazorpayTestRail:
    def __init__(
        self,
        client: RazorpayClient,
        config: RazorpayConfig,
        links: PaymentLinkWriter | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        if not config.is_test_mode:
            raise LiveModeRefused(
                f"key id {config.key_id!r} is not a test-mode key; "
                "RazorpayTestRail will not run against live credentials"
            )
        self._client = client
        self._config = config
        self._links = links
        self._audit = audit

    def fetch_subscription(self, subscription_id: str) -> dict[str, Any]:
        try:
            entity = self._client.subscription.fetch(subscription_id)
        except Exception:
            return {}
        return entity if isinstance(entity, dict) else {}

    def subscription_state(self, subscription_id: str) -> str:
        status = self.fetch_subscription(subscription_id).get("status")
        return str(status) if status else "unknown"

    def card_change_link(self, subscription_id: str) -> str:
        short_url = self.fetch_subscription(subscription_id).get("short_url")
        if not short_url:
            return ""
        separator = "&" if "?" in str(short_url) else "?"
        return f"{short_url}{separator}{CARD_CHANGE_PARAM}"

    def charge(self, subscription_id: str, now: datetime) -> ChargeResult:
        raise ManualRetryUnsupported(
            "Razorpay exposes no manual-retry API for subscription invoices and does not "
            "support manual charging of domestic cards (spike finding F2). Recovery "
            "outcomes come from SimulatedRail, which is labelled as simulated."
        )

    def deliver_update_request(self, subscription_id: str, now: datetime) -> bool:
        return bool(self.card_change_link(subscription_id))

    def _amount_for(self, subscription_id: str) -> int | None:
        """The amount owed, read via the subscription's plan.

        A real Razorpay subscription entity carries no ``amount`` of its own
        -- only a ``plan_id``. The amount lives on the plan
        (``GET plans/{id}`` -> ``item.amount``), confirmed against the live
        account. Never guessed: a missing ``plan_id``, a failed plan fetch,
        or an absent ``item.amount`` all yield ``None`` rather than a
        stand-in figure that would let a link go out for the wrong sum.
        """
        entity = self.fetch_subscription(subscription_id)
        plan_id = entity.get("plan_id")
        if not plan_id:
            return None
        try:
            plan = self._client.plan.fetch(str(plan_id))
        except Exception:
            return None
        if not isinstance(plan, dict):
            return None
        item = plan.get("item")
        if not isinstance(item, dict):
            return None
        amount = item.get("amount")
        if amount is None:
            return None
        try:
            return int(amount)
        except (TypeError, ValueError):
            return None

    def _customer_for(self, subscription_id: str) -> dict[str, str] | None:
        """The customer contact, read from the subscription entity's flat fields.

        A real Razorpay subscription entity carries ``customer_email`` and
        ``customer_contact`` directly at the top level -- there is no nested
        ``customer`` object and no name field. Never invented: a subscription
        with neither email nor contact yields ``None`` rather than a
        placeholder address nobody can receive mail at, and the name is left
        blank rather than fabricated when nothing better is available.
        """
        entity = self.fetch_subscription(subscription_id)
        email = str(entity.get("customer_email") or "")
        contact = str(entity.get("customer_contact") or "")
        if not email and not contact:
            return None
        notes = entity.get("notes")
        name = ""
        if isinstance(notes, dict):
            name = str(notes.get("name") or notes.get("customer_name") or "")
        if not name:
            name = str(entity.get("customer_id") or "")
        return {
            "name": name,
            "email": email,
            "contact": contact,
        }

    def create_pay_now_link(self, subscription_id: str, now: datetime) -> str | None:
        """Create a real Razorpay Payment Link and return its short URL."""
        if self._links is None:
            return None
        amount = self._amount_for(subscription_id)
        customer = self._customer_for(subscription_id)
        if amount is None or customer is None:
            # Never guess an amount or invent an email address. A link that
            # cannot be built correctly is not built at all.
            if self._audit is not None:
                self._audit.append(
                    new_record(
                        subscription_id,
                        now,
                        "pay_now_link_unavailable",
                        {"missing": "amount" if amount is None else "customer contact"},
                    )
                )
            return None
        link = self._links.create(
            amount_paise=amount,
            reference_id=subscription_id,
            description="Subscription payment",
            customer=customer,
        )
        if self._audit is not None:
            # Spec R-A2: the created link is evidence and must be recoverable
            # from the log, not only from the Razorpay dashboard.
            self._audit.append(
                new_record(
                    subscription_id,
                    now,
                    "pay_now_link_created",
                    {
                        "link_id": link.id,
                        "short_url": link.short_url,
                        "status": link.status,
                        "notified": False,
                    },
                )
            )
        return link.short_url

    def deliver_pay_now_link(self, subscription_id: str, now: datetime) -> bool:
        """Whether the customer paid is not knowable here.

        Always False. Conversion is an event that arrives later, through the
        webhook or the scanner's reconciliation (Task 8). Returning True would
        assert a payment that has not happened, which is the same fabrication
        ``charge()`` raises to prevent.
        """
        return False
