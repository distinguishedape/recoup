# Recoup - measured recovery against the baseline retry ladder

Cohort of 2000 failed subscription charges, seed 3, configuration hash `7aa7962cac907ba0`.

## Headline (Mid band)

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹18,35,622.00 | ₹13,902.00 | ₹18,21,720.00 | 46.0% | 5.28 | 1782 | 34.7h |
| Recoup | ₹20,85,999.00 | ₹9,362.10 | ₹20,76,636.90 | 52.4% | 3.00 | 3 | 39.4h |

Gross lift ₹2,50,377.00 - net lift ₹2,54,916.90 - recovery rate +6.4pp - wasted attempts avoided 1779.

## Where the lift comes from

Gross recovered per failure cause, both arms. A total is easy to take on trust; this is the table that says which causes actually earn it, and which ones the agent gives up on deliberately.

| Cause | Baseline | Recoup | Difference |
|---|---|---|---|
| `INSUFFICIENT_FUNDS` | ₹10,05,017.00 | ₹10,82,483.00 | **₹77,466.00** |
| `INSTRUMENT_INVALID` | ₹11,996.00 | ₹2,11,399.00 | **₹1,99,403.00** |
| `MANDATE_REVOKED` | ₹0.00 | ₹0.00 | ₹0.00 |
| `TRANSIENT_ISSUER` | ₹5,34,742.00 | ₹5,04,252.00 | **-₹30,490.00** |
| `RISK_DECLINE` | ₹499.00 | ₹0.00 | -₹499.00 |
| `UNCLASSIFIED` | ₹2,83,368.00 | ₹2,87,865.00 | **₹4,497.00** |

## How far up the ladder subjects travelled

Recovered money by the escalation tier that achieved it. The baseline has no ladder, so everything it recovers sits at a single tier.

| Tier | Baseline | Recoup |
|---|---|---|
| T1 notify | ₹18,35,622.00 | ₹17,83,136.00 |
| T2 request action | ₹0.00 | ₹3,02,863.00 |

## Findings across the sensitivity sweep

| Finding | Low | Mid | High | Verdict | Note |
|---|---|---|---|---|---|
| gross_recovered | ₹1,60,417.00 | ₹2,50,377.00 | ₹3,35,330.00 | **survives** | holds at every band |
| net_recovered | ₹1,65,137.25 | ₹2,54,916.90 | ₹3,39,771.80 | **survives** | holds at every band |
| recovery_rate | +4.3pp | +6.4pp | +8.9pp | **survives** | holds at every band |
| attempts_per_recovery | -2.99 | -2.28 | -1.92 | **survives** | holds at every band |
| wasted_attempts | +1796.00 | +1779.00 | +1754.00 | **survives** | holds at every band |

A finding is reported as surviving only if it points the right way at **all three** bands. A lift that appears only at the High band is reported as not surviving, however large it is.

## Does it replicate?

The same experiment run over 4 independent cohorts of 2000 subjects each. A finding **replicates** only if it survives the Low/Mid/High sweep in *every* cohort. Surviving in most of them is reported as not replicating, for the same reason a lift that appears only at the optimistic band is reported as not surviving: averaging is how a result that depends on luck gets laundered into one that looks robust.

| Finding | seed 3 | seed 11 | seed 29 | seed 47 | mean | Verdict |
|---|---|---|---|---|---|---|
| gross_recovered | ₹2,50,377.00 | ₹3,40,842.00 | ₹3,08,365.00 | ₹2,55,368.00 | ₹2,88,738.00 | **replicates** |
| net_recovered | ₹2,54,916.90 | ₹3,45,371.35 | ₹3,12,885.75 | ₹2,59,659.85 | ₹2,93,208.46 | **replicates** |
| recovery_rate | +6.4pp | +8.4pp | +7.1pp | +6.9pp | +7.2pp | **replicates** |
| attempts_per_recovery | -2.28 | -2.41 | -2.23 | -2.10 | -2.26 | **replicates** |
| wasted_attempts | +1779.00 | +1755.00 | +1773.00 | +1687.00 | +1748.50 | **replicates** |


## Per-band detail

### Low band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹14,25,815.00 | ₹14,964.00 | ₹14,10,851.00 | 35.9% | 7.28 | 1797 | 36.5h |
| Recoup | ₹15,86,232.00 | ₹10,243.75 | ₹15,75,988.25 | 40.2% | 4.29 | 1 | 42.4h |

### Mid band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹18,35,622.00 | ₹13,902.00 | ₹18,21,720.00 | 46.0% | 5.28 | 1782 | 34.7h |
| Recoup | ₹20,85,999.00 | ₹9,362.10 | ₹20,76,636.90 | 52.4% | 3.00 | 3 | 39.4h |

### High band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹21,75,966.00 | ₹13,029.00 | ₹21,62,937.00 | 54.2% | 4.20 | 1758 | 33.5h |
| Recoup | ₹25,11,296.00 | ₹8,587.20 | ₹25,02,708.80 | 63.1% | 2.28 | 4 | 36.7h |

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
