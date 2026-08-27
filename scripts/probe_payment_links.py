"""Read-then-write probe of the Payment Links API. Creates ONE link with
notify OFF, records the exact response shape, then cancels it.

notify is off, so nothing is delivered to anybody. The link is cancelled
immediately afterwards so the account is left as it was found.

Razorpay rate-limits burst writes on test accounts and answers
"Too many requests" instead of queueing, so both the create and the cancel
retry with exponential backoff rather than giving up on the first brush
with the limiter.
"""
import base64, json, os, time, urllib.request, urllib.error
from recoup.razorpay.config import load_config, load_dotenv

for k, v in load_dotenv().items():
    os.environ.setdefault(k, v)
cfg = load_config()
AUTH = base64.b64encode(f"{cfg.key_id}:{cfg.key_secret}".encode()).decode()

RATE_LIMIT_PAUSE_SECONDS = 3.0
RATE_LIMIT_RETRIES = 4


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"https://api.razorpay.com/v1/{path}", data=data, method=method,
        headers={"Authorization": f"Basic {AUTH}", "User-Agent": "recoup/0.1",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}


def call_with_retry(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    delay = RATE_LIMIT_PAUSE_SECONDS
    for attempt in range(RATE_LIMIT_RETRIES):
        status, payload = call(method, path, body)
        message = str(payload.get("error", ""))
        if status != 429 and "Too many requests" not in message:
            return status, payload
        if attempt == RATE_LIMIT_RETRIES - 1:
            return status, payload
        print(f"  rate limited ({status}), retrying in {delay}s...")
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("unreachable")


status, created = call_with_retry("POST", "payment_links", {
    "amount": 99900,
    "currency": "INR",
    "description": "Recoup probe - not delivered",
    "reference_id": "recoup-probe-001",
    "customer": {"name": "Probe", "email": "probe@example.com", "contact": "+919876543210"},
    "notify": {"sms": False, "email": False},
    "reminder_enable": False,
    "notes": {"purpose": "shape probe"},
})
print("CREATE ->", status)
print(json.dumps(created, indent=2)[:2000])

if status < 300 and created.get("id"):
    cancel_status, cancelled = call_with_retry("POST", f"payment_links/{created['id']}/cancel")
    print("\nCANCEL ->", cancel_status)
    print(json.dumps(cancelled, indent=2)[:2000])
