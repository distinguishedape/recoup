# Recoup - measured recovery against the baseline retry ladder

Cohort of 2000 failed subscription charges, seed 3, configuration hash `7aa7962cac907ba0`.

Measurement inputs hash `1f90b70d9afeb6a0` — the prompts, probability bands, budgets, costs, cohort distribution and schedule. The configuration hash above covers the seed and the cohort; it does not move when a prompt is edited, and an edited prompt re-asks every plan. This digest excludes the model name, which varies with whoever reproduces the run; `frozen_config.json` registers the model as well, in full, so a reader can see exactly what was pre-registered.

## Headline (Mid band)

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹18,35,622.00 | ₹13,902.00 | ₹18,21,720.00 | 46.0% | 5.28 | 1782 | 34.7h |
| Recoup | ₹23,18,879.00 | ₹8,795.30 | ₹23,10,083.70 | 58.7% | 2.46 | 3 | 34.4h |

Gross lift ₹4,83,257.00 - net lift ₹4,88,363.70 - recovery rate +12.7pp - wasted attempts avoided 1779.

## Where the lift comes from

Gross recovered per failure cause, both arms. A total is easy to take on trust; this is the table that says which causes actually earn it, and which ones the agent gives up on deliberately.

| Cause | Baseline | Recoup | Difference |
|---|---|---|---|
| `INSUFFICIENT_FUNDS` | ₹10,05,017.00 | ₹11,93,917.00 | **₹1,88,900.00** |
| `INSTRUMENT_INVALID` | ₹11,996.00 | ₹2,56,874.00 | **₹2,44,878.00** |
| `MANDATE_REVOKED` | ₹0.00 | ₹0.00 | ₹0.00 |
| `TRANSIENT_ISSUER` | ₹5,34,742.00 | ₹5,04,252.00 | **-₹30,490.00** |
| `RISK_DECLINE` | ₹499.00 | ₹0.00 | -₹499.00 |
| `UNCLASSIFIED` | ₹2,83,368.00 | ₹3,63,836.00 | **₹80,468.00** |

## How far up the ladder subjects travelled

Recovered money by the escalation tier that achieved it. The baseline has no ladder, so everything it recovers sits at a single tier.

| Tier | Baseline | Recoup |
|---|---|---|
| T1 notify | ₹18,35,622.00 | ₹17,36,664.00 |
| T2 request action | ₹0.00 | ₹5,82,215.00 |

## What actually earned the money

Gross recovered by the mechanism that produced it, mid band. A total lift is easy to take on trust; this is the table that says how much of it the pay-now link is responsible for, rather than the retry ladder underneath it. The baseline has no payment link and never will, so its money is entirely `retry`.

| Mechanism | Baseline | Recoup |
|---|---|---|
| `instrument_update` | ₹0.00 | ₹2,56,874.00 |
| `pay_now_link` | ₹0.00 | ₹3,25,341.00 |
| `retry` | ₹18,35,622.00 | ₹17,36,664.00 |

## Findings across the sensitivity sweep

| Finding | Low | Mid | High | Verdict | Note |
|---|---|---|---|---|---|
| gross_recovered | ₹3,42,330.00 | ₹4,83,257.00 | ₹6,54,171.00 | **survives** | holds at every band |
| net_recovered | ₹3,47,351.40 | ₹4,88,363.70 | ₹6,59,525.10 | **survives** | holds at every band |
| recovery_rate | +8.9pp | +12.7pp | +17.2pp | **survives** | holds at every band |
| attempts_per_recovery | -3.62 | -2.82 | -2.44 | **survives** | holds at every band |
| wasted_attempts | +1796.00 | +1779.00 | +1752.00 | **survives** | holds at every band |

A finding is reported as surviving only if it points the right way at **all three** bands. A lift that appears only at the High band is reported as not surviving, however large it is.

## Does it replicate?

The same experiment run over 4 independent cohorts of 2000 subjects each. A finding **replicates** only if it survives the Low/Mid/High sweep in *every* cohort. Surviving in most of them is reported as not replicating, for the same reason a lift that appears only at the optimistic band is reported as not surviving: averaging is how a result that depends on luck gets laundered into one that looks robust.

