"""The lift has to survive giving the schedule back to the baseline.

The treatment differs from the control in two ways at once: which *channel* it
picks for a cause, and *when* it retries -- 24/72/120h against 24/48/72h. The
timing model rewards the later schedule for a shortfall, and the Low/Mid/High
sweep never varies it, because the bands move retry success and conversion and
those scale both arms. The one asymmetric input is constant in all twelve cells.

``scripts/ablate_schedule`` removes the advantage and publishes the result over
four cohorts. This is the same measurement shrunk to test size, so a change that
quietly makes the lift depend on the schedule fails here rather than in a room.

ponytail: one paired run at a fraction of the published cohort, so the assertion
is the direction and the survival, not the exact percentage. The four-seed
evidence at full size lives in ``evidence/schedule-ablation.md``.
"""

from datetime import datetime, timezone
from pathlib import Path

from recoup.experiment.harness import run_paired_experiment
from recoup.models.enums import Band
from recoup.orchestrate.runner import RunConfig
from recoup.plan import fallback
from scripts.ablate_schedule import CONTROL_DELAYS_HOURS

COHORT_SIZE = 400
SEED = 3
START = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)

#: The project's own bar for a finding, from the sweep's survival rule.
TARGET_LIFT_PERCENT = 15.0


def test_lift_survives_the_control_s_own_retry_schedule(tmp_path, monkeypatch):
    for name in (
        "FUNDS_RETRY_DELAYS_HOURS",
        "TRANSIENT_RETRY_DELAYS_HOURS",
        "UNCLASSIFIED_RETRY_DELAYS_HOURS",
    ):
        monkeypatch.setattr(fallback, name, CONTROL_DELAYS_HOURS)

    config = RunConfig(
        run_id="schedule_ablation",
        seed=SEED,
        band=Band.MID,
        cohort_size=COHORT_SIZE,
        start_at=START,
    )
    result = run_paired_experiment(config, Path(tmp_path), llm_client=None)

    control = result.control.net_recovered_paise
    treatment = result.treatment.net_recovered_paise
    lift = (treatment - control) / control * 100.0

    assert lift > TARGET_LIFT_PERCENT, (
        f"with the schedule advantage removed the lift is {lift:+.2f}%, below the "
        f"{TARGET_LIFT_PERCENT:.0f}% bar -- the result would then be a timing artefact "
        "rather than the channel choice the product claims"
    )
