"""Versioned structured contracts for supervisor/subagent handoffs.

These models are the trust boundary for values returned from model-driven
subagents; callers must not reconstruct missing identifiers or metrics from
summary prose.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

HANDOFF_SCHEMA_VERSION = "1.0"


class HandoffContract(BaseModel):
    """Strict base model for every nested handoff value."""

    model_config = ConfigDict(extra="forbid", strict=True)


class HandoffError(HandoffContract):
    """Safe domain error returned to the supervisor."""

    code: str = Field(min_length=1, max_length=96, pattern=r"^[a-z0-9_]+$")
    message: str = Field(min_length=1, max_length=1000)
    stage: str | None = Field(default=None, max_length=96)
    retryable: bool = False


class HandoffWarning(HandoffContract):
    """Typed non-blocking limitation or warning."""

    code: str = Field(min_length=1, max_length=96, pattern=r"^[a-z0-9_]+$")
    message: str = Field(min_length=1, max_length=1000)
    stage: str | None = Field(default=None, max_length=96)


class HandoffMetric(HandoffContract):
    """One named numeric result; prose cannot supply metric values."""

    name: str = Field(min_length=1, max_length=96, pattern=r"^[a-z0-9_]+$")
    value: int | float
    unit: str | None = Field(default=None, max_length=48)


class OutputReference(HandoffContract):
    """Typed reference to an identity returned by a trusted tool boundary."""

    kind: Literal[
        "dataset_staging",
        "dataset",
        "dataset_revision",
        "factor_snapshot",
        "experiment",
        "artifact",
        "interpretation",
        "publication",
        "source",
    ]
    identifier: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    label: str | None = Field(default=None, max_length=160)


class EvidenceGap(HandoffContract):
    """A typed gap that limits how strongly evidence may be used."""

    code: str = Field(min_length=1, max_length=96, pattern=r"^[a-z0-9_]+$")
    description: str = Field(min_length=1, max_length=1000)
    impact: Literal["low", "medium", "high", "blocking"]
    resolvable: bool


class ResearchSource(HandoffContract):
    """Safe source evidence included by the read-only researcher."""

    source_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=8, max_length=2048, pattern=r"^https?://")
    claim: str = Field(min_length=1, max_length=1200)


class ResearchClaim(HandoffContract):
    """A claim linked only to declared source identifiers."""

    statement: str = Field(min_length=1, max_length=1200)
    source_ids: list[str] = Field(default_factory=list, max_length=20)


class QuantDataOutputs(HandoffContract):
    """Quant-data worker identities and validation outputs in schema 1.0."""

    dataset_staging_id: str | None = Field(default=None, min_length=1, max_length=256)
    staging_revision_id: str | None = Field(default=None, min_length=1, max_length=256)
    dataset_id: str | None = Field(default=None, min_length=1, max_length=256)
    dataset_revision_id: str | None = Field(default=None, min_length=1, max_length=256)
    dataset_alias: str | None = Field(default=None, min_length=1, max_length=256)
    factor_snapshot_id: str | None = Field(default=None, min_length=1, max_length=256)
    factor_snapshot_revision_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
    )
    request_fingerprint: str | None = Field(default=None, min_length=1, max_length=128)
    content_hash: str | None = Field(default=None, min_length=1, max_length=128)
    point_in_time_status: Literal["unsupported"] | None = None
    manifest_hash: str | None = Field(default=None, min_length=1, max_length=128)
    manifest_verified: bool | None = None
    universe: str | None = Field(default=None, min_length=1, max_length=128)
    provider_uri: str | None = Field(default=None, min_length=1, max_length=1024)
    rows: int | None = Field(default=None, ge=0)
    valid: bool | None = None


class QuantExperimentOutputs(HandoffContract):
    """Current quant experiment identities and state."""

    dataset_id: str | None = Field(default=None, min_length=1, max_length=256)
    dataset_revision_id: str | None = Field(default=None, min_length=1, max_length=256)
    universe: str | None = Field(default=None, min_length=1, max_length=128)
    experiment_id: str | None = Field(default=None, min_length=1, max_length=256)
    status: Literal["queued", "running", "completed", "failed"] | None = None
    artifact_uri: str | None = Field(default=None, min_length=1, max_length=1024)
    interpretation_report_id: str | None = Field(
        default=None, min_length=1, max_length=128
    )


class AiTraderOutputs(HandoffContract):
    """AI-Trader read or publication result values."""

    signal_id: int | None = Field(default=None, ge=1)
    available: bool | None = None
    total: int | None = Field(default=None, ge=0)
    matched_symbols: list[str] = Field(default_factory=list, max_length=100)
    unmatched_symbols: list[str] = Field(default_factory=list, max_length=100)


class BaseHandoff(HandoffContract):
    """Fields shared by all final subagent results."""

    schema_version: Literal["1.0"]
    ok: bool
    task: str = Field(min_length=1, max_length=96, pattern=r"^[a-z0-9_]+$")
    summary: str = Field(min_length=1, max_length=2000)
    references: list[OutputReference] = Field(default_factory=list, max_length=100)
    metrics: list[HandoffMetric] = Field(default_factory=list, max_length=100)
    warnings: list[HandoffWarning] = Field(default_factory=list, max_length=100)
    errors: list[HandoffError] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        """Keep success and failure envelopes internally consistent."""
        if self.ok and self.errors:
            raise ValueError("Successful handoffs cannot contain errors.")
        if not self.ok and not self.errors:
            raise ValueError("Failed handoffs must contain at least one error.")
        return self


class ResearchHandoff(BaseHandoff):
    """Evidence-oriented result from the researcher subagent."""

    agent: Literal["researcher"]
    claims: list[ResearchClaim] = Field(default_factory=list, max_length=100)
    sources: list[ResearchSource] = Field(default_factory=list, max_length=100)
    evidence_gaps: list[EvidenceGap] = Field(default_factory=list, max_length=100)
    next_action: Literal[
        "final_answer",
        "delegate_to_quant_data",
        "delegate_to_quant_researcher",
        "ask_user",
    ]


class QuantDataHandoff(BaseHandoff):
    """Validated result from the quant-data subagent."""

    agent: Literal["quant-data-agent"]
    task: Literal[
        "prepare_qlib_dataset",
        "fetch_factor_snapshot",
    ]
    outputs: QuantDataOutputs
    next_action: Literal[
        "delegate_to_quant_researcher",
        "final_answer",
        "retry_with_corrected_inputs",
        "ask_user",
    ]


class QuantExperimentHandoff(BaseHandoff):
    """Validated result from the quant-researcher subagent."""

    agent: Literal["quant-researcher"]
    task: Literal[
        "list_quant_datasets",
        "run_governed_qlib_experiment",
        "get_qlib_experiment_result",
        "record_experiment_interpretation",
    ]
    outputs: QuantExperimentOutputs
    next_action: Literal[
        "final_answer",
        "retry_with_corrected_inputs",
        "ask_user",
    ]


class AiTraderHandoff(BaseHandoff):
    """Validated result from the AI-Trader subagent."""

    agent: Literal["ai-trader-agent"]
    task: Literal[
        "market_overview",
        "market_news",
        "signal_feed",
        "publish_strategy",
        "publish_discussion",
        "heartbeat",
    ]
    outputs: AiTraderOutputs
    next_action: Literal[
        "final_answer",
        "retry_with_corrected_inputs",
        "ask_user",
    ]


HANDOFF_SCHEMAS = {
    "ResearchHandoff": ResearchHandoff,
    "QuantDataHandoff": QuantDataHandoff,
    "QuantExperimentHandoff": QuantExperimentHandoff,
    "AiTraderHandoff": AiTraderHandoff,
}
