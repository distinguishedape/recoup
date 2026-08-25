from datetime import datetime, timedelta, timezone

from recoup.clock.virtual import VirtualClock

START = datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)


def test_clock_starts_at_the_given_time():
    clock = VirtualClock(START)
    assert clock.now == START


def test_pop_returns_events_in_time_order_regardless_of_insertion_order():
    clock = VirtualClock(START)
    clock.schedule(START + timedelta(days=2), "later")
    clock.schedule(START + timedelta(days=1), "sooner")
    assert clock.pop()[1] == "sooner"
    assert clock.pop()[1] == "later"


def test_pop_advances_now_to_the_event_time():
    clock = VirtualClock(START)
    clock.schedule(START + timedelta(days=3), "x")
    clock.pop()
    assert clock.now == START + timedelta(days=3)


def test_ties_break_by_insertion_order_not_by_heap_accident():
    clock = VirtualClock(START)
    at = START + timedelta(hours=1)
    for label in ["a", "b", "c", "d"]:
        clock.schedule(at, label)
    assert [clock.pop()[1] for _ in range(4)] == ["a", "b", "c", "d"]


def test_payloads_that_cannot_be_compared_do_not_break_the_heap():
    clock = VirtualClock(START)
    at = START + timedelta(hours=1)
    clock.schedule(at, {"unorderable": 1})
    clock.schedule(at, {"unorderable": 2})
    assert clock.pop()[1] == {"unorderable": 1}
    assert clock.pop()[1] == {"unorderable": 2}


def test_pop_on_an_empty_clock_returns_none_and_leaves_now_alone():
    clock = VirtualClock(START)
    assert clock.pop() is None
    assert clock.now == START


def test_scheduling_in_the_past_is_clamped_to_now():
    clock = VirtualClock(START)
    clock.schedule(START - timedelta(days=1), "stale")
    when, payload = clock.pop()
    assert payload == "stale"
    assert when == START


def test_len_reports_pending_events():
    clock = VirtualClock(START)
    clock.schedule(START + timedelta(days=1), "x")
    clock.schedule(START + timedelta(days=2), "y")
    assert len(clock) == 2
    clock.pop()
    assert len(clock) == 1
