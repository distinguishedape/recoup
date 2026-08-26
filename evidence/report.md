# Recoup - measured recovery against the baseline retry ladder

Cohort of 2000 failed subscription charges, seed 3, configuration hash `7aa7962cac907ba0`.

## Headline (Mid band)

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹18,62,629.00 | ₹13,998.00 | ₹18,48,631.00 | 45.7% | 5.36 | 1800 | 35.2h |
| Recoup | ₹20,32,563.00 | ₹8,403.70 | ₹20,24,159.30 | 49.2% | 3.96 | 438 | 36.5h |

Gross lift ₹1,69,934.00 - net lift ₹1,75,528.30 - recovery rate +3.5pp - wasted attempts avoided 1362.

## Findings across the sensitivity sweep

| Finding | Low | Mid | High | Verdict | Note |
|---|---|---|---|---|---|
| gross_recovered | ₹1,16,940.00 | ₹1,69,934.00 | ₹1,70,917.00 | **survives** | holds at every band |
| net_recovered | ₹1,22,806.85 | ₹1,75,528.30 | ₹1,76,288.20 | **survives** | holds at every band |
| recovery_rate | +3.1pp | +3.5pp | +4.4pp | **survives** | holds at every band |
| attempts_per_recovery | -2.00 | -1.40 | -1.13 | **survives** | holds at every band |
| wasted_attempts | +1350.00 | +1362.00 | +1372.00 | **survives** | holds at every band |

A finding is reported as surviving only if it points the right way at **all three** bands. A lift that appears only at the High band is reported as not surviving, however large it is.

## Does it replicate?

The same experiment run over 4 independent cohorts of 2000 subjects each. A finding **replicates** only if it survives the Low/Mid/High sweep in *every* cohort. Surviving in most of them is reported as not replicating, for the same reason a lift that appears only at the optimistic band is reported as not surviving: averaging is how a result that depends on luck gets laundered into one that looks robust.

| Finding | seed 3 | seed 11 | seed 29 | seed 47 | mean | Verdict |
|---|---|---|---|---|---|---|
| gross_recovered | ₹1,69,934.00 | ₹1,60,401.00 | ₹1,32,944.00 | ₹1,30,928.00 | ₹1,48,551.75 | **replicates** |
| net_recovered | ₹1,75,528.30 | ₹1,65,914.20 | ₹1,38,438.05 | ₹1,36,586.20 | ₹1,54,116.68 | **replicates** |
| recovery_rate | +3.5pp | +5.3pp | +3.0pp | +3.8pp | +3.9pp | **replicates** |
| attempts_per_recovery | -1.40 | -1.45 | -1.17 | -1.36 | -1.35 | **replicates** |
| wasted_attempts | +1362.00 | +1330.00 | +1359.00 | +1382.00 | +1358.25 | **replicates** |


## Per-band detail

### Low band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹14,41,328.00 | ₹15,090.00 | ₹14,26,238.00 | 35.3% | 7.49 | 1836 | 37.4h |
| Recoup | ₹15,58,268.00 | ₹9,223.15 | ₹15,49,044.85 | 38.4% | 5.48 | 486 | 39.2h |

### Mid band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹18,62,629.00 | ₹13,998.00 | ₹18,48,631.00 | 45.7% | 5.36 | 1800 | 35.2h |
| Recoup | ₹20,32,563.00 | ₹8,403.70 | ₹20,24,159.30 | 49.2% | 3.96 | 438 | 36.5h |

### High band

| Arm | Gross recovered | Cost | Net recovered | Recovery rate | Attempts / recovery | Wasted attempts | Mean time to recovery |
|---|---|---|---|---|---|---|---|
| Baseline ladder | ₹22,55,963.00 | ₹13,023.00 | ₹22,42,940.00 | 54.4% | 4.19 | 1767 | 33.6h |
| Recoup | ₹24,26,880.00 | ₹7,651.80 | ₹24,19,228.20 | 58.8% | 3.06 | 395 | 33.6h |

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
