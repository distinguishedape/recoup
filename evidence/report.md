# Recoup - measured recovery against the baseline retry ladder

Cohort of 2000 failed subscription charges, seed 3, configuration hash `7aa7962cac907ba0`.

## Headline (Mid band)

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹18,35,622.00 | ₹13,902.00 | ₹18,21,720.00 | 46.0% | 5.28 | 1782 | 34.7h |
| Recoup | ₹20,73,009.00 | ₹9,777.00 | ₹20,63,232.00 | 51.9% | 3.17 | 2 | 40.1h |

Gross lift ₹2,37,387.00 - net lift ₹2,41,512.00 - recovery rate +5.9pp - wasted attempts avoided 1780.

## Where the lift comes from

Gross recovered per failure cause, both arms. A total is easy to take on trust; this is the table that says which causes actually earn it, and which ones the agent gives up on deliberately.

| Cause | Baseline | Recoup | Difference |
|---|---|---|---|
| `INSUFFICIENT_FUNDS` | ₹10,05,017.00 | ₹8,87,082.00 | **-₹1,17,935.00** |
| `INSTRUMENT_INVALID` | ₹11,996.00 | ₹1,98,409.00 | **₹1,86,413.00** |
| `MANDATE_REVOKED` | ₹0.00 | ₹0.00 | ₹0.00 |
| `TRANSIENT_ISSUER` | ₹5,34,742.00 | ₹4,61,274.00 | **-₹73,468.00** |
| `RISK_DECLINE` | ₹499.00 | ₹0.00 | -₹499.00 |
| `UNCLASSIFIED` | ₹2,83,368.00 | ₹5,26,244.00 | **₹2,42,876.00** |

## How far up the ladder subjects travelled

Recovered money by the escalation tier that achieved it. The baseline has no ladder, so everything it recovers sits at a single tier.

| Tier | Baseline | Recoup |
|---|---|---|
| T1 notify | ₹18,35,622.00 | ₹18,21,620.00 |
| T2 request action | ₹0.00 | ₹2,51,389.00 |

## Findings across the sensitivity sweep

| Finding | Low | Mid | High | Verdict | Note |
|---|---|---|---|---|---|
| gross_recovered | ₹1,79,410.00 | ₹2,37,387.00 | ₹2,97,850.00 | **survives** | holds at every band |
| net_recovered | ₹1,83,651.60 | ₹2,41,512.00 | ₹3,01,911.20 | **survives** | holds at every band |
| recovery_rate | +4.7pp | +5.9pp | +7.9pp | **survives** | holds at every band |
| attempts_per_recovery | -2.83 | -2.11 | -1.77 | **survives** | holds at every band |
| wasted_attempts | +1796.00 | +1780.00 | +1755.00 | **survives** | holds at every band |

A finding is reported as surviving only if it points the right way at **all three** bands. A lift that appears only at the High band is reported as not surviving, however large it is.

## Does it replicate?

The same experiment run over 4 independent cohorts of 2000 subjects each. A finding **replicates** only if it survives the Low/Mid/High sweep in *every* cohort. Surviving in most of them is reported as not replicating, for the same reason a lift that appears only at the optimistic band is reported as not surviving: averaging is how a result that depends on luck gets laundered into one that looks robust.

| Finding | seed 3 | seed 11 | seed 29 | seed 47 | mean | Verdict |
|---|---|---|---|---|---|---|
| gross_recovered | ₹2,49,379.00 | ₹3,40,842.00 | ₹3,08,365.00 | ₹2,55,368.00 | ₹2,88,488.50 | **replicates** |
| net_recovered | ₹2,53,819.40 | ₹3,45,371.35 | ₹3,12,885.75 | ₹2,59,659.85 | ₹2,92,934.08 | **replicates** |
| recovery_rate | +6.3pp | +8.4pp | +7.1pp | +6.9pp | +7.2pp | **replicates** |
| attempts_per_recovery | -2.24 | -2.41 | -2.23 | -2.10 | -2.25 | **replicates** |
| wasted_attempts | +1779.00 | +1755.00 | +1773.00 | +1687.00 | +1748.50 | **replicates** |


## Per-band detail

### Low band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹14,25,815.00 | ₹14,964.00 | ₹14,10,851.00 | 35.9% | 7.28 | 1797 | 36.5h |
| Recoup | ₹16,05,225.00 | ₹10,722.40 | ₹15,94,502.60 | 40.6% | 4.45 | 1 | 42.6h |

### Mid band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹18,35,622.00 | ₹13,902.00 | ₹18,21,720.00 | 46.0% | 5.28 | 1782 | 34.7h |
| Recoup | ₹20,73,009.00 | ₹9,777.00 | ₹20,63,232.00 | 51.9% | 3.17 | 2 | 40.1h |

### High band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹21,75,966.00 | ₹13,029.00 | ₹21,62,937.00 | 54.2% | 4.20 | 1758 | 33.5h |
| Recoup | ₹24,73,816.00 | ₹8,967.80 | ₹24,64,848.20 | 62.0% | 2.43 | 3 | 37.2h |

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

**Per-class attempt budgets**

| Class | Charge retries | Contacts |
|---|---|---|
| `INSUFFICIENT_FUNDS` | 3 | 1 |
| `INSTRUMENT_INVALID` | 0 | 2 |
| `MANDATE_REVOKED` | 0 | 0 |
| `TRANSIENT_ISSUER` | 3 | 0 |
| `RISK_DECLINE` | 0 | 0 |
| `UNCLASSIFIED` | 3 | 1 |

## Definitions

- **Recovery rate** excludes voluntary churn from the denominator. A customer who revoked their mandate did not fail to be recovered; they left.
- **Wasted attempts** are charge attempts spent on a cause a retry cannot fix (`INSTRUMENT_INVALID`, `MANDATE_REVOKED`, `RISK_DECLINE`) on a subject that never recovered. A charge after a successful instrument update is not counted as waste.
- **Net recovered** is gross recovered minus every rupee spent across *all* subjects in the arm, recovered or not.
