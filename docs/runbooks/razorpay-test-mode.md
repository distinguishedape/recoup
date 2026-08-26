# Runbook: firing a real Razorpay failure into Recoup

Everything below happens in Razorpay **test mode**. No real money moves.

## 1. Credentials

Create `.env` (already gitignored) with your test-mode values:

```
RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxxxxxx
RAZORPAY_WEBHOOK_SECRET=choose-any-string
```

`RAZORPAY_WEBHOOK_SECRET` is a value you invent and then enter into the
Dashboard when creating the webhook. Recoup refuses to start on an
`rzp_live_` key — `load_config` raises `LiveModeRefused` before anything
else runs.

## 2. Create a plan and subscription

```bash
python -m scripts.setup_test_subscription --amount-paise 99900
```

Note the `subscription_id` and open the printed authorisation link.

## 3. Authorise the mandate

Open the link and pay with a Razorpay test card. The subscription moves
from `created` to `authenticated`, then `active` at the first successful
charge.

## 4. Expose the receiver and register the webhook

Start the receiver:

```bash
uvicorn recoup.ingest.webhook_app:app --port 8000
```

Expose port 8000 with any tunnel you prefer, then in **Dashboard →
Settings → Webhooks**, add the tunnel URL + `/webhooks/razorpay`, set the
secret to your `RAZORPAY_WEBHOOK_SECRET`, and subscribe to
`subscription.pending`.

Confirm the receiver is up:

```bash
curl http://localhost:8000/healthz
```

It returns the key id and no secrets.

## 5. Fail a charge

In **Dashboard → Subscriptions**, open the subscription and use **Charge
as Failure** on the pending invoice.

Per spike finding F1, test mode offers only success/failure — it cannot
inject a *specific* decline reason, which is why recovery outcomes in the
experiment come from the simulated rail and say so in the report.

## 6. Confirm ingestion

The receiver writes an `ingest` audit record. Check it:

```bash
python -c "from recoup.audit.log import AuditLog; \
log = AuditLog('artifacts/audit.db'); \
print([(r.stage, r.payload.get('error_reason')) for r in log.all()][-5:]); \
log.close()"
```

You should see the `FailureEvent` the mapper produced — the same shape the
synthetic cohort generator emits, which is what lets one pipeline handle
both. Nothing downstream can tell which producer a record came from; the
`source` field exists for the audit trail only.

## What the real rail will and will not do

`RazorpayTestRail` reads subscription state and builds the hosted
card-change link (`subscription_card_change=1`). Its `charge` method
**raises** rather than returning a result.

That is deliberate. Razorpay exposes no manual-retry API for subscription
invoices and states that manual charging of a domestic card is not
supported (spike finding F2). A rail that returned a plausible-looking
`ChargeResult` here would let simulated outcomes enter through the path
labelled "real", and every figure in the report would become
unfalsifiable. There is a test asserting it raises.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| 400 `bad signature` | Dashboard secret differs from `RAZORPAY_WEBHOOK_SECRET` | Re-enter it in the Dashboard; they must match exactly |
| No webhook arrives | Tunnel URL missing `/webhooks/razorpay`, or the event not subscribed | Re-check the webhook config |
| `status: ignored` | A different event type arrived | Expected; only `subscription.pending` is handled, and a 200 is returned so Razorpay stops retrying |
| `status: duplicate` | Razorpay redelivered an event already processed | Expected; the receiver is idempotent on payment id |
| `LiveModeRefused` | A live key is in `.env` | Replace with the `rzp_test_` key |
| Test card stops working after a few days | Test-mode tokens expire in 3 days (spike finding F3) | Re-authorise the subscription |
