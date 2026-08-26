"""The live Razorpay webhook receiver.

The order of operations in ``receive`` is the security-relevant part:
read the raw bytes, verify the signature against those exact bytes, and
only then parse. Parsing before verifying would mean running a JSON
decoder on unauthenticated input and, worse, verifying a body that is no
longer the one that was signed.

Status codes are chosen for Razorpay's retry behaviour rather than for
REST tidiness. Razorpay retries any non-2xx response, so an event we
deliberately do not handle returns 200 with ``status: ignored`` -- a 404
or a 422 there would produce an endless retry loop for an event that will
never be processable. Only a genuinely malformed or unauthenticated
request gets a 400, because those *should* stop.
"""

import json
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import FastAPI, Request, Response

from recoup.audit.log import AuditLog, new_record
from recoup.ingest.signature import SIGNATURE_HEADER, verify_signature
from recoup.ingest.webhook_mapper import map_subscription_pending
from recoup.models.core import FailureEvent
from recoup.razorpay.config import RazorpayConfig, load_config

SUBSCRIPTION_PENDING_EVENT = "subscription.pending"
WEBHOOK_PATH = "/webhooks/razorpay"

EventSink = Callable[[FailureEvent], None]


def create_app(
    config: RazorpayConfig,
    audit: AuditLog,
    sink: EventSink | None = None,
) -> FastAPI:
    app = FastAPI(title="Recoup webhook receiver")
    seen_event_ids: set[str] = set()

    def _json_response(payload: dict[str, Any], status_code: int = 200) -> Response:
        return Response(
            content=json.dumps(payload),
            status_code=status_code,
            media_type="application/json",
        )

    @app.get("/healthz")
    def healthz() -> Response:
        return _json_response({"status": "ok", "key_id": config.key_id})

    @app.post(WEBHOOK_PATH)
    async def receive(request: Request) -> Response:
        body = await request.body()
        signature = request.headers.get(SIGNATURE_HEADER)

        if not verify_signature(body, signature, config.webhook_secret):
            audit.append(
                new_record(
                    "unknown",
                    datetime.now(timezone.utc),
                    "webhook_rejected",
                    {"reason": "signature verification failed", "body_bytes": len(body)},
                )
            )
            return _json_response({"status": "rejected", "reason": "bad signature"}, 400)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            audit.append(
                new_record(
                    "unknown",
                    datetime.now(timezone.utc),
                    "webhook_rejected",
                    {"reason": "body was not valid JSON", "body_bytes": len(body)},
                )
            )
            return _json_response({"status": "rejected", "reason": "malformed body"}, 400)

        if not isinstance(payload, dict):
            return _json_response({"status": "rejected", "reason": "malformed body"}, 400)

        event_type = str(payload.get("event", ""))
        if event_type != SUBSCRIPTION_PENDING_EVENT:
            return _json_response({"status": "ignored", "event": event_type})

        received_at = datetime.now(timezone.utc)
        event = map_subscription_pending(payload, received_at)

        if event.event_id in seen_event_ids:
            return _json_response(
                {"status": "duplicate", "subscription_id": event.subscription_id}
            )
        seen_event_ids.add(event.event_id)

        audit.append(
            new_record(
                event.subscription_id, event.occurred_at, "ingest", event.model_dump(mode="json")
            )
        )

        if sink is not None:
            sink(event)

        return _json_response({"status": "accepted", "subscription_id": event.subscription_id})

    return app


def _default_app() -> FastAPI:
    """Module-level app so ``uvicorn recoup.ingest.webhook_app:app`` works.

    Built from the environment for the runbook's one-liner. Tests always call
    ``create_app`` directly with explicit dependencies.
    """
    from pathlib import Path

    config = load_config()
    audit = AuditLog(Path("artifacts/audit.db"), Path("artifacts/audit.jsonl"))
    return create_app(config, audit)


class _MisconfiguredApp:
    """Stands in for the app when credentials are absent.

    ``app = None`` made uvicorn fail with an opaque message about a NoneType
    not being callable, which tells an operator nothing about what is actually
    wrong. This raises the original error, naming the missing variables, at the
    moment someone tries to serve it -- while still importing cleanly so the
    test suite can collect this module without credentials present.
    """

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def __call__(self, scope, receive, send):  # pragma: no cover - uvicorn path
        raise RuntimeError(
            f"the webhook receiver is not configured: {self._error}"
        ) from self._error


try:  # pragma: no cover - exercised by uvicorn, not by the tests
    app: object = _default_app()
except Exception as exc:  # credentials absent, e.g. during test collection
    app = _MisconfiguredApp(exc)
