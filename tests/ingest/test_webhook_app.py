import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recoup.audit.log import AuditLog
from recoup.ingest.signature import SIGNATURE_HEADER, compute_signature
from recoup.ingest.webhook_app import WEBHOOK_PATH, create_app
from recoup.razorpay.config import RazorpayConfig

SECRET = "webhook_value"
FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "subscription_pending.json").read_text(
        encoding="utf-8"
    )
)


def config() -> RazorpayConfig:
    return RazorpayConfig(
        key_id="rzp_test_abc123", key_secret="secret_value", webhook_secret=SECRET
    )


@pytest.fixture()
def harness(tmp_path):
    audit = AuditLog(tmp_path / "audit.db")
    received = []
    app = create_app(config(), audit, sink=received.append)
    with TestClient(app) as client:
        yield client, audit, received
    audit.close()


def post(client, payload: dict, secret: str = SECRET, signature: str | None = None):
    body = json.dumps(payload).encode("utf-8")
    headers = {
        SIGNATURE_HEADER: signature if signature is not None else compute_signature(body, secret),
        "Content-Type": "application/json",
    }
    return client.post(WEBHOOK_PATH, content=body, headers=headers)


def test_a_correctly_signed_event_is_accepted(harness):
    client, _, _ = harness
    response = post(client, FIXTURE)
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["subscription_id"] == "sub_TEST0001"


def test_an_accepted_event_reaches_the_sink_as_a_failure_event(harness):
    client, _, received = harness
    post(client, FIXTURE)
    assert len(received) == 1
    assert received[0].subscription_id == "sub_TEST0001"
    assert received[0].source == "webhook"


def test_an_accepted_event_is_audited(harness):
    client, audit, _ = harness
    post(client, FIXTURE)
    records = audit.reconstruct("sub_TEST0001")
    assert [r.stage for r in records] == ["ingest"]
    assert records[0].payload["error_reason"] == "insufficient_funds"


def test_a_bad_signature_is_rejected(harness):
    client, _, received = harness
    assert post(client, FIXTURE, signature="0" * 64).status_code == 400
    assert received == []


def test_a_signature_from_the_wrong_secret_is_rejected(harness):
    client, _, received = harness
    assert post(client, FIXTURE, secret="attacker").status_code == 400
    assert received == []


def test_a_missing_signature_header_is_rejected(harness):
    client, _, received = harness
    body = json.dumps(FIXTURE).encode("utf-8")
    response = client.post(
        WEBHOOK_PATH, content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert received == []


def test_a_rejected_event_is_audited_and_never_processed(harness):
    client, audit, _ = harness
    post(client, FIXTURE, signature="0" * 64)
    stages = [r.stage for r in audit.all()]
    assert "webhook_rejected" in stages
    assert "ingest" not in stages


def test_a_body_that_is_not_json_is_rejected(harness):
    client, _, received = harness
    body = b"this is not json"
    headers = {SIGNATURE_HEADER: compute_signature(body, SECRET)}
    assert client.post(WEBHOOK_PATH, content=body, headers=headers).status_code == 400
    assert received == []


def test_an_event_type_we_do_not_handle_is_ignored_with_a_two_hundred(harness):
    client, _, received = harness
    other = copy.deepcopy(FIXTURE)
    other["event"] = "payment.captured"
    response = post(client, other)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert received == []


def test_a_replayed_event_is_processed_only_once(harness):
    client, _, received = harness
    post(client, FIXTURE)
    response = post(client, FIXTURE)
    assert response.status_code == 200
    assert response.json()["status"] == "duplicate"
    assert len(received) == 1


def test_the_signature_is_checked_against_the_bytes_razorpay_sent(harness):
    client, _, received = harness
    body = (
        b'{"event":"subscription.pending","payload":{"subscription":{"entity":'
        b'{"id":"sub_X","status":"pending","paid_count":0}}},"created_at":1756110000}'
    )
    headers = {SIGNATURE_HEADER: compute_signature(body, SECRET)}
    response = client.post(WEBHOOK_PATH, content=body, headers=headers)
    assert response.status_code == 200
    assert received[0].subscription_id == "sub_X"


def test_a_sink_that_raises_does_not_lose_the_audit_record(tmp_path):
    audit = AuditLog(tmp_path / "audit2.db")

    def explode(event):
        raise RuntimeError("downstream is down")

    app = create_app(config(), audit, sink=explode)
    with TestClient(app, raise_server_exceptions=False) as client:
        post(client, FIXTURE)
    assert [r.stage for r in audit.all()] == ["ingest"]
    audit.close()


def test_the_health_endpoint_reports_the_key_id_but_no_secret(harness):
    client, _, _ = harness
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["key_id"] == "rzp_test_abc123"
    assert "secret_value" not in response.text
    assert "webhook_value" not in response.text
