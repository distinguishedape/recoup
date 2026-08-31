# Journey Into Recoup

*A technical history assembled from claude-mem's timeline — 329 observations and 25
session summaries — cross-checked against 87 git commits.*

---

## A note on the sources, before anything else

This report has a gap in it, and the gap should be stated before the narrative rather
than buried inside it.

claude-mem's observations for this project begin at **27 Aug, 13:24**. The first commit
landed on **25 Aug at 22:28**. Between those two timestamps the project accumulated
**52 of its 87 commits — sixty percent of the build** — including the entire core
pipeline, the classifier, the policy engine, the escalation ladder, and the experiment
harness. None of that is in memory.

So this is not a complete history. It is a complete history of the *last two days*, and
a reconstruction of the first two from git. Where the narrative below draws on memory it
cites observation IDs (`#164`); where it draws on git it cites commit hashes (`8389ea1`).
Where it draws on neither, it says so.

That distinction matters more than usual here, because the project this report describes
spent much of its life discovering that its own evidence was less trustworthy than it
looked. It would be poor form to write its history with the same flaw.

---

## 1. Project Genesis

Recoup was built for the Razorpay AI Buildathon, Track 03 — AI Revenue Recovery. The
problem it attacks is narrow and real: when a subscription auto-debit fails, almost
every system retries it on a fixed schedule, regardless of *why* it failed. Two of the
three common causes cannot be fixed by retrying, and one of them — a revoked mandate —
means the retry is charging someone who withdrew consent.

The first two commits, both at 22:28 on 25 Aug, say a great deal about how the project
would go:

```
8389ea1  Set up the package skeleton and the closed vocabularies
361da51  Add the engineering decision log
```

The decision log was the **second thing that existed**. Before the domain models, before
the clock, before a single line of recovery logic, there was a document for recording
what was chosen and what it would cost if the choice was wrong. That log now runs to 65
entries and is the single most useful artefact in the repository.

The four commits of 25 Aug lay down an architecture in a deliberate order:

1. **Closed vocabularies** — `FailureClass`, `ActionType`, `Tier`, `TerminalState`. Enums
   the model is not permitted to invent outside of. The knowledge graph would later
   confirm this as the spine of the whole system: `FailureClass` is the single most
   connected node in the codebase at **221 edges** (`#175`).
2. **The decision log** — the discipline, established before it was needed.
3. **Frozen domain models** (`d31bf2b`) — Pydantic, immutable, shared by every stage.
4. **The virtual clock** (`171b3cb`) — "compresses a dunning run into seconds," which is
   what makes a five-day recovery ladder testable in a hackathon.

Then, on 26 Aug at 10:54, the piece everything else would come to depend on:

```
dd93608  Add the append-only audit log that reconstructs a subject's whole story
```

No update path. No delete path. `reconstruct(subscription_id)` replays any subject's
entire history including every blocked action and the rule that blocked it. The graph
analysis (`#178`, `#179`) would later find that in production code, **the only path
between the real Razorpay rail and the simulated one runs through the audit log** — the
two rails share no other code at all. That was not planned as a property; it fell out of
building the log first and everything else around it.

26 Aug was the heaviest day of the project: **48 commits**, and none of them are in
memory.

---

## 2. Architectural Evolution

The shape that emerged is a pipeline with a gate in the middle:

```
ingest → classify → plan → POLICY GATE → execute → audit
```

Every stage writes to the audit log; nothing skips the gate. The load-bearing invention
is `AuthorizedAction`: the executor accepts exactly one type, and only the policy engine
can produce it. Direct construction raises. So does `dataclasses.replace` — the route
that would have let someone forge permission *by accident* while adjusting a scheduled
time. Observation `#14` records this as "compliance enforcement through the type system
and audit immutability," and `#13` frames the whole architecture as
"classification → targeted intervention → compliance gates → audit trail."

Three evolutions are visible in the record.

