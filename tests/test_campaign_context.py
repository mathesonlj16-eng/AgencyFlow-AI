"""Standalone tests for proposed versus verified creative campaign context."""
import pytest
from pydantic import ValidationError
from campaign_context import CampaignContext, DiscoveryField, DiscoveryStatus


def test_unknown_fields_are_default_and_no_facts_are_inferred():
    ctx = CampaignContext()
    assert ctx.business_goal.status == DiscoveryStatus.UNKNOWN
    assert ctx.target_audience.value is None
    assert ctx.primary_kpi.value is None
    assert ctx.baseline.value is None
    assert len(ctx.unresolved_questions()) == 8


def test_unknown_field_cannot_smuggle_a_value():
    with pytest.raises(ValidationError):
        DiscoveryField(status="unknown", value="Increasing reservations")
    with pytest.raises(ValidationError):
        DiscoveryField(status="unknown", provenance="inferred")


def test_proposal_never_auto_confirms_and_does_not_mutate_original():
    original = CampaignContext()
    suggestion = original.propose("business_goal", "Increase reservations", rationale="AI suggestion")
    assert suggestion.business_goal.status == DiscoveryStatus.PROPOSED
    assert suggestion.business_goal.provenance == "AI suggestion"
    assert original.business_goal.status == DiscoveryStatus.UNKNOWN
    assert len(suggestion.unresolved_questions()) == 8


def test_confirmation_requires_human_source_note():
    ctx = CampaignContext()
    with pytest.raises(ValidationError):
        ctx.confirm("primary_kpi", "Bookings", source_note="")
    updated = ctx.confirm("primary_kpi", "Booking count", source_note="Client approved by coordinator")
    assert updated.primary_kpi.status == DiscoveryStatus.CLIENT_CONFIRMED
    assert len(updated.unresolved_questions()) == 7
    assert len(ctx.unresolved_questions()) == 8


def test_regeneration_keeps_client_confirmed_values():
    ctx = CampaignContext().confirm("target_audience", "Local weekday diners", source_note="Verified call notes")
    rerun = ctx.propose("target_audience", "Tourists", rationale="model guess")
    assert rerun.target_audience.value == "Local weekday diners"
    assert rerun.target_audience.status == DiscoveryStatus.CLIENT_CONFIRMED


def test_unknown_field_and_extra_fields_are_rejected():
    ctx = CampaignContext()
    with pytest.raises(ValueError):
        ctx.propose("invented_revenue", "$100,000")
    with pytest.raises(ValidationError):
        CampaignContext.model_validate({"roi": {"status": "client_confirmed", "value": "400%"}})


def test_round_trip_preserves_status_and_provenance():
    ctx = CampaignContext().confirm("business_goal", "Brand awareness", source_note="Client signed brief")
    ctx = ctx.propose("primary_kpi", "Instagram reach", rationale="Suggested KPI, not agreed")
    rebuilt = CampaignContext.model_validate_json(ctx.model_dump_json())
    assert rebuilt == ctx
    assert rebuilt.primary_kpi.status == DiscoveryStatus.PROPOSED
