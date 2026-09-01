# The schedule ablation

The treatment retries at 24/72/120h and the control at 24/48/72h, against a
timing model that rewards waiting for a shortfall. That is an asymmetric input
the Low/Mid/High sweep never varies: the bands move retry success and conversion,
which scale both arms, so twelve cells test one schedule assumption twelve times.

This removes the advantage rather than arguing about it. The treatment is forced
onto the control's own 24/48/72, leaving the choice of *channel* -- pay-now link,
card-update request, silence, hard stop -- as the only remaining difference.

Mid band, 2,000 subjects per arm, deterministic planner on both sides.

| Cohort seed | As shipped | Schedule-matched |
|---|---|---|
| 3 | +27.56% | +25.17% |
| 11 | +30.66% | +26.73% |
| 29 | +29.58% | +27.19% |
| 47 | +26.30% | +24.32% |

**Schedule-matched lift survives in all 4 cohorts, +24.32% to +27.19%.**

About 91% of the published lift remains when the schedule advantage is
removed entirely. The later retries are worth roughly the remainder -- real, and
not the source of the result. What earns it is refusing to retry a dead card and
offering a link to someone who was short of money, which is the claim the product
actually makes.

The honest headline is therefore not the raw number but this one: the lift is
channel choice, and it holds with the schedule handed back to the baseline.

Regenerate with `python -m scripts.ablate_schedule`.