**The AI moved from author to proposer.** The model does two narrow jobs — resolve
decline reasons a lookup table refuses, and draft a plan — and every output is validated
against closed enums and a template allowlist before anything acts. Critically, the
deterministic planner became a *floor the model must clear* rather than a fallback:
plans are scored against the timing model and the model's is used only when it is
genuinely better. That makes the model upside-only. `#82`–`#83` capture the two prompts;
`#26` captures the honest verdict on what the model is worth.

**The rails split, and stayed split.** `SimulatedRail` produces measured outcomes;
`RazorpayTestRail` talks to the live API and its `charge()` method **raises** rather than
returning a plausible number (`#71`). A simulated outcome cannot enter through the path
labelled real, because there is no path. The conformance test added at `#102` and `#138`
now enforces that every rail — including test doubles — implements the full protocol.

**Detection was added beside ingestion.** A webhook is a push; it reports a failure and
nothing else. Two of the three risk surfaces have no webhook to wait for, because
*nothing happened* is not an event (`#149`). So the scanner polls for at-risk
subscriptions, abandoned checkouts and overdue invoices — read-only, with a test
asserting the module contains no write verb.

---

## 3. Key Breakthroughs

**The pay-now link closes the shortfall.** The project's headline metric — net recovery
— sat at +14.0% against a +15% target. The fix was not a tuned constant but a new
capability: offer a customer who is short of money a way to pay from somewhere else.
`#7` records ₹3.25L gross recovery through the mechanism; `#15` records that it
replicates at +26.8% to +32.6% across bands and seeds; `#17` that the lift beats target
in **all 12 band/seed combinations with a +21.6% worst case**. `#31` records the
decision: *D60 — net-recovery target cleared by R-A*.

What makes this a breakthrough rather than a tuning exercise is `#16`: **the contact
budget was widened from 1 to 2 before the measurement, not after.** The project had
already disclosed a case (D40) where a budget was changed *after* seeing a loss, which
is what pre-registration exists to prevent. This time the order was reversed and written
down first.

**The classifier's value is proven, and its money value is not.** `#26` is the most
intellectually honest observation in the timeline: *"AI's direct money contribution
(+₹7,753 mean) doesn't replicate; classification accuracy (99.4%) does; dead-card gain
(+₹8,494) consistent."* The project had a strong result available — a positive mean —
and reported it as **not replicating** because it failed in one of four cohorts. `#27`'s
no-LLM ablation confirms the core metrics hold without the model at all.

**The freeze learns to cover what it claims to.** `#60` is a security note recorded on
27 Aug: *"Prompt changes are measurement changes but lack detection/warning."* At that
moment it was an observation, not a bug report. Two hours later `#86` records the fix:
*"Added measurement_inputs hashing to catch prompt and constant changes invisible to
config_hash."* This is the cleanest problem→solution arc in the entire history, and it is
examined in detail in section 6.

---

## 4. Work Patterns

The rhythm is unusually legible because the whole project ran in four days.

| Day | Commits | Observations | Discovery tokens | Character |
|---|---|---|---|---|
| 25 Aug | 4 | — | — | Foundations; vocabularies and the decision log |
| 26 Aug | 48 | — | — | The build. Heaviest day; entirely outside memory |
| 27 Aug | 22 | 152 | 454,620 | Measurement, and the discovery that measurement was broken |
| 28 Aug | 11 | 173 | 480,009 | Presentation, review, and the longest debugging saga |
| 29 Aug | 2 | ~20 | — | Fixes and ship |

The inversion between the two halves is the striking part. On 26 Aug the project
produced 48 commits and no recorded observations; on 28 Aug it produced 11 commits and
173 observations. **Commit count fell by 77% while observation count rose.** The late
phase was not less productive — it was differently productive: reviewing, measuring,
correcting, and writing, all of which generate discoveries rather than diffs.

The observation-type breakdown says the same thing:

| Type | Count | Share |
|---|---|---|
| discovery | 183 | 56% |
| change | 75 | 23% |
| feature | 31 | 9% |
| bugfix | 24 | 7% |
| decision | 12 | 4% |
| security_note | 4 | 1% |

**Fifty-six percent discoveries.** For a four-day hackathon build this is a project that
spent most of its recorded life *finding things out* — and, as the next sections show,
most of what it found out was about itself.

