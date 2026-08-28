"""Replay one subject's entire story out of the audit log.

    python -m scripts.replay sub_0609 --audit-db artifacts/audit/mid/treatment.db

The audit log has no update path and no delete path, so this is not a summary
of what happened -- it is what happened. Blocked actions are included with the
rule that blocked them, because a compliance claim that only shows the actions
it took is not a compliance claim.
"""

import argparse
import sys
from pathlib import Path

from recoup.audit.log import AuditLog

INTERESTING = ("failure_class", "action_type", "rule", "verdict_rule", "detail", "short_url")


def replay_lines(audit: AuditLog, subscription_id: str) -> list[str]:
    records = audit.reconstruct(subscription_id)
    if not records:
        return [f"no audit records for {subscription_id}"]
    lines = []
    for record in records:
        parts = [
            f"{key}={record.payload[key]}"
            for key in INTERESTING
            if record.payload.get(key) not in (None, "")
        ]
        lines.append(
            f"{record.stage:<28} {record.virtual_time.isoformat()}  " + "  ".join(parts)
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subscription_id")
    parser.add_argument("--audit-db", type=Path, default=Path("artifacts/demo.db"))
    args = parser.parse_args(argv)

    audit = AuditLog(args.audit_db)
    try:
        for line in replay_lines(audit, args.subscription_id):
            print(line)
    finally:
        audit.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
