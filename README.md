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
INSUFFICIENT_FUNDS   notify → retry 24h → pay-now link 25h → retry 72h → 120h   (chases the pay cycle, then offers another way to pay)
INSTRUMENT_INVALID   ask for a new card → final notice   (never retries the dead card)
MANDATE_REVOKED      stop                                (never charges, never messages)
TRANSIENT_ISSUER     retry 12h → 24h → 48h               (silent; it's the bank's problem)
RISK_DECLINE         escalate to a human                 (a retry should not argue with a risk block)
UNCLASSIFIED         notify → retry 24h → pay-now link 25h → retry 72h → 120h
```

The baseline runs one row for **every** one of those causes: notify, then retry at
T+1, T+2 and T+3 — no link, no request for a new card, and no stopping.

A pay-now link is a **customer contact**, not a loophole around the rules that
govern them: it obeys the contact window, the 24-hour minimum gap and the per-cause
contact budget, it is refused at the policy gate for any cause outside the two it
applies to, and delivery to the customer is **off by default** until a deployer
opts in.

## What the experiment actually found

Both arms over the same cohort, paired per-subject random draws, three
probability bands, four independent cohorts of 2,000 subjects.

| Mid band, n=2000 | Baseline ladder | Recoup | | Spec target |
|---|---|---|---|---|
| Gross recovered | ₹18,35,622 | **₹23,18,879** | +26.3% | +10% ✅ |
| Cost of chasing | ₹13,902 | **₹8,795** | −36.7% | — |
| **Net recovered** | ₹18,21,720 | **₹23,10,084** | **+26.8%** | +15% ✅ |
| Recovery rate | 46.0% | **58.7%** | +12.7pp | +5pp ✅ |
| Attempts per recovery | 5.28 | **2.46** | −53.4% | −25% ✅ |
| Wasted attempts | 1,782 | **3** | −99.8% | >90% ✅ |
| Time to recovery | 34.7h | **34.4h** | −0.3h | — |

**Net recovery cleared its +15% target once failed payers were offered another way
to pay.** It did not before: the previous published run came in at **+14.0%**, one
point short, on a codebase that could tell a customer their payment failed but had
nothing to offer them except the same instrument again. A pay-now link closed it —
[D60](docs/decisions.md), and the honest accounting of what that link is and is not
responsible for is below.

All five headline findings **replicate in all four cohorts** — a finding counts
only if it survives the Low/Mid/High sweep in *every* one, not on average. Net lift
clears +15% in **all twelve cells** (three bands × four cohorts); the worst is +21.6%.

Recoup is no longer **slower**, which it was for most of this project — it recovered
more but 4.7 hours later, because the probability lives in the later retries and it
went and got it. Offering a way to pay one hour after the first retry collects from
customers who would otherwise have waited for the day-three retry: 39.4h → 34.4h,
now marginally *faster* than the baseline rather than materially slower.

### The lift is not evenly earned, and the report says so

Gross recovered per cause, both arms, mid band:

| Cause | Baseline | Recoup | |
|---|---|---|---|
| `INSTRUMENT_INVALID` | ₹11,996 | **₹2,56,874** | **+₹2,44,878** |
| `INSUFFICIENT_FUNDS` | ₹10,05,017 | **₹11,93,917** | **+₹1,88,900** |
| `UNCLASSIFIED` | ₹2,83,368 | **₹3,63,836** | **+₹80,468** |
| `TRANSIENT_ISSUER` | **₹5,34,742** | ₹5,04,252 | −₹30,490 |
| `RISK_DECLINE` | **₹499** | ₹0 | −₹499 |

**Dead cards are where the whole thesis lives: ₹11,996 recovered by the baseline
against ₹2,56,874 by Recoup**, twenty-onefold, because Recoup asks for a new card
instead of hammering one that cannot work.

It loses on `TRANSIENT_ISSUER` — an outage is the one cause a plain retry ladder
is already well suited to, and Recoup spends fewer attempts on it by design. And
it gives up ₹499 on `RISK_DECLINE` on purpose, because that class routes to a
human rather than arguing with a bank's fraud decision. Neither is a bug; both
are the cost of a policy that is stated in advance.

That per-cause split and the near-total elimination of wasted attempts held
across every version of the model during development. They are the claims to
trust most.

### What actually earned the money, and what the link is not responsible for

A total lift is easy to take on trust. Recovered money is therefore also broken
down by the mechanism that collected it, mid band:

| Mechanism | Baseline | Recoup |
|---|---|---|
| `retry` | ₹18,35,622 | ₹17,36,664 |
| `pay_now_link` | ₹0 | **₹3,25,341** |
| `instrument_update` | ₹0 | **₹2,56,874** |

**That ₹3,25,341 overstates what the link added, and the number to trust is
smaller.** Credit goes to whichever mechanism actually collected the payment, so a
customer the link converts on day two is credited to the link even when the day-four
retry would have collected the same rupees. Measured against the identical run
without the link — same seed, same cohort, byte-identical control arm — the two
classes the link is permitted for gained **₹1,87,405**, not ₹3,25,341. About
**two-fifths of the link's headline figure is money the ladder would have earned
anyway** (31% at the Low band, 48% at the High). It earns real money *and* pulls
existing money forward, which is also why time to recovery fell five hours.

And **₹45,475 of the improvement is not the link at all.** Dead cards gained, on a
cause `pay_now_link` is refused for at the policy gate. The audit trail says why: the
planner prompt changed, every plan was therefore re-asked, and 71 of 409 dead cards
now get a *second* request for a new card where they previously got a generic notice
that carries no conversion probability at all. That is worth +25 recoveries and it
belongs to the model, not to the link. Both figures are in
[D60](docs/decisions.md).

### What it took to get here, stated plainly

An earlier version of this README reported Recoup **losing** 2.26% of gross
recovery. That number was real, and three things changed between then and now:

1. **The model could not see timing.** Recovery probability was indexed on
   attempt count alone, so a six-hour retry and a day-long wait were the same
   event. Recoup's advantage is almost entirely *when* it acts and its cost is
   *how often*, so the harness measured the cost and none of the benefit. Fixing
   this is a correctness fix, and it made the result **worse** first, because it
   revealed the retry schedule was too eager.
2. **Budgets were widened and the schedule re-timed** — after seeing the loss,
   which is exactly what pre-registration guards against. The principle was
   stated first: the budget exists to prevent *waste*, not to be thrifty on
   causes where retries work, since an attempt costs ₹3 against a plan worth
   ~₹1,500. The configuration was re-frozen. Weigh it knowing the order.
3. **A live model exposed two real bugs** (see below).
4. **An audit against the spec's own targets found all five primary metrics
   missing**, and two defects behind them. `contact_window` was blocking 872
   contacts per run and **discarding every one** — turning a rule about *when* to
   message into a rule about *whether*, which is a far more expensive policy than
   anyone agreed to, wearing a compliance costume. Blocked contacts now return to
   the clock at the next permitted hour, bounded and audited. Fixing that exposed
   the second defect: both efficiency metrics are defined on *charge* attempts and
   were summing every executed action including messages, so recovering 872 lost
   contacts read as a regression. Both are bug fixes — one was losing money, the
   other was counting the wrong thing — but they were made **after** measuring a
   shortfall against targets set in advance, and a reader should weigh them
   knowing that order.

A sceptical reader should know the −2.26% and the +26.8% come from the same
codebase at different points, and part of the difference is judgement about what
the model ought to represent. The two figures in the previous section are the
ones that survived all of it.

5. **The +15% net target was missed at +14.0% and then closed by adding a
   capability, not by adjusting a constant.** Customers whose payment failed for
   want of money were being told what happened and offered nothing to act on; they
   now get a real Razorpay payment link. The one budget change that made room for it
   (two contacts instead of one, on the two causes the link applies to) was made and
   written down **before** the re-measurement, for a structural reason — D59 — which
   is the opposite of the sequence in point 2. Nothing was adjusted afterwards.

### Two defects in how the evidence was produced

Both were found while building the decision console, after the numbers had
already been published. Neither was in the pipeline; both were in the harness
that measures it, which is the harder place to look.

**The experiment ran without a model provider.** `scripts/run_experiment.py`
never loaded `.env`, so the client resolved no provider and every prompt that
missed the committed cache raised `LLMUnavailable`. The classifier caught it and
degraded to `UNCLASSIFIED` at 0.30 confidence, exactly as it should in
production — which meant **930 of 1,542 ambiguous classifications never reached a
model**, and the arm labelled "with LLM" was running about 40% connected. The
degraded run scored *higher*, because `UNCLASSIFIED` subjects get the generic
ladder and its extra retries. Fixed by loading `.env`; and the runner now refuses
to write a bundle if any prompt reached neither cache nor model, unless
`--allow-fallback` is passed. Graceful degradation is right in a live pipeline
and wrong in a measurement.

**The audit log accumulated across runs.** `run_paired_experiment` opened its
`AuditLog` on a path that already existed, and an audit log is append-only by
design. Re-running into the same directory stacked runs on top of each other:
the published `audit_mid_treatment.csv` held **six runs at once**, 12,000 ingest
records for a 2,000-subject experiment. Metrics are computed from the in-memory
result rather than from the export, so the headline numbers were never wrong —
but the audit trail offered as evidence for them was unreadable. A measurement
now starts from an empty log.

The classifier is measured now rather than asserted:
`tests/classify/test_accuracy.py` runs 2,000 cohort events against known truth
and asserts **84.5% from the lookup table alone, 99.4% with the model**, and
96.1% on the ambiguous subset the table refuses. It runs from the committed
cache, so it needs no API key.

### Two bugs a live model found that 429 tests had not

Turning a real model on, rather than the deterministic planner, broke things in
ways worth recording:

- It placed **charge retries a tier above the notification**. The ladder opens a
  tier only once its predecessor has executed, so a notification blocked by the
  contact window killed the retries behind it. The root error was mine: the
  ladder governs *contact intensity*, and a charge retry has no channel and was
  never on that scale.
- For a dead card it put a generic notice first and **the request for a new card
  behind it**. Every action permitted, within budget, and structurally broken:
  63 recoveries became 0 on identical attempts.

Both are fixed, and the deterministic planner is now a **floor the model has to
clear** — plans are scored against the timing model and the model's is used only
when it is genuinely better. That makes the model upside-only rather than a
liability.

## Detecting, not just being told

A webhook is a push: it reports a failure the moment it happens and reports
nothing else. Two of the three risk surfaces the brief names have no webhook to
wait for, because *nothing happened* is not an event. So Recoup also goes
looking — `recoup/detect/scanner.py`, against real list queries:

| Surface | Found by | What it yields |
|---|---|---|
| Payment failures | `subscriptions` in `pending`/`halted` | the same revenue the push reports, found without one |
| Checkout abandonment | unpaid `orders` past a threshold | see below |
| Overdue receivables | `invoices` past `due_by` | unpaid balance, detected not yet actioned |

On the first run against the live test account it found **₹3,497 at risk across
three abandoned checkouts**.

**Razorpay gives away a cause signal for free.** An order with `attempts=0` is a
customer who never tried; one with `attempts=3` tried and was declined three
times. Those are different problems wanting different answers — this product's
thesis applied to a second surface. So an attempted order's failed payments are
fetched and converted into a `FailureEvent` carrying the real `error_reason`,
which the existing classifier handles unchanged; and an order nobody attempted
is reported as `actionable=False` **with no failure class assigned**, because
nobody declined anything and inventing a cause would be the fabrication this
codebase keeps refusing.

Every method on the read client is a GET, and a test asserts the module contains
no write verb — detection runs on a schedule against a live account, so it
should be safe without anyone reading the code to be sure.

The webhook receiver now runs the agent rather than logging to it. For most of
this project `_default_app()` passed no sink, so a signed event was verified,
mapped, written to the audit log and dropped — every claim here rested on the
simulation while the deployed artefact was a receiver. `LiveAgent` closes that.

```
python -m scripts.error_card_walk --create   # one order per error-scenario card
python -m scripts.error_card_walk --verify   # classify whatever actually arrived
```

## Honesty about what is simulated

Razorpay test mode cannot inject a specific decline reason, and it exposes no
manual-retry API for domestic cards.

That first claim is now measured rather than assumed. Razorpay publishes eight
error-scenario test cards documented to produce distinct error codes. **All eight
were paid, each card confirmed by `last4`, and every one returned the identical
generic result:**

```
error_reason  payment_failed    error_source  gateway
error_step    payment_authorization           "Payment failed"
```

Eight documented scenarios, one indistinguishable string —
[`evidence/error-card-walk.md`](evidence/error-card-walk.md), reproducible via
`python -m scripts.error_card_walk --verify`. A classifier cannot be exercised
against real declines that carry no cause, which is exactly why the cohort
injects a 25-reason mix and why the live rail's `charge()` raises rather than
returning a plausible number.

So **recovery outcomes are simulated** against published dunning benchmarks,
declared in the report, and swept across three probability bands. The real
Razorpay rail reads subscription state, builds hosted card-change links and
**creates real Razorpay payment links** through the live API; what it will not do is
invent whether one was *paid* — `charge` and `deliver_pay_now_link` raise or return
`False` rather than returning a plausible result, so simulated outcomes can never
enter through the path labelled real. Link creation is real; link conversion
(12%/22%/34%) is a declared assumption printed in the report's Assumptions table,
and if the true rate sits materially below the Low band the +15% target reopens.

Ingestion is real and has been proven end to end. **Razorpay signed a
`payment.failed` event and delivered it over the public internet to the
receiver, which verified the signature, mapped it, classified it through the
model and audited it.** The signature was computed by Razorpay and accepted by
our code — not, as in an earlier run, produced and checked by the same HMAC.
See [`evidence/live-razorpay-run.md`](evidence/live-razorpay-run.md).

Webhook events and the synthetic cohort emit an identical `FailureEvent`, and
nothing downstream may branch on which produced it.

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
python -m pytest -q                       # 648 tests

python -m scripts.run_experiment --cohort-size 2000 --seed 3     --replicate 11,29,47 --out-dir artifacts --freeze
```

