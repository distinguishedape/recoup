"""``AuthorizedAction`` -- permission expressed as a type the executor demands.

The executor accepts this type and nothing else, and the only code that
can produce one is ``recoup.policy.engine.authorize``. Every ordinary way
of building a Python object is closed:

* the constructor always raises, so ``AuthorizedAction(action=..., verdict=...)``
  fails;
* because the constructor raises, ``dataclasses.replace`` fails too -- it
  rebuilds through ``__init__``. That route mattered most: replacing one
  field of an immutable object is idiomatic, and an engineer bumping
  ``scheduled_at`` for a backoff would otherwise have forged an
  authorisation *by accident*;
* there is no public "mint anything" helper. An earlier version exported
  one, which meant any caller who wrote their own ``PolicyVerdict(allowed=True)``
  got a valid authorisation without a single rule running.

**The standard this meets, stated honestly.** Python has no real privacy,
and this is not proof against a determined caller: ``object.__new__`` plus
``object.__setattr__``, a subclass overriding a hook, ``pickle``, or
reaching into this module for ``_construct`` will all still work. What the
design guarantees is narrower and still worth having -- accidental bypass
is impossible, and deliberate bypass cannot be written without a line that
is unmistakable in review. "We remembered to call the policy engine" is a
promise about discipline; this is a promise about the type system, up to
the limits of the language.
"""

from dataclasses import dataclass
from typing import Any

from recoup.models.core import Action, PolicyVerdict


@dataclass(frozen=True, init=False)
class AuthorizedAction:
    action: Action
    verdict: PolicyVerdict

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise PermissionError(
            "AuthorizedAction cannot be constructed directly or copied with "
            "dataclasses.replace; it is produced only by recoup.policy.engine.authorize"
        )


def _construct(action: Action, verdict: PolicyVerdict) -> AuthorizedAction:
    """Build an authorisation. Private to ``recoup.policy``; only ``engine`` calls it.

    Deliberately not exported and deliberately awkward to reach. It bypasses
    the raising constructor the way the language allows, which is exactly the
    line a reviewer should notice if it ever appears outside this package.
    """
    if not verdict.allowed:
        raise ValueError("cannot authorize an action on a denying verdict")
    authorized = object.__new__(AuthorizedAction)
    object.__setattr__(authorized, "action", action)
    object.__setattr__(authorized, "verdict", verdict)
    return authorized
