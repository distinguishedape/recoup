"""A virtual clock that compresses a multi-day dunning run into seconds.

The clock is an event queue, not a timer. Nothing sleeps. Time advances
only when an event is popped, and it advances exactly to that event.

Two properties matter for reproducibility:

* Events at the identical timestamp come out in the order they went in.
  Without the sequence counter, ``heapq`` would fall through to comparing
  payloads -- which is both non-deterministic across runs and a TypeError
  for dicts.
* Scheduling into the past is clamped to ``now`` rather than rejected, so
  a planner that computes a retry delay of zero cannot rewind history.
"""

import heapq
from datetime import datetime
from typing import Any


class VirtualClock:
    def __init__(self, start: datetime) -> None:
        self._now = start
        self._heap: list[tuple[datetime, int, Any]] = []
        self._seq = 0

    @property
    def now(self) -> datetime:
        return self._now

    def schedule(self, at: datetime, payload: Any) -> int:
        when = max(at, self._now)
        seq = self._seq
        self._seq += 1
        heapq.heappush(self._heap, (when, seq, payload))
        return seq

    def pop(self) -> tuple[datetime, Any] | None:
        if not self._heap:
            return None
        when, _seq, payload = heapq.heappop(self._heap)
        self._now = when
        return when, payload

    def __len__(self) -> int:
        return len(self._heap)
