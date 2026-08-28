"""Every rail, real or fake, must actually implement `PaymentRail`.

`PaymentRail` is a `typing.Protocol`: structural, checked by nobody at runtime.
Adding `create_pay_now_link`/`deliver_pay_now_link` to it in Task 4 changed
nothing anywhere until a scenario finally called them, at which point two
hand-written test doubles raised `AttributeError` and a suite of 590 tests had
had nothing to say about it. The test that was supposed to catch this was named
`test_the_rail_still_satisfies_the_payment_rail_protocol` and asserted `hasattr`
on two of the four methods.

So the method set is derived from the Protocol itself rather than typed out
here: adding a method to `PaymentRail` tightens this test automatically, which
is the only version of it that cannot rot. Test doubles are included on
purpose — a double that has drifted from the interface is exactly the thing
that makes a green suite lie.
"""

import inspect

import pytest

from recoup.execute.rail import PaymentRail, SimulatedRail
from recoup.execute.razorpay_rail import RazorpayTestRail
from tests.execute.test_executor import ConfigurablePayNowRail
from tests.live.test_agent import FakeRail, RefusingRail
from tests.scripts.test_demo import _StubRail

IMPLEMENTATIONS = [
    SimulatedRail,
    RazorpayTestRail,
    FakeRail,
    RefusingRail,
    ConfigurablePayNowRail,
    _StubRail,
]


def protocol_methods() -> dict[str, inspect.Signature]:
    """The Protocol's own method set, read off the Protocol."""
    return {
        name: inspect.signature(getattr(PaymentRail, name))
        for name in sorted(getattr(PaymentRail, "__protocol_attrs__", ()))
        if callable(getattr(PaymentRail, name, None))
    }


def test_the_protocol_actually_declares_the_methods_this_test_checks():
    """Guards the guard: if `__protocol_attrs__` ever comes back empty, every
    assertion below would pass vacuously and this file would be decoration."""
    names = set(protocol_methods())
    assert names == {
        "charge",
        "deliver_update_request",
        "create_pay_now_link",
        "deliver_pay_now_link",
    }


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS, ids=lambda c: c.__name__)
def test_every_rail_implements_every_protocol_method(implementation):
    missing = [name for name in protocol_methods() if not callable(getattr(implementation, name, None))]
    assert not missing, (
        f"{implementation.__name__} is used as a PaymentRail but does not implement "
        f"{', '.join(missing)}. A double that has drifted from the interface makes a "
        "green suite lie about production."
    )


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS, ids=lambda c: c.__name__)
def test_every_rail_takes_the_arguments_the_protocol_promises(implementation):
    """Presence is not conformance. A method that takes different arguments
    fails at the call site just as loudly as one that is missing."""
    for name, expected in protocol_methods().items():
        actual = inspect.signature(getattr(implementation, name))
        expected_params = [p for p in expected.parameters if p != "self"]
        actual_params = [p for p in actual.parameters if p != "self"]
        assert actual_params == expected_params, (
            f"{implementation.__name__}.{name}{actual} does not match "
            f"PaymentRail.{name}{expected}"
        )
