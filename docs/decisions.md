# Recoup — Engineering Decision Log

Every problem that came up while designing and building Recoup, what the options
were, what I chose, and what it costs if the choice was wrong.

This is a living document. Design decisions are recorded as they were made;
execution decisions are appended as tasks run.

Related: [design spec](superpowers/specs/2026-08-25-recoup-design.md) ·
[core pipeline plan](superpowers/plans/2026-08-25-recoup-core-pipeline.md) ·
[ingestion plan](superpowers/plans/2026-08-25-recoup-razorpay-ingestion.md) ·
[experiment plan](superpowers/plans/2026-08-25-recoup-experiment-harness.md)

---

## Phase 1 — Research: the constraint that determined the architecture

### D1. Can Razorpay test mode produce the failures this product needs?

**Problem.** The whole premise of Recoup is reading *why* an auto-debit failed and
choosing an intervention matched to that cause. That requires failures with
distinguishable causes. Before writing a line of code I needed to know whether
Razorpay test mode could produce them.

**What I found.** It cannot. Test mode offers exactly two Dashboard controls —
*Charge as Success* and *Charge as Failure* — with no API and no way to specify a
decline reason. A "failure" in test mode is an undifferentiated failure.

Three further findings compounded it:

| # | Finding | Consequence |
|---|---|---|
| F1 | Test mode cannot inject a specific decline reason; Dashboard-only, no API | The classifier has no real input to classify |
| F2 | No manual-retry API for subscription invoices; Razorpay's docs state manual charging of a domestic card is not supported | The retry intervention cannot be executed against the real API at all |
| F3 | Test-mode card tokens expire after 3 days | Any multi-day experiment against real test mode dies mid-run |
| F4 | The documented retry ladder is not T+1/T+2/T+3, and Razorpay's own pages contradict each other (test-mode page: 10min → 1hr → halted; retries page: day-stepping) | The baseline I planned to measure against does not exist as I described it |

**Decision.** This is not a detail — it invalidates the obvious architecture. I
stopped and re-planned rather than discovering it during implementation.

**Cost if wrong.** None; these are documented, verifiable facts, and F1/F2 are the
load-bearing ones. If Razorpay ships a manual-retry API, the `PaymentRail`
protocol means swapping the rail is a one-file change.

### D2. Two findings that survived and became features

Not everything from the spike was bad news:

- **F5** — Razorpay's real reason strings (`insufficient_funds`, `card_expired`,
  `bank_technical_error`, `payment_risk_check_failed`, …) map cleanly onto the
  taxonomy, and the `source` / `step` error parameters give a second independent
  signal. The classifier can be a lookup table for most cases.
- **F6** — Mandate revocation is a *subscription state* (`cancelled`), not a
  payment error code. This is easy to get wrong and produces the worst possible
  failure mode: dunning a customer who has explicitly withdrawn authorisation.
- **F7** — The instrument-update mechanism is real: `subscription_card_change=1`,
  a hosted card-change page, and `halted → active` on success. The intervention
  Recoup proposes for a dead card is a real Razorpay flow, not an invention.

---

## Phase 2 — Architecture

### D3. What to do about a payment rail that cannot be driven

**Options.**

- **A — Real ingestion, simulated recovery rail.** Take real webhooks from
  Razorpay test mode; simulate the recovery outcomes.
- **B — Real everything.** Drive real retries against the API.
- **C — Simulate everything.** Skip Razorpay entirely.

**Decision: A** (refined to **A1**: real ingestion only, card-change *execution*
demoted to future scope).

**Why.** B is not buildable — F2 says the API cannot do it. C would make the
Razorpay integration decorative, and the submission is for a Razorpay buildathon.
A is the only option that produces the promised experiment while keeping a real,
load-bearing integration.

**The thing that makes A honest:** both ingestion paths — the live webhook
receiver and the synthetic cohort generator — emit an *identical* `FailureEvent`,
and nothing downstream is permitted to branch on `source`. That single property is
what makes the real slice load-bearing rather than a demo bolted onto a
simulation. If the real path were a different shape, it would be decoration.

**Cost if wrong.** The recovery numbers are simulated, and the report says so in
its first assumptions paragraph. A judge who rejects simulation entirely rejects
the result — which is why D4 exists.