That writes `report.md`, machine-readable `sweep.json` and `replication.json`,
and audit CSVs for both arms at all three bands. A generated copy is committed
under [`evidence/`](evidence/).

**`--freeze` registers what the run is measured against**, and not only the seed.
The configuration hash covers the seed, band, cohort size and start time — none of
which is what actually moved the published numbers. A sentence added to the planner
prompt re-asked every plan and shifted dead-card money by ₹45,475 while that hash sat
unchanged and `--verify-frozen` reported "configuration verified unchanged". So the
prompts, probability bands, budgets, costs, cohort distribution, schedule and model
name are registered in `frozen_config.json` too, and a later run that differs on any
of them **refuses to publish**, naming what moved:

```
refusing to run: the measurement inputs changed after freezing:
budgets.INSUFFICIENT_FUNDS, prompts.planner_user_shape
```

Re-run with `--freeze` to register the change deliberately. That is the point: a
measurement change should be a visible act, not a silent one. See
[D62](docs/decisions.md).

**Reproducing with no API key:**

```bash
mkdir -p artifacts && cp evidence/llm_cache.json artifacts/
RECOUP_LLM_MODEL=openai/gpt-oss-120b   python -m scripts.run_experiment --cohort-size 2000 --seed 3       --replicate 11,29,47 --out-dir artifacts
```

