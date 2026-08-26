# A real Razorpay webhook, delivered and verified

Razorpay sent this. Not a fixture, not a replay, not a body assembled locally
and signed with our own key: a genuine `payment.failed` event, signed by
Razorpay, delivered over the public internet to the receiver, and accepted.

| | |
|---|---|
| Payment | `pay_TUSchJ2f00m441` |
| Order | `order_TUSZGhseMiUhPT` |
| `error_reason` | `payment_failed` |
| `error_source` | `gateway` |
| `error_step` | `payment_authorization` |
| `source` | `webhook` |
| Received | 2026-08-26T16:15:31Z |

## Why this run and not the earlier one

An earlier version of this file described a payment read back from Razorpay's
API, assembled into a webhook body, and signed locally before being posted to
the receiver. Everything downstream of the signature was genuinely exercised,
but the signature itself was produced by the same code that verified it.
Self-consistent code passes its own check whether or not it agrees with the
sender, so interoperability was the one thing that run could not show.

It is shown now. **Razorpay computed the signature and our code accepted it.**

## What the pipeline did

**1. Verified the signature** against the exact bytes Razorpay sent, before
parsing any of them. 2 other requests were refused with HTTP 400 in the
same session for failing that check.

**2. Mapped** it to the same `FailureEvent` the synthetic cohort emits. A
standalone payment has no subscription, so the order identifies the subject.
Nothing downstream asks which producer a record came from.

**3. The deterministic table declined to decide.** `payment_failed` is one of
the few reasons Razorpay documents as not disclosing the cause, so it is one of
the few handed to the model rather than guessed at.

**4. The model answered `TRANSIENT_ISSUER` at 0.75 confidence:**

> Gateway refusal at payment_authorization typically indicates a transient
> issuer issue rather than a risk decline.

That is the evidence being used rather than avoided. On this same reason and
source an earlier prompt produced a reflexive `UNCLASSIFIED`, because it told
the model that `source` and `step` were evidence and then, in the next line, to
prefer the unknown bucket over any guess it could not fully justify. The second
instruction swamped the first.

**5. Audited**, replayable by subject id.

## Reproducing

The receiver runs on `uvicorn recoup.ingest.webhook_app:app --port 8000`,
exposed with any tunnel, registered in the Razorpay dashboard against
`payment.failed` and `subscription.pending` with the webhook secret from
`.env`. `scripts/setup_test_order.py` creates an order and a checkout page;
paying it with `4100 2800 0008 0001` and choosing Failure produces a decline
like this one.
