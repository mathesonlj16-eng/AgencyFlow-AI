"""Campaign discovery fields that preserve unknowns and distinguish proposals from confirmed facts.

Standalone from the existing AI prompt, FastAPI routes, and n8n handoff so it can be
integrated after concurrent Notion work without changing production behaviour.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DiscoveryStatus(str, Enum):
    UNKNOWN = "unknown"
    PROPOSED = "proposed"
    CLIENT_CONFIRMED = "client_confirmed"


class DiscoveryField(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: DiscoveryStatus = DiscoveryStatus.UNKNOWN
    value: str | None = Field(default=None, max_length=500)
    provenance: str | None = Field(default=None, max_length=350)

    @model_validator(mode="after")
    def validate_state(self) -> "DiscoveryField":
        if self.status == DiscoveryStatus.UNKNOWN:
            if self.value is not None or self.provenance is not None:
                raise ValueError("unknown fields cannot contain purported facts or provenance")
        elif not self.value:
            raise ValueError("proposed and confirmed fields require a nonblank value")
        elif self.status == DiscoveryStatus.CLIENT_CONFIRMED and not self.provenance:
            raise ValueError("client-confirmed fields require a human-entered source note")
        return self


# Questions are recommendations for the coordinator, not messages sent to clients.
DISCOVERY_QUESTIONS = {
    "business_goal": "What business outcome is this campaign intended to achieve?",
    "target_audience": "Who are the intended customers and which location or audience matters most?",
    "primary_kpi": "Which metric should indicate campaign success?",
    "measurement_source": "Which analytics, booking or sales source will be used to measure that metric?",
    "baseline": "What is the verified baseline (including units and reporting period)?",
    "target": "What measurable target is agreed with the client?",
    "budget": "What is the approved creative and distribution budget?",
    "platforms": "Which publishing platforms are confirmed?",
}


class CampaignContext(BaseModel):
    """Optional structured discovery, independent from approved production tasks.

    This model makes no claims based on the brief by itself. Only a separate
    explicit human confirmation action may set ``client_confirmed``.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    business_goal: DiscoveryField = Field(default_factory=DiscoveryField)
    target_audience: DiscoveryField = Field(default_factory=DiscoveryField)
    primary_kpi: DiscoveryField = Field(default_factory=DiscoveryField)
    measurement_source: DiscoveryField = Field(default_factory=DiscoveryField)
    baseline: DiscoveryField = Field(default_factory=DiscoveryField)
    target: DiscoveryField = Field(default_factory=DiscoveryField)
    budget: DiscoveryField = Field(default_factory=DiscoveryField)
    platforms: DiscoveryField = Field(default_factory=DiscoveryField)

    def unresolved_questions(self) -> list[str]:
        """Questions still needing confirmation; no outbound communication."""
        return [question for field, question in DISCOVERY_QUESTIONS.items()
                if getattr(self, field).status != DiscoveryStatus.CLIENT_CONFIRMED]

    def propose(self, field_name: str, value: str, *, rationale: str | None = None) -> "CampaignContext":
        """Mark a suggestion as proposed, never as a verified client fact.

        A later model pass must not overwrite anything a person has confirmed.
        """
        self._check_field_name(field_name)
        if getattr(self, field_name).status == DiscoveryStatus.CLIENT_CONFIRMED:
            return self.model_copy(deep=True)
        updated = self.model_copy(deep=True)
        setattr(updated, field_name, DiscoveryField(
            status=DiscoveryStatus.PROPOSED, value=value, provenance=rationale))
        return updated

    def confirm(self, field_name: str, value: str, *, source_note: str) -> "CampaignContext":
        """Human review operation; requires a source note to avoid silent promotion."""
        self._check_field_name(field_name)
        updated = self.model_copy(deep=True)
        setattr(updated, field_name, DiscoveryField(
            status=DiscoveryStatus.CLIENT_CONFIRMED, value=value, provenance=source_note))
        return updated

    @staticmethod
    def _check_field_name(field_name: str) -> None:
        if field_name not in DISCOVERY_QUESTIONS:
            raise ValueError("unsupported campaign discovery field")
