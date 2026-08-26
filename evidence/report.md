# Recoup - measured recovery against the baseline retry ladder

Cohort of 200 failed subscription charges, seed 3, configuration hash `53ffabac5f4d18f0`.

## Headline (Mid band)

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹2,37,899.00 | ₹1,293.00 | ₹2,36,606.00 | 53.4% | 4.27 | 168 | 31.8h |
| Recoup | ₹2,38,398.00 | ₹736.20 | ₹2,37,661.80 | 54.0% | 3.37 | 42 | 25.8h |

Gross lift ₹499.00 - net lift ₹1,055.80 - recovery rate +0.5pp - wasted attempts avoided 126.

## Findings across the sensitivity sweep

| Finding | Low | Mid | High | Verdict | Note |
|---|---|---|---|---|---|
| gross_recovered | ₹5,997.00 | ₹499.00 | ₹2,996.00 | **survives** | holds at every band |
| net_recovered | ₹6,592.60 | ₹1,055.80 | ₹3,514.20 | **survives** | holds at every band |
| recovery_rate | +1.6pp | +0.5pp | +2.1pp | **survives** | holds at every band |
| attempts_per_recovery | -1.31 | -0.89 | -0.75 | **survives** | holds at every band |
| wasted_attempts | +128.00 | +126.00 | +130.00 | **survives** | holds at every band |

A finding is reported as surviving only if it points the right way at **all three** bands. A lift that appears only at the High band is reported as not surviving, however large it is.

## Does it replicate?

The same experiment run over 4 independent cohorts of 200 subjects each. A finding **replicates** only if it survives the Low/Mid/High sweep in *every* cohort. Surviving in most of them is reported as not replicating, for the same reason a lift that appears only at the optimistic band is reported as not surviving: averaging is how a result that depends on luck gets laundered into one that looks robust.

| Finding | seed 3 | seed 11 | seed 29 | seed 47 | mean | Verdict |
|---|---|---|---|---|---|---|
| gross_recovered | ₹499.00 | -₹4,001.00 | -₹998.00 | -₹16,992.00 | -₹5,373.00 | does not replicate |
| net_recovered | ₹1,055.80 | -₹3,400.00 | -₹406.40 | -₹16,389.00 | -₹4,784.90 | does not replicate |
| recovery_rate | +0.5pp | +0.5pp | -1.0pp | -4.3pp | -1.1pp | does not replicate |
| attempts_per_recovery | -0.89 | -1.08 | -0.87 | -0.54 | -0.85 | **replicates** |
| wasted_attempts | +126.00 | +123.00 | +127.00 | +126.00 | +125.50 | **replicates** |

- `gross_recovered`: survives in 1 of 4 cohorts (not in seed 11, 29, 47), so it is reported as not replicating
- `net_recovered`: survives in 1 of 4 cohorts (not in seed 11, 29, 47), so it is reported as not replicating
- `recovery_rate`: survives in 1 of 4 cohorts (not in seed 11, 29, 47), so it is reported as not replicating

## Per-band detail

### Low band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹2,02,415.00 | ₹1,383.00 | ₹2,01,032.00 | 45.0% | 5.42 | 171 | 32.8h |
| Recoup | ₹2,08,412.00 | ₹787.40 | ₹2,07,624.60 | 46.6% | 4.11 | 43 | 27.2h |

### Mid band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹2,37,899.00 | ₹1,293.00 | ₹2,36,606.00 | 53.4% | 4.27 | 168 | 31.8h |
| Recoup | ₹2,38,398.00 | ₹736.20 | ₹2,37,661.80 | 54.0% | 3.37 | 42 | 25.8h |

### High band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹2,72,384.00 | ₹1,203.00 | ₹2,71,181.00 | 61.4% | 3.46 | 168 | 30.8h |
| Recoup | ₹2,75,380.00 | ₹684.80 | ₹2,74,695.20 | 63.5% | 2.71 | 38 | 25.7h |

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
| `INSUFFICIENT_FUNDS` | 2 | 1 |
| `INSTRUMENT_INVALID` | 0 | 2 |
| `MANDATE_REVOKED` | 0 | 0 |
| `TRANSIENT_ISSUER` | 2 | 0 |
| `RISK_DECLINE` | 0 | 0 |
| `UNCLASSIFIED` | 3 | 1 |

## Definitions

- **Recovery rate** excludes voluntary churn from the denominator. A customer who revoked their mandate did not fail to be recovered; they left.
- **Wasted attempts** are charge attempts spent on a cause a retry cannot fix (`INSTRUMENT_INVALID`, `MANDATE_REVOKED`, `RISK_DECLINE`) on a subject that never recovered. A charge after a successful instrument update is not counted as waste.
- **Net recovered** is gross recovered minus every rupee spent across *all* subjects in the arm, recovered or not.
