"""Creating Razorpay Payment Links -- the one place this project writes.

Kept apart from ``recoup/razorpay/client.py`` on purpose. That module is
GET-only and a test asserts it contains no write verb, because detection runs
on a schedule against a live account and should be safe without anyone reading
the code to be sure. Putting a POST in it would quietly retire that guarantee.

``notify`` defaults to False. Turning it on makes Razorpay send real email and
SMS to a real person; the failure mode of an accidental broadcast is
unrecoverable and the failure mode of an unsent link is a config change.

Field names below (request and response) were confirmed against a live test
account in ``docs/superpowers/plans/payment-link-api-findings.md``: the write
body ``{amount, currency, description, reference_id, customer{name,email,
contact}, notify{sms,email}, reminder_enable, notes}`` is accepted verbatim,
and the response carries ``id``, ``short_url`` and ``status`` exactly as read
below. The one live-only surprise that findings file recorded is that
``customer.contact`` values with repeating-digit runs (like
``+919999999999``) are rejected with a 400 -- not a format issue, a live
validation rule. Any placeholder contact number anywhere in this project
avoids that pattern and uses ``+919876543210`` instead.
"""

import base64
import json
import urllib.error
import urllib.request
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from recoup.razorpay.config import LiveModeRefused, RazorpayConfig

API_ROOT = "https://api.razorpay.com/v1"
USER_AGENT = "recoup/0.1"

Post = Callable[[str, dict[str, Any]], dict[str, Any]]


class PaymentLinkError(RuntimeError):
    """The link could not be created."""


class PaymentLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    short_url: str
    status: str


def _http_post(config: RazorpayConfig) -> Post:
    token = base64.b64encode(f"{config.key_id}:{config.key_secret}".encode()).decode()

    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{API_ROOT}/{path}",
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": f"Basic {token}",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise PaymentLinkError(
                f"POST {path} answered {exc.code}: {exc.read().decode()[:200]}"
            ) from exc
        except Exception as exc:
            raise PaymentLinkError(f"POST {path} failed: {exc}") from exc

    return post


class PaymentLinkWriter:
    """The one write path in this project. Test-mode credentials only.

    Mirrors the ``config.is_test_mode`` guard used by ``RazorpayTestRail``:
    a live-mode key must never reach a class whose job is to create real
    money-collection links.
    """

    def __init__(self, config: RazorpayConfig, post: Post | None = None) -> None:
        if not config.is_test_mode:
            raise LiveModeRefused(
                f"key id {config.key_id!r} is not a test-mode key; "
                "PaymentLinkWriter will not run against live credentials"
            )
        self._config = config
        self._post = post or _http_post(config)

    def create(
        self,
        amount_paise: int,
        reference_id: str,
        description: str,
        customer: dict[str, str],
        notify: bool = False,
        reminder_enable: bool = False,
    ) -> PaymentLink:
        body = {
            "amount": amount_paise,
            "currency": "INR",
            "description": description,
            "reference_id": reference_id,
            "customer": customer,
            "notify": {"sms": notify, "email": notify},
            "reminder_enable": reminder_enable,
            "notes": {"created_by": "recoup"},
        }
        payload = self._post("payment_links", body)
        return PaymentLink(
            id=str(payload["id"]),
            short_url=str(payload["short_url"]),
            status=str(payload.get("status", "created")),
        )

    def cancel(self, link_id: str) -> bool:
        payload = self._post(f"payment_links/{link_id}/cancel", {})
        return str(payload.get("status", "")) == "cancelled"
