# The error-card walk

Razorpay publishes error-scenario test cards documented to inject specific decline
reasons. This walk pays one order per card, choosing *Failure* on the mock bank
screen as the documentation instructs, and reads back what actually arrived.

Each row's card was confirmed against the `last4` on the payment's card entity, so
these are the intended cards and not a mistyped run.

| Card | Documented reason | Reason returned | source | step | Recoup classified |
|---|---|---|---|---|---|
| `...0001` | `insufficient_fund` | `payment_failed` | `gateway` | `payment_authorization` | TRANSIENT_ISSUER (llm, 0.75) |
| `...0006` | `card_disabled_for_online_payments` | `payment_failed` | `gateway` | `payment_authorization` | TRANSIENT_ISSUER (llm, 0.75) |
| `...0008` | `card_number_invalid` | `payment_failed` | `gateway` | `payment_authorization` | TRANSIENT_ISSUER (llm, 0.75) |
| `...0007` | `gateway_technical_error` | `payment_failed` | `gateway` | `payment_authorization` | TRANSIENT_ISSUER (llm, 0.75) |
| `...0003` | `card_declined` | `payment_failed` | `gateway` | `payment_authorization` | TRANSIENT_ISSUER (llm, 0.75) |
| `...0009` | `authentication_failed` | `payment_failed` | `gateway` | `payment_authorization` | TRANSIENT_ISSUER (llm, 0.75) |
| `...0000` | `payment_timed_out` | `payment_failed` | `gateway` | `payment_authorization` | TRANSIENT_ISSUER (llm, 0.75) |
| `...0002` | `payment_cancelled` | `payment_failed` | `gateway` | `payment_authorization` | TRANSIENT_ISSUER (llm, 0.75) |

**8/8 cards paid. 1 distinct reason string(s) returned.**

Every documented scenario collapses to the same generic string. Spike finding F1
-- *test mode cannot inject decline reasons* -- holds, and this is considerably
stronger evidence for it than the documentation it was originally drawn from:
eight published error cards, each verified used, producing one indistinguishable
result.

An earlier revision of `docs/decisions.md` claimed F1 was too strong, on the
grounds that these cards existed. They exist; on this account they do not
differentiate. The claim was made from documentation and is retracted on data.

This is the answer to *why is the experiment simulated*. A classifier cannot be
exercised against real declines that carry no cause, which is why the cohort
injects the 25-reason mix and why the live rail's `charge()` raises rather than
returning a plausible number.