### D4. Making a simulated result survive a hostile reading

**Problem.** "We simulated it and it worked" is worth nothing. Simulation lets you
pick the numbers that produce the answer you want, and everyone reading knows it.

**Decision.** Five mechanisms, all in code rather than prose:

1. **Cited benchmark probabilities**, not invented ones — sourced from published
   dunning benchmarks, printed in the report.
2. **Low / Mid / High bands** for every probability, declared up front.
3. **A mandatory sensitivity sweep** — the entire paired experiment runs three
   times, once per band.
4. **The survival rule, enforced in code:** a lift that points the right way only
   at the High band is reported as **not surviving**. `Finding.survives` is
   computed by the sweep and printed verbatim by the renderer, so there is no path
   by which a favourable-looking number reaches the report with a claim the sweep
   did not support.
5. **A declared cohort distribution and frozen configuration hash**, so the cohort
   cannot be quietly tuned until the result appears.

**Cost if wrong.** The bands could be miscalibrated. But they are printed, so a
reader can substitute their own and recompute — which is the point.

### D5. Where the AI actually sits

**Options.** LLM drives execution · LLM proposes and deterministic policy disposes ·
LLM only classifies.

**Decision: LLM proposes, policy disposes.**

**Why.** A recovery agent that can message customers and charge cards is a system
where a hallucination has a real-world cost. The model gets two bounded jobs:
resolve the two genuinely ambiguous reason strings (`card_declined`,
`payment_failed`), and author an intervention plan. Everything it returns is
validated against closed enums and a template allowlist before anything acts on
it. It never executes.

The deterministic table handles the other thirteen reason strings — free, instant,
auditable, and incapable of hallucinating.

**Cost if wrong.** The LLM's contribution is narrow enough that a sceptic could
call it underused. I'd rather defend "the model is used where judgment is actually
required" than defend an agent that talked itself into charging a cancelled
mandate.

### D6. Making non-bypassability structural rather than conventional

**Problem.** "The policy engine checks every action" is a claim about discipline.
Discipline fails. A judge asking "what stops this from texting someone at 3am?"
deserves better than "we always remember to call `authorize()`".

**Decision.** `AuthorizedAction` is a frozen dataclass carrying a module-private
token, and `Executor.execute()` accepts nothing else:

```python
_AUTH_TOKEN = object()          # never exported

@dataclass(frozen=True)
class AuthorizedAction:
    action: Action
    verdict: PolicyVerdict
    token: Any = field(repr=False, default=None)

    def __post_init__(self):
        if self.token is not _AUTH_TOKEN:
            raise PermissionError(...)
```

An unauthorised action is not a policy violation caught at runtime — it cannot be
constructed. A code path that skips the policy engine does not run even once.
There is a test that tries to forge one.

**Cost if wrong.** Slightly unusual Python. Worth it: this is the single design
decision that best answers the compliance question.

### D7. Money as integer paise

**Problem.** The spec was written in `plan_amount_inr`. Floating-point rupees
accumulate error across hundreds of subjects and dozens of attempts, and the
headline deliverable is a rupee figure.

**Decision.** Every money value in the pipeline is `int` paise. Only the report
converts, and it does so with `divmod(paise, 100)` plus Indian digit grouping —
never a float. There is a formatting test for `₹10,00,000.00`.

**Cost if wrong.** None. Slightly more verbose field names.

---

## Phase 3 — The compliance audit that reordered the product

### D8. The spec measured recovery rate but never measured money

**Problem.** I re-read the Track 03 brief against my own v0.2 spec:

> **the bar**: Don't just identify the problem. Show **measured money recovered**
> across a batch, with compliant escalation, stopping rules, and an audit trail.

My spec measured recovery *rate* and had the cost model parked as a secondary
concern. That is exactly backwards from what was asked for. The brief names money
first, and I had it nowhere in the primary metrics.

**Decision.** Four changes, all promoting money and compliance to first-class:

| Gap | Fix |
|---|---|
| No money measured | **R9 Money accounting** — every subscription carries a plan amount; every attempt carries a cost; each arm reports **gross ₹ recovered** and **net ₹ recovered** (gross − cost across *all* subjects, recovered or not) |
| "Compliant escalation" not modelled as escalation | **R8 Escalation ladder** — four named tiers, each independently gated, advancement mechanical |
| Stopping rules scattered across requirements | Consolidated into six named rules and four terminal states, with an invariant test that the states partition the cohort |
| No mapping to the brief | A bar-mapping table in §1 of the spec |

**Why net and not just gross.** Gross recovery alone rewards spraying attempts at
everyone. Net — which charges the cost of every attempt on every subject including
the ones that never recover — is the number that actually distinguishes a smart
recovery system from an aggressive one. It is also the number that makes
"attempts avoided on a dead card" show up as money rather than as a footnote.

**Cost if wrong.** The cost constants (₹3.00/charge, ₹0.20/email, ₹0.25/SMS) are
declared assumptions, printed in the report. Real Razorpay pricing would be a
one-line substitution.

### D9. The baseline was wrong, and I had to say so

**Problem.** The original PRD described Razorpay's retry ladder as "T+1/T+2/T+3".
Research (F4) showed that is not Razorpay's documented behaviour, and Razorpay's
own documentation contradicts itself between two pages.

**Decision.** Define the modelled baseline explicitly, state that it is a model,
and state why this model: four total charge attempts (initial failure + three
retries), day-stepped, context-blind, no intervention beyond Razorpay's own
failure email, terminating in `halted`.

Day-stepping was chosen over the test-mode 10min → 1hr ladder because the latter
reads as test acceleration rather than production behaviour. The report says all
of this in a section titled "The baseline this was compared against".

**Cost if wrong.** If a judge believes the real ladder is materially different,
the lift number moves. Stating the baseline explicitly is what lets them check —
a comparison against an unstated baseline is not a comparison at all.

### D10. Voluntary churn must leave the denominator

**Problem.** A customer who revoked their mandate did not *fail to be recovered*.
They left. Counting them as an involuntary-churn failure rewards whichever arm
gives up on them fastest — which would flatter Recoup, since Recoup stops
immediately and the baseline spends three retries.

**Decision.** Recovery rate is `recovered / (cohort − voluntary_churn)` in **both**
arms. The control arm has no classifier, so its voluntary-churn label comes from
the cohort's ground truth — used purely as a bookkeeping split, never as a
decision input. The control arm still spends its full ladder on those subjects,
because it genuinely has no way to know, and that wasted spend correctly shows up
in its net figure.

**Cost if wrong.** This is the accounting choice most likely to be challenged. It
is stated in the report's Definitions section precisely so it can be.

---

## Phase 4 — Planning

### D11. One plan or three

**Problem.** The spec is one coherent pipeline but large enough that a single plan
would mix three separable deliverables with different failure modes.

**Decision.** Three plans, each producing working, testable software on its own:

- **A — Core recovery pipeline** (15 tasks, 104 steps): models, clock, simulated
  rail, audit, classifier, planner, policy engine, escalation, orchestrator.
- **B — Real Razorpay ingestion** (6 tasks, 37 steps): the only part with an
  external dependency that can fail outside the codebase, so it gets its own
  failure surface and its own runbook.
- **C — Experiment harness and reporting** (6 tasks, 36 steps): control arm, money
  accounting, sweep, report.

**Cost if wrong.** Three files to keep consistent instead of one. Mitigated by
each plan naming its dependencies and by the cross-plan interface check in the
pre-flight scan.

### D12. Writing long documents: a tooling failure worth recording

**Problem.** Writing the ~400-line spec via a Bash heredoc failed:

