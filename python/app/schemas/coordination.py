"""Pydantic schemas for Coordination events."""

from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

from app.models.coordination import EventType, EventPriority, EventStatus


class CoordinationEventCreate(BaseModel):
    """Schema for creating a coordination event."""
    event_type: str
    priority: str = EventPriority.MEDIUM.value
    property_id: int
    guest_id: Optional[int] = None
    job_id: Optional[int] = None
    assigned_cleaner_id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    requested_time: Optional[datetime] = None
    original_time: Optional[datetime] = None
    extra_data: Dict[str, Any] = Field(default_factory=dict)


class CoordinationEventUpdate(BaseModel):
    """Schema for updating a coordination event."""
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_cleaner_id: Optional[int] = None
    description: Optional[str] = None
    resolution_notes: Optional[str] = None
    resolution_outcome: Optional[str] = None
    guest_notified: Optional[bool] = None
    cleaner_notified: Optional[bool] = None


class CoordinationEventResponse(BaseModel):
    """Schema for coordination event response."""
    id: int
    event_type: str
    priority: str
    status: str
    property_id: int
    guest_id: Optional[int]
    job_id: Optional[int]
    assigned_cleaner_id: Optional[int]
    title: str
    description: Optional[str]
    requested_time: Optional[datetime]
    original_time: Optional[datetime]
    guest_notified: bool
    cleaner_notified: bool
    resolved_at: Optional[datetime]
    resolution_notes: Optional[str]
    resolution_outcome: Optional[str]
    ai_categorized: bool
    ai_suggested_action: Optional[str]
    needs_immediate_action: bool
    extra_data: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
