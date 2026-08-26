"""LLM resolution for the two reason strings the table cannot decide.

This is the "LLM proposes, policy disposes" architecture at its narrowest
point. The model is asked one bounded question -- which of six named
classes best explains this decline -- and its answer is validated against
the enum before anything acts on it. An invented class is discarded, not
trusted; an unreachable model degrades to UNCLASSIFIED, which is a valid
class with a defined budget, so the pipeline keeps running.
"""

import json
import re

from recoup.llm.client import LLMClient, LLMUnavailable
from recoup.models.core import Classification, FailureEvent
from recoup.models.enums import FailureClass

RESOLVER_SYSTEM = f"""You classify failed subscription auto-debit attempts for an Indian \
payments recovery system.

Choose exactly one class from this closed list:

- {FailureClass.INSUFFICIENT_FUNDS.value}: the customer's account did not have the money.
- {FailureClass.INSTRUMENT_INVALID.value}: the card or mandate instrument itself is no \
longer usable (expired, blocked, disabled for online use).
- {FailureClass.MANDATE_REVOKED.value}: the customer has withdrawn authorisation.
- {FailureClass.TRANSIENT_ISSUER.value}: a bank or gateway technical failure that is \
likely to clear on its own.
- {FailureClass.RISK_DECLINE.value}: the payment was blocked by a risk or fraud check.
- {FailureClass.UNCLASSIFIED.value}: the evidence does not support any of the above.

Rules:
- Prefer {FailureClass.UNCLASSIFIED.value} over a guess you cannot justify from the evidence.
- Use `source` and `step` as evidence: a bank-sourced decline at authorisation points \
toward funds or issuer problems; a gateway-sourced decline points toward risk or technical \
causes.
- Reply with JSON only, no commentary, in exactly this shape:
{{"failure_class": "<one of the classes above>", "confidence": <number between 0 and 1>, \
"rationale": "<one sentence>"}}
"""

MAX_TOKENS = 2000
"""Generous on purpose.

A budget sized for the visible answer starves any model that reasons before it
writes, because those tokens come out of the same allowance. A real decline was
classified correctly as TRANSIENT_ISSUER and then truncated mid-JSON at 300,
so the guardrail discarded it and fell back -- safe, but a correct answer lost
to an accounting mistake rather than to a wrong model.

The reply itself is one short object, so a large ceiling costs nothing when the
model is concise and rescues the answer when it is not."""


def build_user_prompt(event: FailureEvent) -> str:
    return (
        "Classify this failed auto-debit attempt.\n\n"
        f"reason: {event.error_reason}\n"
        f"source: {event.error_source}\n"
        f"step: {event.error_step}\n"
        f"attempt_number: {event.attempt_number}\n"
    )


def _fallback(detail: str) -> Classification:
    return Classification(
        failure_class=FailureClass.UNCLASSIFIED,
        method="fallback",
        confidence=0.30,
        rationale=f"LLM resolution did not yield a usable class ({detail})",
    )


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def resolve(event: FailureEvent, client: LLMClient) -> Classification:
    try:
        text = client.complete(RESOLVER_SYSTEM, build_user_prompt(event), MAX_TOKENS)
    except LLMUnavailable as exc:
        return _fallback(f"model unavailable: {exc}")

    parsed = _extract_json(text)
    if parsed is None:
        return _fallback("response was not JSON")

    raw_class = str(parsed.get("failure_class", ""))
    try:
        failure_class = FailureClass(raw_class)
    except ValueError:
        return _fallback(f"model proposed unknown class {raw_class!r}")

    try:
        confidence = float(parsed.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = min(1.0, max(0.0, confidence))

    rationale = str(parsed.get("rationale", "")).strip() or "no rationale supplied"
    return Classification(
        failure_class=failure_class,
        method="llm",
        confidence=confidence,
        rationale=rationale,
    )