```
/usr/bin/bash: -c: line 53: unexpected EOF while looking for matching `''
```

Markdown containing apostrophes, backticks and nested quotes does not survive a
heredoc reliably.

**Decision.** Use the file-writing tool for prose documents. For documents longer
than one comfortable write, author numbered part files in scratch and concatenate
them into the deliverable — which is how all three plans were produced.

**Cost if wrong.** None. Purely mechanical.

---

## Phase 5 — Plan self-review: four real defects caught before any code

Reviewing the finished plans against the spec found four problems that would each
have surfaced as a failing task mid-execution.

### D13. A budget rule that would have broken the main recovery path

**Problem.** `INSTRUMENT_INVALID` has a charge budget of **zero** — the entire
point being that retrying a dead card is wasted money. But the intervention for a
dead card is *ask the customer for a new one*, and when that succeeds the very
next thing you must do is charge the new instrument. `class_retry_budget` would
have denied it. The payoff of the whole T2 intervention was unreachable.

**Decision.** Add `instrument_updated` and `post_update_charges_used` to
`PolicyContext`. When the instrument has been updated, `class_retry_budget` permits
up to `MAX_POST_UPDATE_CHARGES = 1` charge, tracked separately from the class
budget.

**Reasoning.** A charge on a *new* instrument is not a retry of the failed one. It
is the first attempt on a different instrument. The zero budget exists to stop us
hammering the instrument that already failed, and that intent is preserved exactly.

**Cost if wrong.** A subject whose instrument update converts gets one extra charge
attempt (₹3.00) beyond its nominal class budget. Bounded, audited, and the
alternative is never recovering that subject at all.

### D14. Zero-budget classes could never reach their own terminal action

**Problem.** `is_exhausted()` returns true when both budgets are spent.
`RISK_DECLINE` and `MANDATE_REVOKED` have budgets of 0/0 — so they are exhausted
at tick zero, before anything runs. The runner's exhaustion guard would then block
their *own terminal action*: `ESCALATE_MANUAL_REVIEW` would never execute, and a
risk decline would be silently booked as `unrecovered` instead of reaching a human.

**Decision.** `STOP` and `ESCALATE_MANUAL_REVIEW` are exempt from the exhaustion
check in the runner.

**Reasoning.** Stopping must always be permitted. A rule that can block a stop is a
rule that traps a customer in the ladder — and a rule that can block an escalation
is one that quietly drops the cases most needing human review.

**Cost if wrong.** None identified. Terminal actions cost ₹0 and contact nobody.

### D15. The LLM planner clamped over-budget plans; the spec says reject

**Problem.** My planner trimmed an over-budget LLM proposal down to size. The
spec's acceptance criterion is *"a plan exceeding its class budget is rejected and
regenerated from the deterministic fallback planner."*

**Decision.** Follow the spec: reject the whole proposal and fall back.
`clamp_to_budget` stays as a second enforcement layer.

**Reasoning.** A plan that overspends its budget is evidence the model misread the
situation. The rest of that plan does not deserve more trust than the part that
would have been trimmed. Rejecting is also the cleaner claim to defend: *the model
cannot overspend; if it tries, we don't use its plan.*

**Cost if wrong.** Slightly more fallback-planner usage, which is deterministic and
tested. Acceptable.

### D16. Time-to-recovery was measured from the wrong origin

**Problem.** The cohort's first failures are deliberately spread across 48 hours so
the run is not a thundering herd at t=0. Measuring time-to-recovery from a shared
run-start therefore conflates *recovered slowly* with *failed late*.

**Decision.** Measure per subject from its own first failure. `SubjectOutcome`
carries both `first_failure_at` and `recovered_at`.

**Cost if wrong.** None. Strictly more correct.

---

## Phase 6 — Pre-flight scan: four rulings before execution

Before dispatching any implementer I scanned the plan for internal conflicts —
every pair of tasks sharing a file or interface, and every task against its own
text. The interface table came back clean (including a check that
`policy.rules → execute.messages` and `execute.executor → policy.authorized` do
not form an import cycle — they don't). Four conflicts inside individual tasks
needed rulings.

### R1. A test that could never pass

`test_the_table_has_no_update_or_delete_path` uppercases `recoup/audit/log.py` and
asserts `"UPDATE "` is absent. The module docstring reads *"There is deliberately
no update and no delete"* — which uppercases to contain `"UPDATE "`. The test
fails on its own documentation.

**Ruling.** Tighten the assertion to the SQL forms `"UPDATE audit"` and
`"DELETE FROM"`. Still catches a real mutation path; ignores prose. The spec's R6
append-only requirement is untouched.

**Cost if wrong.** A mutation written in unusual casing could slip past.
Negligible — `append` and `_query` are the only SQL sites and a reviewer reads both.

### R2. A test that was seed-lucky by construction

`test_a_successful_charge_carries_no_error` charges one rail ten times at a fixed
seed. Because `attempts_made` increments each time, the success probability decays
0.70 → 0.007 across those ten calls, giving roughly an 8.8% chance that no charge
succeeds and the test fails. It passes or fails on which seed I happened to type.

**Ruling.** Rewrite to use a fresh rail per seed across 20 seeds, asserting at
least one success and that successes carry an empty `error_reason`.

**Cost if wrong.** None to behaviour; the assertion is preserved and made
deterministic.

### R3. Two tests pinned to a subject whose behaviour is randomly assigned

The opt-out and promise-to-pay tests both target `sub_0000`, whose latent failure
class is drawn from the cohort distribution. If it draws `MANDATE_REVOKED` or
`RISK_DECLINE`, its only action is terminal — and terminal actions are exempt from
both rules (see D14), so the asserted denial never appears.

**Ruling.** Apply each flag to the first five subscription ids over a 100-subject
cohort; assert no contact executed for any of them, and at least one audited
denial naming the expected rule across the set. The policy-halt test moves to
`cohort_size=100` for the same reason.

**Cost if wrong.** Marginally weaker per-subject assertions in exchange for
robustness. The behaviour under test is unchanged.

### R4. Test counts are indicative, not binding

Several `Expected: PASS (N tests)` lines are off by a few against the parametrised
cases actually listed — I miscounted while writing.

**Ruling.** Implementers must make every *listed* test pass; a count mismatch alone
is not a defect and must not enter the fix loop.

**Cost if wrong.** A genuinely missing test could hide behind the exemption. The
task reviewer still checks the brief's test list against the diff.

---

## Phase 7 — Execution setup

### D17. No repository existed

The project directory contained only `docs/`. Plan A Task 1 calls `git init`, but
the SDD process needs a repository *before* Task 1 for its ledger and for
recording the base commit of each task's diff. It also requires that work not
start on `master` without consent.

**Decision.** Initialise the repository, commit `docs/` to `master`, then create
and switch to branch `recoup-core-pipeline`. Task 1's `git init` becomes an
idempotent no-op, and the implementer is told not to branch or switch.

**Why a branch and not a worktree.** A worktree of a repository with one commit
buys isolation from nothing. The branch gives the same separation from `master` at
a fraction of the ceremony.

**Cost if wrong.** None. `master` holds the docs; all implementation is isolated on
the branch.

### D18. Model selection per task

Each subagent is dispatched on the cheapest model that can do its job, specified
explicitly — an omitted model silently inherits the session's most expensive one.

- **Transcription tasks** (the plan contains the complete code to write): cheapest
  tier. Task 1 is scaffold + enums, entirely given in the brief.
- **Integration tasks** (multi-file coordination, judgment): standard tier.
- **Fix rounds 4-5**: at least one tier above the implementer that got stuck — a
  loop surviving three resumes usually means the implementer cannot see its own
  problem, so fresh eyes and a capability bump arrive together.
- **Final whole-branch review**: most capable tier.

---

## Execution log

Appended as tasks complete. Each entry records problems hit during implementation
and how they were resolved.

Plan A (core pipeline, 15 tasks) and Plan C (experiment harness, 6 tasks) are complete:
334 tests passing. Below are the problems that only appeared once code was running.

### D19. A code review destroyed the non-bypassability claim

**Problem.** I dispatched a reviewer specifically to attack `AuthorizedAction`, asking it to
try `dataclasses.replace`, `pickle`, `object.__new__`, subclassing, and reading the private
token out of the module. It found two routes that defeated the design, and neither needed an
exploit:

1. **`mint()` was a public backdoor.** Its only check was that the verdict said yes. Anyone
   who wrote their own `PolicyVerdict(allowed=True, ...)` — a public Pydantic model — got a
   valid authorisation for any action, with no rule ever running. Two imports and a call.
2. **`dataclasses.replace` forged one silently.** `token` was an ordinary field, so `replace()`
   copied it across by identity and `__post_init__` accepted it. This is the worse of the two:
   replacing one field of an immutable object is idiomatic Python, so an engineer bumping
   `scheduled_at` for a backoff would have forged permission **by accident**.

**Decision.** The constructor now always raises, which closes direct construction and
`replace()` together. Construction moved inside `engine.authorize`, immediately after the
rules have run, with no separately importable helper.

**And the claim itself was narrowed.** The commit had said an unauthorised action "cannot be
constructed". That was false. The module now states the standard it actually meets:
`object.__new__`, a subclass, `pickle`, or reaching for the private constructor will all
still work, because Python allows it. What is guaranteed is that *accidental* bypass is
impossible and *deliberate* bypass cannot be written without a line that is unmistakable in
review. Fixing the overclaim mattered as much as fixing the code.

**Cost if wrong.** A determined caller inside the codebase can still bypass the gate. Stated
plainly rather than papered over.

### D20. The same review found the instrument exemption was ungated

`class_retry_budget` skipped the class budget whenever `instrument_updated` was set —
including for `RISK_DECLINE` and `MANDATE_REVOKED`. Both carry a zero charge budget for
reasons a replacement card does not answer: a risk block is a decision about the transaction,
and a revoked mandate is a withdrawal of consent. The exemption now applies only to
`INSTRUMENT_INVALID`, the one cause it was ever argued for.

### D21. The experiment found the flagship intervention had never run

**Problem.** The first full experiment showed `INSTRUMENT_INVALID` recovering **1 of 34**
subjects, and **zero of 34** instrument-update requests converting. At a 35% conversion
assumption, zero is a probability of about 1 in 10 million — a bug, not luck.

**Root cause.** Spec R8 says the planner chooses a *starting tier* from the failure class, and
the dead-card plan starts at T2 — asking for a new card, rather than sending a neutral notice
about a payment that was never going to succeed. My ladder required tier N−1 to have executed
before tier N could open. T1 never runs for that class, so T2 was permanently shut and
**every one of those subjects had its entire intervention blocked**. I had implemented
advancement and never implemented starting tiers.

**Decision.** `LadderState` carries a `starting_tier`, taken from the minimum tier in the
plan. Tiers at or below it are enterable; above it, advancement is still earned by execution
— which was the property worth having. Dead-card recoveries went 1 → 7, worth +₹14,494 on
that class alone.

**Why this is the strongest argument for the harness.** A demo would have shown a working
pipeline, a full audit trail and a plausible number. Only running both arms and comparing
them revealed that the intervention the whole product is built around was recovering nothing.

### D22. The treatment arm was never actually paired

The control arm passed `paired_seed`; the runner did not. The two arms were drawing from
differently-consumed streams, so part of the measured difference was the dice rather than the
intervention. Fixed by threading the same seed through both.

### D23. The result refuses the headline claim, and I am not retuning to fix that

**What the sweep says**, on 200 subjects, seed 3, after both bugs were fixed:

| Finding | Low | Mid | High | Verdict |
|---|---|---|---|---|
| Gross recovered | −₹1,498 | ₹0 | +₹3,998 | **does not survive** |
| Net recovered | −₹825 | +₹599 | +₹4,546 | **does not survive** |
| Recovery rate | −1.1pp | 0.0pp | +1.1pp | **does not survive** |
| Attempts per recovery | −1.9 | −1.1 | −0.8 | **survives** |
| Wasted attempts avoided | 126 | 126 | 130 | **survives** |

Recoup wins decisively where root cause matters — dead cards, +₹14,494 — and avoids ~126
charge attempts that could never have succeeded. But it **does not recover more money** than
blind retrying. It loses ₹7,997 on `INSUFFICIENT_FUNDS` and ₹1,498 on `TRANSIENT_ISSUER`,
because its budgets give those causes 2 retries where the baseline takes 3.

**Decision.** Report it. The obvious "fix" is to raise the funds budget to 3 and watch gross
lift turn positive — which is exactly the tuning the frozen configuration hash and the
three-band sweep exist to prevent. The configuration was frozen before this run
(`53ffabac5f4d18f0`). Changing budgets now to chase a favourable number would invalidate
every other figure in the report.

The spec anticipated this outcome in R9: *"If Recoup recovers a higher share of subscriptions
but a lower share of rupees... that is a finding, not a defect to hide."*

**The honest headline is efficiency, not money:** the same recovery for materially fewer
attempts, with the waste concentrated where it cannot pay off.

**A caveat that runs the other way.** `RISK_DECLINE` shows −₹4,999 because the baseline
recovered one risk-blocked subject by blind retry while Recoup routes all twelve to manual
review — and the model credits manual review with **zero** recovery. A real queue would
recover some. That understates Recoup, and is stated rather than quietly corrected.

### D24. Two process mistakes of my own

**`git add -A` twice swept unrelated work into a commit whose message described only part of
it.** Caught both times before pushing and split into honest commits. The lesson is to stage
explicitly, which costs seconds and keeps history readable.

**The test suite took three minutes** because the audit log fsynced once per record and a
cohort writes thousands. Write-ahead logging with a relaxed sync brought it to seconds
without giving up the append-per-decision guarantee.


---

## Phase 8 — The real Razorpay slice

### D25. The audit log would have crashed on every live webhook

**Problem.** The webhook receiver's tests failed with
`sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same
thread`.

This was not a test artifact. Any ASGI server hands each request to a worker thread, and the
audit log's connection was pinned to the thread that opened it. **The live receiver would have
raised on the audit write for every single webhook it ever received.** The bug had been sitting
in the audit log since Task 4 and nothing caught it, because until now every caller was
single-threaded.

**Decision.** Open the connection with `check_same_thread=False` and serialise writes behind a
`threading.Lock`. That is what SQLite wants anyway — many readers, one writer — and it composes
correctly with the WAL mode added for speed.

**Why it is worth recording.** The test suite only found it because FastAPI's `TestClient`
reproduces the real threading model rather than calling the handler directly. A test that
called the endpoint function in-process would have passed and shipped the bug.

### D26. Refusing to fake what the API cannot do

`RazorpayTestRail.charge` **raises** rather than returning a `ChargeResult`.

It would have been trivial to return something plausible and let the numbers flow. That is
precisely why it does not: the moment simulated outcomes can enter through the code path
labelled *real*, every figure in the report becomes unfalsifiable. The simulated rail is where
recovery outcomes come from, it says so in its own docstring, and this class makes the two
impossible to blur. There is a test asserting the refusal.

What the real rail *does* do is genuine: read subscription state, and build the hosted
card-change link (`subscription_card_change=1`) that the instrument-update intervention
depends on.

### D27. Status codes chosen for Razorpay's retry behaviour, not REST tidiness

Razorpay retries any non-2xx response. So an event type we deliberately do not handle returns
**200** with `status: ignored` — a 404 or 422 would produce an endless retry loop for an event
that will never be processable. Only a malformed or unauthenticated request gets a 400,
because those *should* stop. Replays return `status: duplicate`; the receiver is idempotent on
payment id.

### D28. I ignored my own recorded lesson

D12 records that Bash heredocs cannot reliably carry prose containing quotes and backticks,
and that the file-writing tool should be used instead. I then wrote the webhook mapper and its
tests as a heredoc, and it failed with `unexpected EOF while looking for matching quote`,
losing both files.

Recorded because the failure mode is *having the lesson and not applying it*, which is more
instructive than the original mistake. Prose-heavy files now go through the file tool
regardless of how convenient a heredoc looks.

---

## Final state

- **397 tests passing.** Three plans complete: core pipeline, experiment harness, real
  ingestion.
- **Headline result:** Recoup recovers roughly the same money as blind retrying, using
  materially fewer attempts, and avoiding ~126 charge attempts that could never have
  succeeded. The money lift does **not** survive the sensitivity sweep and is reported as not
  surviving.
- **Six defects were found by running the thing rather than by reading it**: two forgeable
  authorisation routes (D19), an ungated budget exemption (D20), a completely dead flagship
  intervention (D21), an unpaired treatment arm (D22), and a thread-unsafe audit log that
  would have broken every live webhook (D25).

The last one is the argument for the whole approach. A demo would have shown a working
pipeline, a full audit trail, and a plausible number — and three of those six defects would
have shipped inside it.


---

## Phase 9 — Fixing the defects, and what that changed

### D29. The pairing fix in Plan C was itself half-broken

**Problem.** `SimulatedRail` kept one random stream per subject, shared between the charge roll
and the instrument-conversion roll. In the treatment arm an `INSTRUMENT_INVALID` subject spent
draw #1 on the conversion roll, so its first charge used draw #2 — while the control arm's
first charge used draw #1.

Demonstrated directly: control charge draws `[0.3152, 0.0893, 0.1945]`, treatment charge draws
`[0.0893, 0.1945, 0.5621]`. The treatment arm was literally using the control arm's second
draw as its first.

The arms were **not** facing identical luck for exactly the class where they differ most, and
that class carries the largest claimed win. Pairing was weaker than the commit claimed.

**Decision.** Scope streams by purpose as well as subject: `(seed, subscription_id, "charge")`
and `(seed, subscription_id, "convert")`. A subject's charge outcomes are now identical across
arms regardless of what else either arm did to it. Three tests cover it.

### D30. The post-update bound only existed in the caller's discipline

The policy review flagged this as unverifiable from its diff and I never followed up.
Reproduced it: a caller that never increments `post_update_charges_used` gets **10 of 10**
charges allowed on a cause whose budget is deliberately zero. A stale snapshot, a retry loop,
or a second update mistaken for the first would all do it.

**Decision.** Replace the boolean-plus-counter with the instrument's *identity*.
`PolicyContext` now carries `replacement_instrument_id` and the set of
`charged_instrument_ids`. Charging twice requires naming the same instrument twice, which the
engine can see. A count can be reset by accident; an identity cannot. The bound is now the
number of instruments the customer actually supplied, which is the correct semantic rather
than an arbitrary constant.

### D31. The held-out slice did its job on the first run

With pairing fixed, the registered cohort (seed 3, n=200) showed **all five findings
surviving, including money**. That is the result I wanted.

The held-out cohort (seed 11, same frozen configuration) showed money **not** surviving.

Scaling to n=2000 across four seeds settled it: gross lift is −1.55% to −2.86%, consistently
negative. The n=200 win was noise, and reporting it alone would have been a clean sweep and a
lie.

**This is the single strongest argument for building the harness.** The temptation to publish
the first run was real, and the only thing standing against it was machinery built before the
number was known.

### D32. The economics, not the budgets, are the finding

Recoup gives funds declines 2 retries where the baseline takes 3, trading recovery for
attempt-thrift. Whether that trade is good is arithmetic:

```
charge attempts avoided : 4,475   (across 8,000 subjects)
gross recovery given up : ₹184,935
break-even attempt cost : ₹41.33
assumed attempt cost    : ₹3.00
```

**An attempt must cost 14× more than assumed before the saving pays for the recovery it gives
up.** At ₹3 against plan values averaging ~₹1,500, attempt-thrift is nearly worthless.

**Decision.** Report it as the finding rather than fixing the budgets to hide it. Raising the
funds budget to 3 would close the gap and would also be tuning against a known answer, which
the frozen hash exists to prevent.

**The caveat that runs the other way, stated because it is load-bearing.** The scoreboard
gives **zero** weight to not harassing customers: no churn risk from excess contact, no
support load, no compliance exposure, and manual review credited with zero recovery. Those are
precisely the costs the compliance machinery exists to control. The model is structurally
biased against the agent it is scoring, and the honest conclusion is that Recoup's design pays
when attempts are expensive or when over-dunning has a price — neither of which this
simulation prices.

### D33. Two smaller ones

`app = None` on missing credentials made uvicorn fail with an opaque `NoneType is not
callable`. Replaced with a stand-in that raises the original error naming the missing
variables, while still importing cleanly so tests can collect the module without credentials.

The `rules.py` docstring still described the old counter-based bound after the mechanism
changed. Comments that describe code that no longer exists are worse than no comments.

### Still open

- **The LLM path has never made a real call.** Every run used `--no-llm` because no
  `ANTHROPIC_API_KEY` is present in this environment. The classifier's model branch, the
  planner's model branch, and the response cache's reproducibility guarantee are all covered
  by tests with injected transports, but none has been exercised against the actual model.
  This is the largest remaining gap and it needs a key to close.
- **The live Razorpay webhook has never been received.** Same reason: no test-mode
  credentials. The receiver is tested against a captured payload through FastAPI's client;
  the runbook covers the click-path from credentials to an ingested event.
- **Cost constants remain declared assumptions** (₹3.00 per charge, ₹0.20 per email, ₹0.25
  per SMS). D32 shows the headline result is highly sensitive to the first of these, so real
  Razorpay pricing would be the single most valuable substitution available.