`evidence/llm_cache.json` holds every model response the published run needed,
keyed by a hash of `model | system | user | max_tokens`. That first component is
why the model has to be named: the cache was filled by `openai/gpt-oss-120b`, and
a run that guesses a different model asks a different question. Verified with
every provider key unset — the sweep and the replication come back
**byte-identical** to the committed bundle, and the runner would have refused to
write anything if a single prompt had fallen through to the deterministic
fallback.

Drop `--no-llm` to use a model for ambiguous declines and plan authoring.
Responses are cached by prompt hash, so a rerun reproduces the same report
without touching the network.

### Bring your own model

Set one key and Recoup picks the provider up automatically. Free tiers are
preferred over paid ones when several keys are present.

| Provider | Env var | Free tier | Default model |
|---|---|---|---|
| Groq | `GROQ_API_KEY` | yes, no card | `openai/gpt-oss-120b` |
| Google AI Studio | `GEMINI_API_KEY` | yes, no card | `gemini-2.0-flash` |
| OpenRouter | `OPENROUTER_API_KEY` | yes, `:free` models | `meta-llama/llama-3.3-70b-instruct:free` |
| Together | `TOGETHER_API_KEY` | yes | `Llama-3.3-70B-Instruct-Turbo-Free` |
| xAI (Grok) | `XAI_API_KEY` | paid | `grok-2-latest` |
| Anthropic | `ANTHROPIC_API_KEY` | paid | `claude-sonnet-5` |
| Anything OpenAI-shaped | `OPENAI_API_KEY` + `OPENAI_BASE_URL` | — | `gpt-4o-mini` |

