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

| Mid band, n=2000 | Baseline ladder | Recoup | |
|---|---|---|---|
| Gross recovered | ₹18,62,629 | **₹20,32,563** | +9.1% |
| Cost of chasing | ₹13,998 | **₹8,404** | −40% |
| **Net recovered** | ₹18,48,631 | **₹20,24,159** | **+9.5%** |
| Recovery rate | 45.7% | **49.2%** | +3.5pp |
| Attempts per recovery | 5.36 | **3.96** | −26% |
| Wasted attempts | 1,800 | **438** | −76% |
| Time to recovery | **35.2h** | 36.5h | +1.3h |

All five headline findings **replicate in all four cohorts** — a finding counts
only if it survives the Low/Mid/High sweep in *every* one, not on average.

Recoup is slightly **slower**. The probability lives in the later retries and it
goes and gets it. That is a real trade, not a win everywhere.

### The strongest result is the one that never moved

On dead cards — the one cause where knowing the reason changes what you should
do — Recoup recovers **63 subjects where the baseline recovers 11, using a third
of the attempts**. It asks for a new card instead of hammering one that cannot
work.

That figure, and the 76% cut in wasted attempts, held across every version of
the model during development. They are the claims to trust most.

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

A sceptical reader should know the −2.26% and the +9.5% come from the same
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

Ingestion is real, and has been run for real. A genuine payment declined by
Razorpay was replayed through the receiver: signature verified against the exact
bytes, mapped, classified by the model, planned and audited. See
[`evidence/live-razorpay-run.md`](evidence/live-razorpay-run.md).

Live webhooks and the synthetic cohort emit an identical `FailureEvent`, and
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
