"""One customer's whole story, including what was refused and by which rule.

The refusals are the point. A trace that shows only what happened reads like a
log; a trace that shows what was *not* done and names the rule that stopped it
is the compliance argument, made by the system about itself.
"""

from datetime import datetime, timezone

from recoup.audit.log import AuditLog, new_record
from scripts.replay import replay_lines

NOW = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)


def build_log(tmp_path) -> AuditLog:
    audit = AuditLog(tmp_path / "replay.db")
    audit.append(new_record("sub_1", NOW, "classify", {
        "failure_class": "INSTRUMENT_INVALID", "method": "llm", "confidence": 0.9,
        "rationale": "the reason string does not disclose a cause",
    }))
    audit.append(new_record("sub_1", NOW, "execute", {
        "action_type": "request_instrument_update", "succeeded": True,
        "verdict_rule": "permitted", "detail": "customer updated their payment instrument",
    }))
    audit.append(new_record("sub_1", NOW, "policy_block", {
        "action_type": "send_message", "rule": "contact_window",
        "detail": "23:14 IST is outside 08:00-19:00",
    }))
    return audit


def test_the_replay_is_in_recorded_order(tmp_path):
    """Append-only means recorded order is the story's order."""
    audit = build_log(tmp_path)
    try:
        stages = [line.split()[0] for line in replay_lines(audit, "sub_1")]
    finally:
        audit.close()
    assert stages == ["classify", "execute", "policy_block"]


def test_every_block_names_the_rule_that_caused_it(tmp_path):
    audit = build_log(tmp_path)
    try:
        blocked = [line for line in replay_lines(audit, "sub_1") if line.startswith("policy_block")]
    finally:
        audit.close()
    assert blocked and "contact_window" in blocked[0]


def test_a_subject_with_no_records_says_so_rather_than_printing_nothing(tmp_path):
    audit = AuditLog(tmp_path / "empty.db")
    try:
        lines = replay_lines(audit, "sub_missing")
    finally:
        audit.close()
    assert lines == ["no audit records for sub_missing"]
