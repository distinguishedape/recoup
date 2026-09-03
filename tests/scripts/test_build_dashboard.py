"""The dashboard may not overstate what a test run proved.

Every status on the published page comes from here, so these are the tests that
stop the page claiming a green suite off a partial run -- the one failure mode
that would make the whole thing dishonest rather than merely wrong.
"""

from xml.etree import ElementTree

import pytest

from scripts.build_dashboard import (
    ablation_from_report,
    bands_from_sweep,
    build_suite,
    results_from_junit,
    status_of,
)

INVENTORY = [
    ("classify", "test_engine.py", "test_a_reason_resolves"),
    ("classify", "test_engine.py", "test_an_ambiguous_reason_escalates"),
    ("policy", "test_rules.py", "test_a_revoked_mandate_is_never_charged"),
]

META = {"seconds": 1.5, "at": "03 Sep 2026, 21:40 IST", "failed": 0, "errors": 0}


def suite_for(seen):
    return build_suite(INVENTORY, seen, META, "pytest -q", "Command")


def test_a_test_the_run_never_touched_is_notrun_rather_than_passed():
    ran = {("test_engine.py", "test_a_reason_resolves"): {"status": "passed", "seconds": 0.01}}

    suite = suite_for(ran)

    assert suite["total"] == 3
    assert suite["run"]["count"] == 1
    assert suite["run"]["passed"] == 1
    statuses = {
        t["name"]: t["status"]
        for p in suite["packages"]
        for f in p["files"]
        for t in f["tests"]
    }
    # The two tests outside the run must not be able to read as green anywhere.
    assert statuses["test_an_ambiguous_reason_escalates"] == "notrun"
    assert statuses["test_a_revoked_mandate_is_never_charged"] == "notrun"


def test_the_run_count_never_exceeds_what_the_suite_defines():
    every = {(f, n): {"status": "passed", "seconds": 0.01} for _, f, n in INVENTORY}

    suite = suite_for(every)

    assert suite["run"]["count"] == suite["total"] == 3
    assert suite["run"]["passed"] == 3


def test_a_failure_is_counted_as_failed_and_not_folded_into_passed():
    seen = {
        ("test_engine.py", "test_a_reason_resolves"): {"status": "failed", "seconds": 0.02},
        ("test_engine.py", "test_an_ambiguous_reason_escalates"): {
            "status": "passed",
            "seconds": 0.01,
        },
    }

    suite = suite_for(seen)

    assert suite["run"]["failed"] == 1
    assert suite["run"]["passed"] == 1
    assert suite["run"]["count"] == 2


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        ('<testcase name="t" time="0.1"/>', "passed"),
        ('<testcase name="t" time="0.1"><failure message="no"/></testcase>', "failed"),
        ('<testcase name="t" time="0.1"><error message="boom"/></testcase>', "error"),
        # pytest writes an xfail as a skip; conflating them would hide a real skip
        ('<testcase name="t"><skipped type="pytest.xfail"/></testcase>', "xfailed"),
        ('<testcase name="t"><skipped type="pytest.skip"/></testcase>', "skipped"),
    ],
)
def test_junit_states_map_to_distinct_statuses(xml, expected):
    assert status_of(ElementTree.fromstring(xml)) == expected


def test_reading_a_junit_file_keeps_each_test_s_own_duration(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(
        '<testsuites><testsuite failures="1" errors="0" time="2.5" '
        'timestamp="2026-09-03T21:40:00+05:30">'
        '<testcase classname="tests.classify.test_engine" name="fast" time="0.01"/>'
        '<testcase classname="tests.policy.test_rules" name="slow" time="1.25">'
        '<failure message="nope"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )

    seen, meta = results_from_junit(path)

    assert seen[("test_engine.py", "fast")] == {"status": "passed", "seconds": 0.01}
    assert seen[("test_rules.py", "slow")]["status"] == "failed"
    assert seen[("test_rules.py", "slow")]["seconds"] == 1.25
    assert meta["failed"] == 1
    assert meta["seconds"] == 2.5


def test_an_ablation_table_that_stopped_parsing_fails_loudly():
    good = ablation_from_report("| 3 | +27.56% | +25.17% |\n| 11 | +30.66% | +26.73% |\n")
    assert good == [
        {"seed": 3, "shipped": 27.56, "matched": 25.17},
        {"seed": 11, "shipped": 30.66, "matched": 26.73},
    ]

    # A silently empty chart is worse than a build that stops.
    with pytest.raises(SystemExit):
        ablation_from_report("the table was reformatted and no longer matches")


def test_the_page_reads_rates_as_percentages_not_fractions():
    sweep = {
        "results": {
            band: {
                "control": {
                    "recovered": 1,
                    "recovery_rate": 0.4599,
                    "gross_recovered_paise": 2,
                    "total_cost_paise": 3,
                    "net_recovered_paise": 4,
                    "charge_attempts": 5,
                    "wasted_attempts": 6,
                    "attempts_per_recovery": 7.0,
                },
                "treatment": {
                    "recovered": 8,
                    "recovery_rate": 0.5872,
                    "gross_recovered_paise": 9,
                    "total_cost_paise": 10,
                    "net_recovered_paise": 11,
                    "charge_attempts": 12,
                    "wasted_attempts": 13,
                    "attempts_per_recovery": 2.4,
                    "money_by_mechanism": {"retry": 9},
                },
            }
            for band in ("low", "mid", "high")
        }
    }

    bands = bands_from_sweep(sweep)

    assert bands["mid"]["control"]["rate"] == pytest.approx(45.99)
    assert bands["mid"]["treatment"]["rate"] == pytest.approx(58.72)
    assert bands["mid"]["treatment"]["mech"] == {"retry": 9}
