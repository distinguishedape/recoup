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
INSUFFICIENT_FUNDS   notify → retry 24h → 72h → 120h    (chases the pay cycle)
INSTRUMENT_INVALID   ask for a new card → final notice   (never retries the dead card)
MANDATE_REVOKED      stop                                (never charges, never messages)
TRANSIENT_ISSUER     retry 12h → 24h → 48h               (silent; it's the bank's problem)
RISK_DECLINE         escalate to a human                 (a retry should not argue with a risk block)
UNCLASSIFIED         notify → 24h → 72h → 120h
```

The baseline runs that last row for **every** one of those causes.

## What the experiment actually found

Both arms over the same cohort, paired per-subject random draws, three
probability bands, four independent cohorts of 2,000 subjects.

| Mid band, n=2000 | Baseline ladder | Recoup | | Spec target |
|---|---|---|---|---|
| Gross recovered | ₹18,35,622 | **₹21,18,484** | +15.4% | +10% ✅ |
| Cost of chasing | ₹13,902 | **₹9,865** | −29% | — |
| **Net recovered** | ₹18,21,720 | **₹21,08,619** | **+15.7%** | +15% ✅ |
| Recovery rate | 46.0% | **53.2%** | +7.2pp | +5pp ✅ |
| Attempts per recovery | 5.28 | **3.12** | −40.9% | −25% ✅ |
| Wasted attempts | 1,782 | **2** | −99.9% | >90% ✅ |
| Time to recovery | **34.7h** | 39.2h | +4.5h | — |

All five headline findings **replicate in all four cohorts** — a finding counts
only if it survives the Low/Mid/High sweep in *every* one, not on average.

Recoup is **slower** — 4.5 hours slower on average. The probability lives in the
later retries and it goes and gets it, and blocked contacts wait for the next
permitted hour rather than being dropped. That is a real trade, not a win
everywhere.

### The lift is not evenly earned, and the report says so

Gross recovered per cause, both arms, mid band:

| Cause | Baseline | Recoup | |
|---|---|---|---|
| `INSTRUMENT_INVALID` | ₹11,996 | **₹2,43,884** | **+₹2,31,888** |
| `UNCLASSIFIED` | ₹2,83,368 | **₹6,17,708** | **+₹3,34,340** |
| `TRANSIENT_ISSUER` | **₹5,34,742** | ₹4,61,274 | −₹73,468 |
| `INSUFFICIENT_FUNDS` | **₹10,05,017** | ₹7,95,618 | −₹2,09,399 |

Recoup wins decisively where knowing the cause changes what you do, and **loses
on the two causes an ordinary retry ladder already handles well**. The net is
strongly positive; it is not a clean sweep, and a total alone would have hidden
that.

Dead cards are the clearest case: **₹11,996 recovered by the baseline against
₹2,43,884 by Recoup**, roughly twentyfold, because Recoup asks for a new card
instead of hammering one that cannot work. That result and the near-total
elimination of wasted attempts held across every version of the model during
development. They are the claims to trust most.

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

A sceptical reader should know the −2.26% and the +15.7% come from the same
codebase at different points, and part of the difference is judgement about what
the model ought to represent. The two figures in the previous section are the
ones that survived all of it.

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

## Honesty about what is simulated

Razorpay test mode offers only *Charge as Success* and *Charge as Failure* from
the Dashboard. It cannot inject a specific decline reason, and it exposes no
manual-retry API for domestic cards.

So **recovery outcomes are simulated** against published dunning benchmarks,
declared in the report, and swept across three probability bands. The real
Razorpay rail reads subscription state and builds hosted card-change links; its
`charge` method **raises** rather than returning a plausible result, so
simulated outcomes can never enter through the path labelled real.

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
python -m pytest -q                       # 484 tests

python -m scripts.run_experiment --cohort-size 200 --seed 3     --replicate 11,29,47 --out-dir artifacts --freeze
```

That writes `report.md`, machine-readable `sweep.json` and `replication.json`,
and audit CSVs for both arms at all three bands. A generated copy is committed
under [`evidence/`](evidence/).

**Reproducing with no API key:** `evidence/llm_cache.json` holds every model
response the run needed, keyed by prompt hash. Copy it into your output
directory and the experiment replays exactly, offline. Verified with the key
unset.

Drop `--no-llm` to use a model for ambiguous declines and plan authoring.
Responses are cached by prompt hash, so a rerun reproduces the same report
without touching the network.

### Bring your own model

Set one key and Recoup picks the provider up automatically. Free tiers are
preferred over paid ones when several keys are present.

| Provider | Env var | Free tier | Default model |
|---|---|---|---|
| Groq | `GROQ_API_KEY` | yes, no card | `llama-3.3-70b-versatile` |
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
mean effect on net lift : +₹7,753
spread                  : −₹2,889 to +₹17,581
positive in             : 3 of 4 cohorts  →  does not replicate
```

A finding counts here only if it holds in *every* cohort. The AI's
classification gain clears that bar. Its money contribution does not, and is
reported as not replicating rather than as a mean that happens to be positive.

The clearest thing it does earn: it finds dead cards hiding behind
uninformative reason strings and routes them to a new-card request instead of
retries that cannot work — **+6 recoveries, +₹8,494** on that cause alone.

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
| `docs/decisions.md` | Every problem hit, what was chosen, and what it costs if wrong |
