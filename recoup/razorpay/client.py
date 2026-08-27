"""Read-only Razorpay queries: the ones that let the agent go looking.

``RazorpayTestRail`` can fetch a subscription *by id*, and an id only arrives
because a webhook handed it over. That makes the whole system
downstream-of-notification by construction: it can be told about revenue at
risk, and it cannot find any. The track's problem statement asks for an agent
that *detects*, so this is the missing half.

Everything here is a GET. There is no method that moves money, changes a
subscription or sends anything, and that is deliberate: detection should be
safe to run on a schedule against a live account without anyone having to read
the code to be sure. Writes stay on the rail, where the live-mode guard is.

Stdlib ``urllib`` rather than the ``razorpay`` SDK, matching the transport
style used elsewhere, and with the transport injectable so tests never touch
the network.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Protocol

from recoup.razorpay.config import RazorpayConfig

API_ROOT = "https://api.razorpay.com/v1"
USER_AGENT = "recoup/0.1"
DEFAULT_PAGE = 100
"""Razorpay's list endpoints cap ``count`` at 100."""


class RazorpayUnavailable(RuntimeError):
    """The API could not be reached, or answered with something unusable."""


class Fetch(Protocol):
    def __call__(self, path: str) -> dict[str, Any]: ...


def http_fetch(config: RazorpayConfig) -> Fetch:
    token = base64.b64encode(f"{config.key_id}:{config.key_secret}".encode()).decode()

    def fetch(path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{API_ROOT}/{path}",
            headers={"Authorization": f"Basic {token}", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RazorpayUnavailable(
                f"GET {path} answered {exc.code}: {exc.read().decode()[:200]}"
            ) from exc
        except Exception as exc:
            raise RazorpayUnavailable(f"GET {path} failed: {exc}") from exc

    return fetch


class RazorpayReadClient:
    """List and fetch queries against one Razorpay account."""

    def __init__(self, config: RazorpayConfig, fetch: Fetch | None = None) -> None:
        self._config = config
        self._fetch = fetch or http_fetch(config)

    def _items(self, resource: str, **params: Any) -> list[dict[str, Any]]:
        params.setdefault("count", DEFAULT_PAGE)
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        body = self._fetch(f"{resource}?{query}")
        items = body.get("items")
        return list(items) if isinstance(items, list) else []

    def orders(self, **params: Any) -> list[dict[str, Any]]:
        """Orders, newest first. ``status`` is created / attempted / paid."""
        return self._items("orders", **params)

    def invoices(self, **params: Any) -> list[dict[str, Any]]:
        """Invoices. ``due_by`` plus a non-paid status is an overdue receivable."""
        return self._items("invoices", **params)

    def subscriptions(self, **params: Any) -> list[dict[str, Any]]:
        """Subscriptions. ``pending`` and ``halted`` are the at-risk states."""
        return self._items("subscriptions", **params)

    def payments(self, **params: Any) -> list[dict[str, Any]]:
        return self._items("payments", **params)

    def payment_links(self, **params: Any) -> list[dict[str, Any]]:
        """Payment links. ``status`` is created / partially_paid / paid / cancelled / expired."""
        return self._items("payment_links", **params)

    def payments_for_order(self, order_id: str) -> list[dict[str, Any]]:
        """The attempts made against one order.

        This is what turns an abandoned checkout into a classifiable failure:
        an order the customer tried and failed to pay carries real payments
        with real ``error_reason``, ``error_source`` and ``error_step``, which
        is exactly what the classifier already consumes.
        """
        body = self._fetch(f"orders/{order_id}/payments")
        items = body.get("items")
        return list(items) if isinstance(items, list) else []

    def payment(self, payment_id: str) -> dict[str, Any]:
        return self._fetch(f"payments/{payment_id}")

    def plan(self, plan_id: str) -> dict[str, Any]:
        """One plan. A subscription carries no amount of its own -- it lives
        here, under ``item.amount``."""
        return self._fetch(f"plans/{plan_id}")
