# A real Razorpay decline through the whole pipeline

## What is real here, and what is not

**Real:** the order, the payment, the decline, and the error data below. All of
it came from a live Razorpay test-mode account, produced by a human paying a
checkout page with a card that fails.

**Not real:** the delivery. Razorpay never sent this to the receiver over HTTP.
The payment was read back from Razorpay's API, the webhook body was assembled
around it, and it was signed locally with the account's webhook secret before
being posted to the receiver.

**What that leaves untested.** The signature was produced by the same HMAC code
that verified it. Self-consistent code passes its own check whether or not it
agrees with Razorpay, so interoperability of the signature scheme is the one
thing this run cannot demonstrate. Everything downstream of the signature --
mapping, classification, planning, audit -- is exercised on genuine Razorpay
error data.

Closing the gap needs the receiver exposed on a public URL and registered in
the dashboard, which is a step nobody has performed yet.

| | |
|---|---|
| Payment | `pay_TUQF4WxWt8SUAa` |
| Order | `order_TUQBQQbvDPMp3A` |
| Amount | Rs 999.00 |
| Status | `failed` |
| `error_reason` | `payment_failed` |
| `error_source` | `gateway` |
| `error_step` | `payment_authorization` |
| `error_description` | Payment failed |

## What the pipeline did with it

**1. Signature verified** against the exact bytes, before parsing anything:
`{'status': 'accepted', 'event': 'payment.failed', 'subscription_id': 'order_TUQBQQbvDPMp3A'}`

A body signed with the wrong secret is refused: HTTP `400`.

**2. Mapped** to the domain model. `subscription_id=order_TUQBQQbvDPMp3A`,
`reason='payment_failed'`, `source='gateway'`, `step='payment_authorization'`,
`source='webhook'`. This is the identical shape the synthetic cohort emits,
and nothing downstream can tell which produced it.

**3. Deterministic table returned `None`** — meaning it
declined to decide. `payment_failed` is one of the few reasons Razorpay
documents as carrying no specific error code, so it is one of the few the table
hands to the model rather than guessing at.

**4. The model answered:** `UNCLASSIFIED`, via `llm`,
confidence `0.9`.

> The generic gateway payment_failed at authorization provides no evidence to identify a specific cause

That is the right answer and the honest one. The prompt instructs the model to
prefer the unclassified bucket over a guess it cannot justify from the evidence,
and a generic gateway failure at authorisation is evidence of nothing in
particular. The bucket is a real class with a real budget, not a failure mode.

**5. Planned:** `retry_charge` at +2h -> `retry_charge` at +24h -> `send_message` at +48h -> `retry_charge` at +72h

**6. Audited:** every step, replayable by subscription id.

## What this run cost the project

Two defects, both found only because this ran against something real:

- The model's first answer was **truncated mid-object** by a token budget sized
  for the visible reply rather than for a model that reasons before writing. The
  guardrail discarded it correctly, but a correct classification was lost to an
  accounting mistake. Budget raised, regression test added.
- Looking up the right test card to produce this decline surfaced that the
  classifier covered fifteen reason strings while Razorpay publishes about a
  hundred — including six issuer-outage reasons that were falling through to the
  slow generic ladder despite being the most recoverable failures in the set.