Twenty-nine of the 329 observations came from `general-purpose` subagents; the remaining
303 from the main session. The subagent share drops to zero after 28 Aug, 12:50, which is
exactly when a preference was recorded (`#219`) for inline execution over subagent
dispatch.

---

## 5. Technical Debt

Recoup's debt is unusual: almost none of it is in the product, and almost all of it was
in the machinery that measures the product.

The pattern is visible in the deferred-defect cycle. Four issues were parked during the
pay-now-link work and closed together on 27 Aug as D62–D65 (`#137`–`#143`):

- The measurement freeze did not cover the inputs that affect the numbers.
- `PaymentRail` conformance was not enforced, so test doubles could silently diverge.
- The real subscription entity shape was documented in a comment rather than recorded as
  a fixture.
- Live contact validation and payment-confirmation audit events were missing.

Each is a *checkability* debt rather than a functional one. The code worked; what was
missing was the thing that would notice when it stopped working. `#139` puts it
precisely: *"Real subscription entity shape recorded as fixture, not comment."*

The one piece of genuine product debt in the record is `#244`: a demo loop limit and the
policy gate's reschedule ceiling had a **parity mismatch** — a hardcoded 5 whose comment
claimed agreement with a constant that was 3. `#245` fixed it by deriving the value.
The decision log notes this was the second occurrence of the same magic-number pattern;
D59 had already corrected two test fixtures that hardcoded a budget of 1 and silently
inverted their own meaning when the budget moved.

---

## 6. Challenges and Debugging Sagas

### The evening that broke the demo

The longest continuous debugging effort in the timeline runs from `#243` to `#275` on
28 Aug, between 7:05pm and 7:15pm — roughly thirty observations. It is worth tracing in
full because it contains **three false fixes before the root cause**.

The symptom: a test asserting the demo produces a pay-now link URL had started failing.

- `#244`–`#246` — a constant mismatch is found and fixed. Reasonable. Not the cause.
- `#248` — the test still fails: the link URL is missing from the narration.
- `#249` — the loop bound is corrected to account for both the contact window and the
  escalation ladder. A better fix. Still not the cause.
- `#250` — *"Test still fails despite corrected loop bound; root cause not yet
  identified."* The investigation stops guessing.
- `#251`–`#252` — the failure is isolated to `demo.py`, and then the crucial finding:
  **the test failure pre-existed.** It was not caused by any of the recent edits.
- `#253` — the root cause: *"Test fails due to contact window policy blocking link at
  current wall-clock time."* The contact window closes at 19:00 IST. The suite was being
  run at 19:07.
- `#254` — the deeper mechanism: the demo passed a simulated time to `due()`, but the
  executor gated on the **real** clock, so no amount of fast-forwarding could move it.

The fix took three more iterations — `#255` (sequential ladder), `#262` (ascending time
order), `#264`–`#265` (discovering `VirtualClock` exists, then building `AdvancingClock`),
`#268` and `#271` (multiple passes at one instant; seeding the pending set with
reschedules that had *already* happened) — before `#273`: *"Evening trace shows complete
successful flow: reschedules, tier opening, link creation."*

The consequence was larger than a red test. **The demo could not produce a payment link
after 19:00 IST**, and hackathon judging happens in the evening. This was found because
the suite happened to run at 19:07, not because anyone reasoned about it.

### Four defects in the evidence, not the product

A recurring saga, spread across the whole timeline, is the project auditing its own
measurement and repeatedly finding it wanting:

1. **The experiment ran with the model disconnected.** 930 of 1,542 ambiguous
   classifications never reached a model, and the degraded run scored *higher* because
   `UNCLASSIFIED` subjects take the generic ladder. The runner now refuses to publish a
   bundle if any prompt reached neither cache nor model (`#41`).
2. **The audit log accumulated across runs** — a published CSV held six runs at once.
   Metrics came from memory, so the headline numbers were never wrong, but the evidence
   offered for them was unreadable.
