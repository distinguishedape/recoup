"""The closed vocabularies of the pipeline.

Every one of these is a fixed set defined by the design spec. Nothing in
Recoup may invent a seventh failure class or a fifth tier at runtime; the
LLM's output is validated against these enums precisely so that it cannot.
"""

from enum import Enum, IntEnum


class FailureClass(str, Enum):
    """Root cause of an auto-debit failure (spec R2)."""

    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    INSTRUMENT_INVALID = "INSTRUMENT_INVALID"
    MANDATE_REVOKED = "MANDATE_REVOKED"
    TRANSIENT_ISSUER = "TRANSIENT_ISSUER"
    RISK_DECLINE = "RISK_DECLINE"
    UNCLASSIFIED = "UNCLASSIFIED"


class Tier(IntEnum):
    """Escalation ladder tiers (spec R8). Ordered: a higher tier is more intense."""

    T1_NOTIFY = 1
    T2_REQUEST_ACTION = 2
    T3_FINAL_NOTICE = 3
    T4_TERMINAL = 4


class TerminalState(str, Enum):
    """Where a subject ends up. These four partition the cohort."""

    RECOVERED = "recovered"
    UNRECOVERED = "unrecovered"
    VOLUNTARY_CHURN = "voluntary_churn"
    MANUAL_REVIEW = "manual_review"


class ActionType(str, Enum):
    """Everything the pipeline is capable of doing to a subject."""

    RETRY_CHARGE = "retry_charge"
    REQUEST_INSTRUMENT_UPDATE = "request_instrument_update"
    SEND_MESSAGE = "send_message"
    STOP = "stop"
    ESCALATE_MANUAL_REVIEW = "escalate_manual_review"


class Band(str, Enum):
    """Sensitivity-sweep band for the recovery probability model (spec §7)."""

    LOW = "low"
    MID = "mid"
    HIGH = "high"
