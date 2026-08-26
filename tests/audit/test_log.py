import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from recoup.audit.log import AuditLog, new_record

T0 = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def log(tmp_path):
    audit = AuditLog(tmp_path / "audit.db", tmp_path / "audit.jsonl")
    yield audit
    audit.close()


def test_new_record_stamps_identity_and_real_time():
    record = new_record("sub_1", T0, "classify", {"failure_class": "INSUFFICIENT_FUNDS"})
    assert record.subscription_id == "sub_1"
    assert record.stage == "classify"
    assert record.record_id
    assert record.real_time.tzinfo is not None


def test_appended_records_come_back(log):
    log.append(new_record("sub_1", T0, "ingest", {"error_reason": "insufficient_funds"}))
    assert len(log.all()) == 1
    assert log.all()[0].payload["error_reason"] == "insufficient_funds"


def test_reconstruct_returns_one_subjects_story_in_virtual_time_order(log):
    log.append(new_record("sub_2", T0, "ingest", {}))
    log.append(new_record("sub_1", T0 + timedelta(days=1), "execute", {"n": 2}))
    log.append(new_record("sub_1", T0, "ingest", {"n": 1}))
    story = log.reconstruct("sub_1")
    assert [r.payload["n"] for r in story] == [1, 2]


def test_records_at_the_same_virtual_time_reconstruct_in_append_order(log):
    for n in range(4):
        log.append(new_record("sub_1", T0, "execute", {"n": n}))
    assert [r.payload["n"] for r in log.reconstruct("sub_1")] == [0, 1, 2, 3]


def test_the_log_survives_being_reopened(tmp_path):
    first = AuditLog(tmp_path / "audit.db")
    first.append(new_record("sub_1", T0, "ingest", {"n": 1}))
    first.close()
    second = AuditLog(tmp_path / "audit.db")
    assert len(second.all()) == 1
    second.close()


def test_the_jsonl_mirror_is_written_line_per_record(log, tmp_path):
    log.append(new_record("sub_1", T0, "ingest", {"n": 1}))
    log.append(new_record("sub_1", T0, "classify", {"n": 2}))
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["stage"] == "ingest"


def test_the_table_has_no_update_or_delete_path(log, tmp_path):
    log.append(new_record("sub_1", T0, "ingest", {}))
    source = (
        __import__("pathlib").Path("recoup/audit/log.py").read_text(encoding="utf-8").upper()
    )
    assert "UPDATE AUDIT" not in source
    assert "DELETE FROM" not in source


def test_export_csv_writes_one_row_per_record(log, tmp_path):
    log.append(new_record("sub_1", T0, "ingest", {"n": 1}))
    log.append(new_record("sub_1", T0, "classify", {"n": 2}))
    out = tmp_path / "audit.csv"
    log.export_csv(out)
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 2
    assert rows[0]["stage"] == "ingest"


def test_export_json_round_trips(log, tmp_path):
    log.append(new_record("sub_1", T0, "ingest", {"n": 1}))
    out = tmp_path / "audit.json"
    log.export_json(out)
    assert json.loads(out.read_text(encoding="utf-8"))[0]["payload"]["n"] == 1


def test_sqlite_actually_holds_the_rows(log, tmp_path):
    log.append(new_record("sub_1", T0, "ingest", {}))
    conn = sqlite3.connect(tmp_path / "audit.db")
    assert conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == 1
    conn.close()