Override with `RECOUP_LLM_PROVIDER` and `RECOUP_LLM_MODEL` if you want a
specific pairing. Temperature is pinned to zero on every provider.

**Why a free model is fine here.** The model does two narrow jobs: resolve the
two ambiguous decline reasons, and draft a plan. Both outputs are validated
against closed enums and a template allowlist before anything acts on them, and
both fall back to the deterministic path when the answer is unusable. A weaker
model degrades the *quality* of a suggestion; it cannot degrade the *safety* of
an action.

For the live Razorpay path, see
[`docs/runbooks/razorpay-test-mode.md`](docs/runbooks/razorpay-test-mode.md).

## Where the AI sits, and what it is measurably worth

The model proposes; deterministic policy disposes. A lookup table resolves 28
of Razorpay's documented decline reasons — free, instant, and incapable of
hallucinating. Only the handful Razorpay itself documents as *not disclosing
the cause* reach the model.

**Classification accuracy, 2,000 subjects, realistic reason mix:**

| | Accuracy |
|---|---|
| Table alone | 84.5% |
| Table + model | **99.4%** |

The model correctly resolves 298 declines the table refuses to guess at, on
**six API calls** — identical evidence produces identical prompts and the cache
collapses them. The 12 it misses are all one confusion between two
gateway-sourced causes.

**Its effect on money is weaker, and reported by the same strict rule as
everything else:**

```
mean effect on net lift : +₹13,230
spread                  : −₹13,651 to +₹33,436
positive in             : 3 of 4 cohorts  →  does not replicate
```

A finding counts here only if it holds in *every* cohort. The AI's
classification gain clears that bar. Its money contribution does not, and is
reported as not replicating rather than as a mean that happens to be positive.

The clearest thing it does earn, and the one contribution that clears the bar:
it finds dead cards hiding behind uninformative reason strings and routes them to a
new-card request instead of retries that cannot work. Against the deterministic
planner that is **+₹8,994 to +₹79,463, positive in all twelve cells** — every band
of every cohort — around +₹49,000 at the mid band.

**Everything it returns is validated before anything acts on it.** Asked to
invent a failure class, retry a dead card five times, write its own threatening
copy, or use an unregistered template, every proposal is rejected and the
deterministic planner runs instead. And since a permitted plan can still be a
bad one, its schedule is scored against the timing model — the deterministic
planner is a floor it has to clear, not merely a fallback.

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
| `recoup/razorpay/` | GET-only read client, detection scanner, payment-link writer |
| `docs/decisions.md` | Every problem hit, what was chosen, and what it costs if wrong |
