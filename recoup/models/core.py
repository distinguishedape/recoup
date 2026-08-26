"""Domain models shared by every pipeline stage.

Everything here is frozen. A stage receives data, derives new data, and
passes it on; no stage may reach back and edit what an earlier stage
decided. That property is what makes the audit log trustworthy.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from recoup.models.enums import ActionType, FailureClass, Tier


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Subscription(_Frozen):
    subscription_id: str
    customer_id: str
    plan_amount_paise: int = Field(ge=0)


class FailureEvent(_Frozen):
    """A single failed auto-debit attempt.

    Both ingestion paths -- the real Razorpay webhook and the synthetic
    cohort generator -- emit this identical shape. Nothing downstream is
    permitted to branch on ``source``; it exists for the audit trail only.
    """

    event_id: str
    subscription_id: str
    invoice_id: str
    error_reason: str
    error_source: str
    error_step: str
    attempt_number: int = Field(ge=1)
    occurred_at: datetime
    source: Literal["webhook", "cohort"]


class Classification(_Frozen):
    failure_class: FailureClass
    method: Literal["table", "llm", "fallback"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class Action(_Frozen):
    action_id: str
    subscription_id: str
    type: ActionType
    scheduled_at: datetime
    tier: Tier
    channel: str | None
    template_id: str | None
    free_text: str | None
    suppressed_free_text: str | None = None
    """Copy the model wrote and was not permitted to send.

    Audit only. The policy engine never reads it and the executor has no path
    to it, so it cannot reach a customer. It exists so a reviewer can see what
    the model wanted to say -- which is more useful than discarding the plan
    and more honest than pretending it never proposed anything."""
    reason: str


class InterventionPlan(_Frozen):
    subscription_id: str
    failure_class: FailureClass
    actions: list[Action]


class PolicyVerdict(_Frozen):
    allowed: bool
    rule: str
    detail: str


class ExecutionResult(_Frozen):
    action_id: str
    subscription_id: str
    succeeded: bool
    detail: str
    cost_paise: int = Field(ge=0)
    occurred_at: datetime


class AuditRecord(_Frozen):
    record_id: str
    subscription_id: str
    virtual_time: datetime
    real_time: datetime
    stage: str
    payload: dict[str, Any]
