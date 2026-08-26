# Recoup

An agent for failed subscription auto-debits. It reads *why* a charge failed,
picks the intervention that matches that cause, executes it inside hard
compliance limits, and measures itself against the retry ladder it replaces.

Built for the Razorpay AI Buildathon, Track 03 — AI Revenue Recovery.

---

## The problem

When a subscription auto-debit fails, the standard response is to retry it on a
fixed schedule. Same three retries whether the customer was two hundred rupees
short, their card expired in March, or they revoked the mandate last week.

Two of those three cases cannot be fixed by retrying. The money spent trying is
simply gone, and in the third case the customer is being charged after
explicitly withdrawing consent.

## What Recoup does differently

It classifies the root cause first, then acts on it:

```
INSUFFICIENT_FUNDS   notify → retry at 24h → retry at 72h
INSTRUMENT_INVALID   ask for a new card → final notice at 72h     (never retries the dead card)
MANDATE_REVOKED      stop                                          (never charges, never messages)
TRANSIENT_ISSUER     retry at 6h → retry at 24h                    (no messages; it's the bank's problem)
RISK_DECLINE         escalate to a human                           (a retry should not argue with a risk block)
UNCLASSIFIED         notify → the baseline ladder
```

The baseline runs that last row for **every** one of those causes.

## What the experiment actually found

Run over 200 failed charges against the baseline ladder, with paired random
draws and a three-band sensitivity sweep:

| Finding | Low | Mid | High | Verdict |
|---|---|---|---|---|
| Gross recovered | −₹1,498 | ₹0 | +₹3,998 | does not survive |
| Net recovered | −₹825 | +₹599 | +₹4,546 | does not survive |
| Recovery rate | −1.1pp | 0.0pp | +1.1pp | does not survive |
| Attempts per recovery | −1.9 | −1.1 | −0.8 | **survives** |
| Wasted attempts avoided | 126 | 126 | 130 | **survives** |

**Recoup does not recover more money than blind retrying.** It recovers roughly
the same money using materially fewer attempts, and it avoids ~126 charge
attempts that could never have succeeded. On dead cards specifically — where
root cause matters most — it recovers **7 subjects where the baseline recovers
1**, worth +₹14,494.

Where it loses: its budgets give funds declines 2 retries where the baseline
takes 3. Raising that budget would turn the money lift positive, which is
exactly the tuning the frozen configuration hash and the three-band sweep exist
to prevent. See [`docs/decisions.md`](docs/decisions.md) D23.

A finding is reported as surviving only if it points the right way at **all
three** bands. That rule is computed in code and printed verbatim; there is no
path by which a flattering number reaches the report with a claim the sweep
did not support.

## Honesty about what is simulated

Razorpay test mode offers only *Charge as Success* and *Charge as Failure* from
the Dashboard. It cannot inject a specific decline reason, and it exposes no
manual-retry API for domestic cards.

So **recovery outcomes are simulated** against published dunning benchmarks,
declared in the report, and swept across three probability bands. The real
Razorpay rail reads subscription state and builds hosted card-change links; its
`charge` method **raises** rather than returning a plausible result, so
simulated outcomes can never enter through the path labelled real.

Ingestion is real. A live `subscription.pending` webhook and the synthetic
cohort emit an identical `FailureEvent`, and nothing downstream may branch on
which produced it.

## Compliance

Six rules gate every action, each naming itself when it denies: contact window
(08:00–19:00 IST), template allowlist, contact rate limit, opt-out stop,
promise-to-pay suppression, per-cause attempt budget.

The executor accepts exactly one type, and that type is produced only by the
policy gate. Direct construction raises, and so does `dataclasses.replace` —
the route that would have let someone forge permission *by accident* while
adjusting a scheduled time. A review found and closed two such routes; see
D19.

Every decision is appended to an audit log with no update and no delete path.
`reconstruct(subscription_id)` replays any subject's whole story, including
each blocked action and the rule that blocked it.

## Running it

```bash
python -m pip install -e ".[dev]"
python -m pytest -q                       # 397 tests

python -m scripts.run_experiment --cohort-size 200 --seed 3 --out-dir artifacts --no-llm --freeze
```

That writes `artifacts/report.md`, a machine-readable `sweep.json`, and audit
CSVs for both arms at all three bands.

Drop `--no-llm` to use the model for ambiguous declines and plan authoring
(needs `ANTHROPIC_API_KEY`). Responses are cached by prompt hash, so a rerun
reproduces the same report without touching the network.

For the live Razorpay path, see
[`docs/runbooks/razorpay-test-mode.md`](docs/runbooks/razorpay-test-mode.md).

## Where the AI sits

The model proposes; deterministic policy disposes. A lookup table resolves 15
of Razorpay's 17 decline reason strings — free, instant, and incapable of
hallucinating. Only `card_declined` and `payment_failed`, which both describe
themselves as "declined by the customer's bank" and nothing more, reach the
model.

Whatever it returns is validated against closed enums and a template allowlist
before anything acts on it. Asked to invent a failure class, retry a dead card
five times, write its own threatening copy, or use an unregistered template, every
proposal is rejected and the deterministic planner runs instead.

## Layout

| Path | What's in it |
|---|---|
| `recoup/classify/` | Reason-string table, then the model for the ambiguous residue |
| `recoup/plan/` | Per-cause budgets, deterministic planner, validated LLM planner |
| `recoup/policy/` | The six rules and the unforgeable authorisation |
| `recoup/escalate/` | Four tiers, advancement earned by execution, stopping rules |
| `recoup/execute/` | Payment rail protocol, simulated rail, executor, templates |
| `recoup/experiment/` | Control arm, paired harness, sensitivity sweep |
| `recoup/ingest/` | Live webhook receiver and the synthetic cohort |
| `docs/decisions.md` | Every problem hit, what was chosen, and what it costs if wrong |
