"""The deck must read the published bundle, never recompute it.

A dashboard that derives its own totals can disagree with the report, and the
disagreement is silent -- it looks like a rounding difference until someone
checks. Every figure on a slide is read straight out of `sweep.json` and
`replication.json`, which are the frozen, committed numbers.
"""

import json
from pathlib import Path

import pytest

from scripts.build_console import experiment_data

BUNDLE = Path("evidence")


@pytest.fixture(scope="module")
def bundle():
    sweep = json.loads((BUNDLE / "sweep.json").read_text(encoding="utf-8"))
    replication = json.loads((BUNDLE / "replication.json").read_text(encoding="utf-8"))
    return experiment_data(sweep, replication)


def test_the_headline_is_read_from_the_bundle_not_recomputed(bundle):
    sweep = json.loads((BUNDLE / "sweep.json").read_text(encoding="utf-8"))
    mid = sweep["results"]["mid"]
    assert bundle["bands"]["mid"]["treatment"]["net"] == mid["treatment"]["net_recovered_paise"]
    assert bundle["bands"]["mid"]["control"]["net"] == mid["control"]["net_recovered_paise"]


def test_every_cause_carries_an_explicit_sign(bundle):
    """Red and green are the one pair colourblind readers cannot separate, so the
    sign is carried by the data and not left to the fill colour."""
    for row in bundle["by_class"]:
        assert row["sign"] in ("+", "-", ""), row
        if row["delta"] > 0:
            assert row["sign"] == "+"
        elif row["delta"] < 0:
            assert row["sign"] == "-"


def test_the_causes_where_it_loses_are_present_and_negative(bundle):
    """Volunteering the losses is the point; a deck that dropped them would be
    the dishonest version of this table."""
    losses = {r["cause"]: r["delta"] for r in bundle["by_class"] if r["delta"] < 0}
    assert "TRANSIENT_ISSUER" in losses
    assert losses["TRANSIENT_ISSUER"] < 0


def test_the_grid_is_three_bands_by_four_cohorts(bundle):
    assert len(bundle["grid"]) == 12
    assert {c["band"] for c in bundle["grid"]} == {"low", "mid", "high"}
    assert len({c["seed"] for c in bundle["grid"]}) == 4


def test_the_grid_marks_whether_each_cell_clears_the_target(bundle):
    assert all(cell["clears"] for cell in bundle["grid"]), (
        "every published cell clears +15%; if this fails the bundle changed"
    )


def test_a_cell_below_target_is_marked_as_missing_it():
    """Guards the guard: if `clears` were hardcoded True the test above would
    pass on any data at all."""
    sweep = json.loads((BUNDLE / "sweep.json").read_text(encoding="utf-8"))
    replication = json.loads((BUNDLE / "replication.json").read_text(encoding="utf-8"))
    # Halve one cohort's treatment net so that cell cannot clear the target.
    doctored = json.loads(json.dumps(replication))
    arm = doctored["sweeps"]["3"]["results"]["low"]["treatment"]
    arm["net_recovered_paise"] = doctored["sweeps"]["3"]["results"]["low"]["control"][
        "net_recovered_paise"
    ]
    data = experiment_data(sweep, doctored)
    doctored_cell = next(c for c in data["grid"] if c["seed"] == 3 and c["band"] == "low")
    assert not doctored_cell["clears"]


def test_the_mechanism_split_sums_to_gross_recovered(bundle):
    sweep = json.loads((BUNDLE / "sweep.json").read_text(encoding="utf-8"))
    gross = sweep["results"]["mid"]["treatment"]["gross_recovered_paise"]
    assert sum(m["treatment"] for m in bundle["by_mechanism"]) == gross
