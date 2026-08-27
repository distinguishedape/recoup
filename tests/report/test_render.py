import json
from datetime import datetime, timezone

import pytest

from recoup.execute.probabilities import BANDS
from recoup.experiment.sweep import run_sweep
from recoup.models.enums import Band
from recoup.orchestrate.runner import RunConfig, config_hash
from recoup.report.render import format_rupees, render_report, write_bundle

START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def sweep(tmp_path_factory):
    config = RunConfig(run_id="report", seed=3, band=Band.MID, cohort_size=200, start_at=START)
    return run_sweep(config, tmp_path_factory.mktemp("sweep"))


@pytest.mark.parametrize(("paise", "expected"), [
    (0, "₹0.00"), (5, "₹0.05"), (99900, "₹999.00"),
    (199900, "₹1,999.00"), (100000000, "₹10,00,000.00"), (-940, "-₹9.40"),
])
def test_rupees_are_formatted_exactly(paise, expected):
    assert format_rupees(paise) == expected


def test_the_report_leads_with_money(sweep):
    report = render_report(sweep)
    assert report.index("Gross") < report.index("Recovery rate")


def test_the_report_names_both_arms(sweep):
    report = render_report(sweep).lower()
    assert "baseline" in report and "recoup" in report


def test_the_report_prints_every_band(sweep):
    report = render_report(sweep)
    assert all(b in report for b in ("Low", "Mid", "High"))


def test_the_report_states_whether_each_finding_survives(sweep):
    report = render_report(sweep)
    assert all(f.name in report for f in sweep.findings)
    assert "survives" in report.lower()


def test_a_finding_that_does_not_survive_is_labelled_as_such(sweep):
    report = render_report(sweep)
    for finding in sweep.findings:
        if not finding.survives:
            assert finding.note in report


def test_the_report_prints_the_declared_assumptions(sweep):
    report = render_report(sweep)
    assert "Assumptions" in report
    assert "INSUFFICIENT_FUNDS" in report
    assert "attempt cost" in report.lower()
    assert "simulated" in report.lower()


def test_the_report_prints_the_seed_and_the_configuration_hash(sweep):
    report = render_report(sweep)
    assert str(sweep.config.seed) in report
    assert config_hash(sweep.config) in report


def test_the_report_states_the_baseline_it_compared_against(sweep):
    report = render_report(sweep)
    assert "T+1" in report and "baseline" in report.lower()


def test_the_report_never_claims_a_lift_the_sweep_rejected(sweep):
    report = render_report(sweep)
    for finding in sweep.findings:
        if not finding.survives:
            assert f"{finding.name}: survives" not in report


def test_the_bundle_writes_the_report_and_the_machine_readable_sweep(sweep, tmp_path):
    names = {p.name for p in write_bundle(sweep, tmp_path)}
    assert "report.md" in names and "sweep.json" in names
    assert (tmp_path / "report.md").read_text(encoding="utf-8").startswith("#")


def test_the_bundle_sweep_json_round_trips(sweep, tmp_path):
    write_bundle(sweep, tmp_path)
    payload = json.loads((tmp_path / "sweep.json").read_text(encoding="utf-8"))
    assert len(payload["findings"]) == len(sweep.findings)


def test_the_bundle_exports_the_audit_trails_for_both_arms(sweep, tmp_path):
    write_bundle(sweep, tmp_path)
    assert (tmp_path / "audit_mid_control.csv").exists()
    assert (tmp_path / "audit_mid_treatment.csv").exists()


def test_the_report_says_which_cause_carries_the_lift(sweep):
    report = render_report(sweep)
    assert "Where the lift comes from" in report
    assert "INSTRUMENT_INVALID" in report
    assert "MANDATE_REVOKED" in report


def test_the_report_shows_how_far_up_the_ladder_recoveries_happened(sweep):
    report = render_report(sweep)
    assert "How far up the ladder" in report
    assert "T1 notify" in report


def test_the_report_shows_what_actually_earned_the_money(sweep):
    report = render_report(sweep)
    assert "What actually earned the money" in report
    assert "`retry`" in report


def test_the_report_declares_the_pay_now_conversion_it_assumed(sweep):
    """The link earns money in the mechanism table, so its probability is an
    assumption the reader is owed -- swept across the three bands like every
    other one, not left implicit behind a number it produced."""
    report = render_report(sweep)
    assert "pay-now link conversion" in report
    for band in Band:
        assert f"{BANDS[band].pay_now_conversion * 100:.0f}%" in report
