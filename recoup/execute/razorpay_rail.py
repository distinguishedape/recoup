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

from recoup.execute.rail import ChargeResult
from recoup.razorpay.config import LiveModeRefused, RazorpayConfig

CARD_CHANGE_PARAM = "subscription_card_change=1"


class ManualRetryUnsupported(NotImplementedError):
    """Razorpay offers no manual-retry API; see spike finding F2."""


class SubscriptionResource(Protocol):
    def fetch(self, subscription_id: str) -> dict[str, Any]: ...


class RazorpayClient(Protocol):
    subscription: SubscriptionResource


def build_client(config: RazorpayConfig) -> RazorpayClient:
    import razorpay

    return razorpay.Client(auth=(config.key_id, config.key_secret))


class RazorpayTestRail:
    def __init__(self, client: RazorpayClient, config: RazorpayConfig) -> None:
        if not config.is_test_mode:
            raise LiveModeRefused(
                f"key id {config.key_id!r} is not a test-mode key; "
                "RazorpayTestRail will not run against live credentials"
            )
        self._client = client
        self._config = config

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
