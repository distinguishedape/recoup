"""The one classification entry point. Table first, model only if needed."""

from recoup.classify.llm_resolver import resolve
from recoup.classify.taxonomy import classify_by_table
from recoup.llm.client import LLMClient
from recoup.models.core import Classification, FailureEvent
from recoup.models.enums import FailureClass


def classify(event: FailureEvent, client: LLMClient | None = None) -> Classification:
    table_result = classify_by_table(event)
    if table_result is not None:
        return table_result
    if client is None:
        return Classification(
            failure_class=FailureClass.UNCLASSIFIED,
            method="fallback",
            confidence=0.30,
            rationale=(
                f"reason string {event.error_reason!r} is ambiguous and no model "
                "client was configured"
            ),
        )
    return resolve(event, client)