| Finding | seed 3 | seed 11 | seed 29 | seed 47 | mean | Verdict |
|---|---|---|---|---|---|---|
| gross_recovered | ₹4,83,257.00 | ₹5,70,235.00 | ₹5,70,254.00 | ₹5,11,747.00 | ₹5,33,873.25 | **replicates** |
| net_recovered | ₹4,88,363.70 | ₹5,75,328.65 | ₹5,75,323.90 | ₹5,16,700.00 | ₹5,38,929.06 | **replicates** |
| recovery_rate | +12.7pp | +14.0pp | +13.0pp | +13.2pp | +13.3pp | **replicates** |
| attempts_per_recovery | -2.82 | -2.90 | -2.72 | -2.64 | -2.77 | **replicates** |
| wasted_attempts | +1779.00 | +1755.00 | +1772.00 | +1686.00 | +1748.00 | **replicates** |


## Per-band detail

### Low band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹14,25,815.00 | ₹14,964.00 | ₹14,10,851.00 | 35.9% | 7.28 | 1797 | 36.5h |
| Recoup | ₹17,68,145.00 | ₹9,942.60 | ₹17,58,202.40 | 44.8% | 3.66 | 1 | 38.7h |

### Mid band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹18,35,622.00 | ₹13,902.00 | ₹18,21,720.00 | 46.0% | 5.28 | 1782 | 34.7h |
| Recoup | ₹23,18,879.00 | ₹8,795.30 | ₹23,10,083.70 | 58.7% | 2.46 | 3 | 34.4h |

### High band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹21,75,966.00 | ₹13,029.00 | ₹21,62,937.00 | 54.2% | 4.20 | 1758 | 33.5h |
| Recoup | ₹28,30,137.00 | ₹7,674.90 | ₹28,22,462.10 | 71.4% | 1.76 | 6 | 30.3h |

## The baseline this was compared against

Four total charge attempts - the initial failure plus three retries at T+1, T+2, T+3 days - context-blind, with no intervention beyond Razorpay's own failure email, terminating in `halted`. Day-stepping is used rather than the test-mode 10-minute/1-hour ladder because the latter reads as test acceleration rather than production behaviour, and Razorpay's own documentation is inconsistent between the two.

## Assumptions

Recovery outcomes are **simulated**. Razorpay test mode offers only Charge-as-Success and Charge-as-Failure from the Dashboard; it cannot inject a specific decline reason, and it exposes no manual-retry API for domestic cards. Every recovery probability below is a stated assumption drawn from published dunning benchmarks, not a measurement. The sweep exists because of this.

**Cohort class distribution**

| Class | Share |
|---|---|
| `INSUFFICIENT_FUNDS` | 40% |
| `INSTRUMENT_INVALID` | 20% |
| `TRANSIENT_ISSUER` | 15% |
| `UNCLASSIFIED` | 15% |
| `MANDATE_REVOKED` | 5% |
| `RISK_DECLINE` | 5% |

**Plan amounts** drawn uniformly from ₹499.00, ₹999.00, ₹1,999.00, ₹4,999.00.

**Attempt cost**: ₹3.00 per charge attempt, ₹0.20 per email, ₹0.25 per sms.

**Recovery probability bands**

| Class | Low | Mid | High |
|---|---|---|---|
| `INSUFFICIENT_FUNDS` | 30.0% | 45.0% | 60.0% |
| `INSTRUMENT_INVALID` | 0.0% | 1.0% | 2.0% |
| `MANDATE_REVOKED` | 0.0% | 0.0% | 0.0% |
| `TRANSIENT_ISSUER` | 55.0% | 70.0% | 80.0% |
| `RISK_DECLINE` | 0.0% | 1.5% | 3.0% |
| `UNCLASSIFIED` | 20.0% | 30.0% | 40.0% |
| instrument-update conversion | 20% | 35% | 50% |
| pay-now link conversion | 12% | 22% | 34% |

**Per-class attempt budgets**

| Class | Charge retries | Contacts |
|---|---|---|
| `INSUFFICIENT_FUNDS` | 3 | 2 |
| `INSTRUMENT_INVALID` | 0 | 2 |
| `MANDATE_REVOKED` | 0 | 0 |
| `TRANSIENT_ISSUER` | 3 | 0 |
| `RISK_DECLINE` | 0 | 0 |
| `UNCLASSIFIED` | 3 | 2 |

## Definitions

- **Recovery rate** excludes voluntary churn from the denominator. A customer who revoked their mandate did not fail to be recovered; they left.
- **Wasted attempts** are charge attempts spent on a cause a retry cannot fix (`INSTRUMENT_INVALID`, `MANDATE_REVOKED`, `RISK_DECLINE`) on a subject that never recovered. A charge after a successful instrument update is not counted as waste.
- **Net recovered** is gross recovered minus every rupee spent across *all* subjects in the arm, recovered or not.