3. **Offline reproduction had been broken since the provider changed to Groq**
   (`#44`, `#45`, `#49`). Cache keys hash the model name; a keyless run fell back to a
   different default and missed every key. True when written, silently invalidated.
4. **The configuration freeze did not cover the prompts** (`#60` → `#86`). It certified a
   run whose planner prompt had been rewritten — a guarantee a reader could point at,
   which is worse than none.

`#137` records the resolution in five words: *"Measurement freeze now covers all inputs
that affect numbers."*

### The graph that found nothing

On 28 Aug the project built a knowledge graph of its own codebase (`#158`–`#186`):
1,374 AST nodes, 5,235 edges, 73 communities. The health check flagged 223 dangling edges
and heavy edge collapsing (`#170`), which looked alarming.

It was not. `#181`–`#186` work through them: the dangling edges are imports of stdlib and
third-party modules; the isolated nodes are empty `__init__.py` files; `mint()` is
correctly documented as a removed API (`#183`); `at_risk_paise()` is actively tested, not
dead (`#186`). `#185` is candid about the tool's own limits: *"Knowledge graph
incomplete; ConfigurationDrift and Finding used in production despite test-only
appearance."*

An investigation that concludes "nothing is broken" is worth recording precisely because
the alternative — quietly manufacturing findings to justify the detour — is the failure
mode it avoided.

---

## 7. Memory and Continuity

claude-mem's effect on this project is real but narrower than the tooling's framing
suggests, and the database is unusually clear about it.

