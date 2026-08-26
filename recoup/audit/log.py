"""Append-only audit log: SQLite for querying, JSONL for eyeballing.

There is deliberately no update and no delete. A judge asking "why did
Recoup message this customer at this moment?" is answered by replaying
``reconstruct(subscription_id)``, and that answer is only worth anything
if nothing can rewrite the record after the fact.

Ordering is by ``(virtual_time, seq)``. ``seq`` is a monotonic insertion
counter, so simultaneous events replay in the order they happened rather
than in whatever order SQLite feels like returning them.
"""

import csv
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recoup.models.core import AuditRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id       TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    virtual_time    TEXT NOT NULL,
    real_time       TEXT NOT NULL,
    stage           TEXT NOT NULL,
    payload         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_subject ON audit (subscription_id, virtual_time, seq);
"""

_COLUMNS = ["record_id", "subscription_id", "virtual_time", "real_time", "stage", "payload"]


def new_record(
    subscription_id: str,
    virtual_time: datetime,
    stage: str,
    payload: dict[str, Any],
) -> AuditRecord:
    return AuditRecord(
        record_id=str(uuid.uuid4()),
        subscription_id=subscription_id,
        virtual_time=virtual_time,
        real_time=datetime.now(timezone.utc),
        stage=stage,
        payload=payload,
    )


class AuditLog:
    def __init__(self, db_path: Path, jsonl_path: Path | None = None) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        # Write-ahead logging with a relaxed sync keeps the append-per-decision
        # guarantee while removing an fsync from every single record. A cohort
        # run writes thousands of rows and the default settings made that the
        # dominant cost of the whole experiment. Records still survive a process
        # crash; only an OS-level crash can lose the most recent ones, which is
        # the right trade for an experiment log.
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._jsonl_path = Path(jsonl_path) if jsonl_path else None
        if self._jsonl_path:
            self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: AuditRecord) -> None:
        self._conn.execute(
            "INSERT INTO audit (record_id, subscription_id, virtual_time, real_time, stage, payload)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.record_id,
                record.subscription_id,
                record.virtual_time.isoformat(),
                record.real_time.isoformat(),
                record.stage,
                json.dumps(record.payload, default=str, sort_keys=True),
            ),
        )
        self._conn.commit()
        if self._jsonl_path:
            with self._jsonl_path.open("a", encoding="utf-8") as handle:
                handle.write(record.model_dump_json() + "\n")

    def _query(self, where: str = "", params: tuple[Any, ...] = ()) -> list[AuditRecord]:
        sql = f"SELECT {', '.join(_COLUMNS)} FROM audit {where} ORDER BY virtual_time, seq"
        rows = self._conn.execute(sql, params).fetchall()
        return [
            AuditRecord(
                record_id=row[0],
                subscription_id=row[1],
                virtual_time=datetime.fromisoformat(row[2]),
                real_time=datetime.fromisoformat(row[3]),
                stage=row[4],
                payload=json.loads(row[5]),
            )
            for row in rows
        ]

    def all(self) -> list[AuditRecord]:
        return self._query()

    def reconstruct(self, subscription_id: str) -> list[AuditRecord]:
        return self._query("WHERE subscription_id = ?", (subscription_id,))

    def export_csv(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(_COLUMNS)
            for record in self.all():
                writer.writerow(
                    [
                        record.record_id,
                        record.subscription_id,
                        record.virtual_time.isoformat(),
                        record.real_time.isoformat(),
                        record.stage,
                        json.dumps(record.payload, default=str, sort_keys=True),
                    ]
                )

    def export_json(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [json.loads(record.model_dump_json()) for record in self.all()]
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def close(self) -> None:
        self._conn.close()
