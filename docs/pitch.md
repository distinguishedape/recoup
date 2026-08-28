# Recoup — five-minute running order

One page for the person presenting. The wording for the three moments that
decide the room is written out verbatim, because those are the ones that go
wrong when improvised.

Judge summary to hand over or put on the last slide:
<https://claude.ai/code/artifact/a42011e0-b5de-4a2c-857a-eabb5ae73ab4>

---

## 0:00 — The problem, one sentence

> "When a subscription auto-debit fails, the standard response is to retry it on
> a fixed schedule — the same three retries whether the customer was two hundred
> rupees short, their card expired in March, or they revoked the mandate last
> week. Two of those three cannot be fixed by retrying."

Stop there. Do not explain dunning. They know.

## 0:30 — The number that needs no methodology

**1,782 wasted attempts become 3.**

Attempts spent on causes a retry cannot fix, on subjects that never recovered.
It is the most legible number you have and it needs no setup. Say it, let it
sit, then move.

## 1:00 — Live demo

```bash
python -m scripts.demo
```

A real failed payment on the Razorpay test account. Narrate along with it: cause
identified, intervention planned, payment link created. Then **switch to the
Razorpay dashboard and show the link is there.** That is the moment that
converts "simulated" from a doubt into a scoped caveat.

**If the window is shut** (before 08:00 or after 19:00 IST) the demo shows the
contact being denied by the compliance rule and rescheduled to the next
permitted hour, then the link landing anyway. Do not apologise for this — it is
a better trace than the happy path. Say:

> "That is the contact window refusing an action in front of you. It reschedules
> rather than dropping it, and the money still arrives."

**Fallback if the network dies:** `python -m scripts.demo --dry-run`. Say
plainly "this is a recording of a run we made earlier." Never imply it is live.

## 2:00 — The replay

```bash
python -m scripts.replay sub_0429 --audit-db artifacts/audit/mid/treatment.db
```

One customer's whole story out of an append-only log with no update path and no
delete path. Point at the blocked line:

> "Every action, and for every blocked action the rule that blocked it. This one
> was denied by the contact window at 19:02 and rescheduled to eight the next
> morning. Then the customer paid."

Ten seconds. No other submission will have this.

## 2:30 — Where the money comes from, and where we lose

Per-cause table. Dead cards: **₹11,996 → ₹2,56,874**, twenty-onefold.

Then, unprompted, before anyone asks:

> "We lose on `TRANSIENT_ISSUER` by ₹30,490. An outage is the one cause a plain
> retry ladder already suits, and we spend fewer attempts on it by design. And
> we give up ₹499 on `RISK_DECLINE` on purpose, because that routes to a human
> instead of arguing with a bank's fraud decision."

Volunteering the losses is what makes the wins credible. This is also the answer
to *"why can't Razorpay just build this into the dashboard?"* — because the right
action depends on the cause, and the cause determines whether any action helps.

## 3:15 — Where the AI sits

> "A lookup table resolves 28 decline reasons — free, instant, and incapable of
> hallucinating. Only the reasons Razorpay itself documents as not disclosing
> the cause reach the model: 84.5% to 99.4%, on six API calls. And everything it
> returns is validated against closed enums before anything acts on it — asked
> to invent a failure class or retry a dead card five times, every proposal is
> rejected and the deterministic planner runs instead."

Do **not** volunteer the money ablation here. It is honestly reported as not
replicating in the report, where a judge who digs will find it and trust you
more. A null result offered mid-pitch costs the room.

## 3:45 — Compliance

> "`MANDATE_REVOKED` → stop. Never charges, never messages. Six rules gate every
> action and each one names itself when it denies."

For a payments company under RBI e-mandate rules, this is the line that reads as
seniority rather than enthusiasm.

## 4:00 — The honesty line, once

> "Recovery outcomes are simulated, and here is why: we paid all eight of
> Razorpay's documented error-scenario cards, and all eight returned one
> indistinguishable string. A classifier cannot be exercised against real
> declines that carry no cause. So we swept every probability across three bands
> and four cohorts, and we report a finding only if it holds in every one."

Deliver this **before** anyone forms the objection. Once, then move on.

## 4:30 — The ask

> "The one thing we need that Razorpay does not expose: manual retry for
> subscription invoices on domestic cards. Everything else in this pipeline is
> live today."

Telling a platform team which of their own gaps blocks the feature is a stronger
close than asking for a prize.

---

## Cut from the spoken pitch

All of it earns marks in the repo. None survives a five-minute clock.

- The sweep methodology and the replication rules
- The decision log (65 entries)
- The four defects found in our own measurement pipeline — **one line at most**,
  if it fits: *"we audited our own measurement pipeline and published what we
  found."* Any longer and rigour reads as instability.
- The measurement-inputs freeze and the drift guard
- The architecture diagram

Last slide: the repo, and the judge summary link at the top of this file.

## If you have sixty seconds, not five

Numbers, demo, ask. Nothing else.

## Known gaps, so you are not surprised

- `evidence/demo-transcript.md` is not recorded yet, so `--dry-run` has nothing
  to replay. Recording it needs a mandate authorised in a checkout form:
  `python -m scripts.setup_test_subscription --amount-paise 99900`, open the
  auth link, pay with a test card, then fail a charge from the Dashboard.
- The demo needs a halted or pending subscription on the account. Same fix.