**Where it helped.** The strongest evidence is structural rather than anecdotal. Sessions
S1–S25 each open with injected context, and several sessions resume work whose state
lives nowhere in the code — `#3` ("Branch State: 67c4cb3 with Template-Action Binding and
Measurement Complete") is exactly the kind of fact that is expensive to re-derive and
trivial to recall. The SDD ledger discipline (`#144`, `#188`) exists for the same reason
and was maintained in parallel: the project did not trust conversation memory to survive
compaction, so it wrote its own.

**Where the evidence is absent.** The database records:

- `relevance_count > 0` on **zero** observations.
- **Zero** narratives mentioning "recalled", "from memory", "previous session", or
  "prior session".

There is no `source_tool` column in this schema version, so the skill's suggested
explicit-recall query cannot run at all. In other words: **not one recall event in this
project is directly evidenced in the store.** Context was injected passively at session
start; whether it changed any decision is not something the data can say.

That is not a criticism of the tool — passive injection is its main mechanism, and
measuring counterfactual savings is genuinely hard. It is a caution about the next
section.

---

## 8. Token Economics and Memory ROI

### What is measured

| Metric | Value |
|---|---|
| Observations | 329 |
| Session summaries | 25 |
| User prompts captured | 34 |
| Total discovery tokens | **940,289** |
| Mean discovery tokens per observation | 2,858 |
| Mean read tokens per observation | 365 |
| **Compression ratio** | **7.8 : 1** |
| Date range | 27 Aug 13:24 → 31 Aug 18:50 |

The compression ratio is the solid number here. It says that the work required to
*produce* a typical observation cost roughly eight times what it costs to *read* it
back — 2,858 tokens against 365. That ratio is computed from stored values, not modelled.

### The five most expensive memories

These are the observations that cost the most to produce, and therefore the ones whose
recall would save the most:

| # | Observation | Discovery cost |
|---|---|---|
| `#164` | Recoup project: AI revenue recovery agent, +26.8% measured lift | 18,934 t |
| `#2` | Pay-now-links: structured design review, two critical rulings | 14,598 t |
| `#3` | Pay-now-links branch state at 67c4cb3 | 14,598 t |
| `#309` | Implemented the HTML dashboard template | 12,720 t |
| `#321` | Interactive deck navigation verified | 11,920 t |

The pattern is consistent with the compression figure: the expensive memories are
*design reviews, architectural summaries and state snapshots* — exactly the artefacts
that are laborious to reconstruct and cheap to re-read.

### What is modelled, and should be labelled as such

claude-mem's own session header reports **"278 obs (106,580t read) | 809,745t work | 87%
savings."** The skill that generated this report also supplies a formula: sessions with
context × the discovery value of a 50-observation window × a **30% relevance factor**,
plus ~10K tokens per explicit recall.

Applying it here would yield an impressive number. I am declining to present one as a
finding, for a reason this project has already established as its own standard: the 30%
relevance factor is an assumption nobody has measured, the explicit-recall term is
**zero** because no recall events are recorded, and the counterfactual — what this work
would have cost without memory — was never run.

The honest summary is this. **The compression is real and measured at 7.8:1. The savings
are plausible and unvalidated.** A project whose decision log contains an entry titled
*"the freeze did not cover the things that decide the numbers"* should not close its own
history with an ROI figure resting on an unexamined constant.

---

## 9. Timeline Statistics

**Span.** 25 Aug 22:28 (first commit) → 29 Aug 00:15 (last commit) — approximately
**74 hours**. Memory covers 27 Aug 13:24 onward, roughly the final 59 hours.

**Volume.** 87 commits. 329 observations. 25 session summaries. 34 user prompts.
940,289 discovery tokens.

**Coverage gap.** 52 of 87 commits (60%) predate the first observation.

**Busiest recorded days.**

| Date | Observations | Discovery tokens |
|---|---|---|
| 28 Aug | 173 | 480,009 |
| 27 Aug | 152 | 454,620 |
| 31 Aug | 4 | 5,660 |

**Longest debugging session.** The evening-demo failure, `#243`–`#275`, ~30 observations
in roughly ten minutes of wall-clock time, with three superseded fixes before the root
cause.

**Most-modified file in the recorded window.** `recoup/report/render.py` — the report
renderer, touched more than any other. Fitting for a project whose central anxiety was
whether its own reporting could be trusted.

---

## 10. Lessons and Meta-Observations

**The decision log was the highest-leverage artefact, and it was the second commit.**
Sixty-five entries recording what was chosen, why, and what it costs if wrong. It is what
makes this history reconstructable at all, and it existed before the code did.

**The project's characteristic move is volunteering its own losses.** Recoup loses ₹30,490
on `TRANSIENT_ISSUER` and gives up ₹499 on `RISK_DECLINE`, and both appear in the headline
table rather than a footnote. The AI's money contribution is reported as *not replicating*
despite a positive mean. The pay-now link's ₹3,25,341 credit is immediately qualified:
the eligible causes gained ₹1,87,405, so two-fifths of the headline is money the ladder
would have earned anyway (`#59`). A reader learns to trust the numbers precisely because
the project keeps pointing at the ones that flatter it.

**Every serious defect was in the measurement, not the product.** Four times. The
disconnected model, the stacking audit log, the broken offline replay, the freeze that
did not cover prompts. The pipeline was fine each time; the instrument was not. For a
project whose entire claim is a measured comparison, that is the correct place for the
bugs to be *found* and the worst place for them to have *stayed*.

**Three false fixes is a signal to stop guessing.** The evening-demo saga only turned
when `#250` recorded "root cause not yet identified" and `#252` established the failure
pre-existed the edits. Everything before that was a plausible fix to a problem nobody had
located yet.

**Claims rot silently, and the fix is to make them executable.** The offline-reproduction
promise was true when written and quietly false three days later. The entity shape was
"verified against the live account" — in a commit message nobody could re-run. The
project's answer in both cases was to convert the claim into something a test could fail
on: a recorded fixture (`#139`), a refusing runner (`#41`), a registered freeze (`#86`).
That is the single most transferable lesson in this history.

**What a new developer should read first.** `docs/decisions.md`, then
`recoup/policy/authorized.py` for the gate, then `recoup/audit/log.py` for the spine.
The graph makes the same recommendation numerically: `FailureClass` at 221 edges,
`ActionType` at 116, `Band` at 85 — the closed vocabularies are the architecture, and
everything else hangs off them.

---

*Generated from claude-mem observation IDs `#1`–`#329` and session summaries S1–S25,
cross-checked against 87 commits. Figures cited from the store are read values; figures
labelled as modelled are marked as such in section 8.*
