"""Pydantic schemas for Job model."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field

from app.models.job import JobStatus, JobUrgency, JobType


class JobBase(BaseModel):
    """Base schema for Job."""
    property_id: int
    scheduled_date: datetime


class JobCreate(JobBase):
    """Schema for creating a new job."""
    job_type: str = JobType.TURNOVER.value
    urgency: str = JobUrgency.NORMAL.value
    scheduled_time: Optional[str] = None
    estimated_duration_minutes: int = 120
    deadline: Optional[datetime] = None
    payment_amount: Optional[float] = None
    guest_id: Optional[int] = None
    previous_guest_checkout: Optional[datetime] = None
    next_guest_checkin: Optional[datetime] = None
    notes: Optional[str] = None


class JobUpdate(BaseModel):
    """Schema for updating a job."""
    scheduled_date: Optional[datetime] = None
    scheduled_time: Optional[str] = None
    urgency: Optional[str] = None
    status: Optional[str] = None
    assigned_cleaner_id: Optional[int] = None
    deadline: Optional[datetime] = None
    payment_amount: Optional[float] = None
    notes: Optional[str] = None
    cleaner_notes: Optional[str] = None


class JobOfferResponse(BaseModel):
    """Schema for job offer response."""
    id: int
    job_id: int
    cleaner_id: int
    cleaner_name: str
    offered_at: datetime
    expires_at: Optional[datetime]
    offered_amount: Optional[float]
    status: str
    responded_at: Optional[datetime]
    response_message: Optional[str]
    reminder_count: int

    class Config:
        from_attributes = True


class JobResponse(JobBase):
    """Schema for job response."""
    id: int
    job_type: str
    status: str
    urgency: str
    scheduled_time: Optional[str]
    estimated_duration_minutes: int
    deadline: Optional[datetime]
    assigned_cleaner_id: Optional[int]
    assigned_cleaner_name: Optional[str] = None
    assignment_attempts: int
    payment_amount: Optional[float]
    payment_status: str
    guest_id: Optional[int]
    previous_guest_checkout: Optional[datetime]
    next_guest_checkin: Optional[datetime]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    actual_duration_minutes: Optional[int]
    notes: Optional[str]
    cleaner_notes: Optional[str]
    batch_id: Optional[str]
    is_same_day: bool
    is_overdue: bool
    property_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class JobListResponse(BaseModel):
    """Schema for list of jobs response."""
    items: List[JobResponse]
    total: int
    page: int
    page_size: int


class JobAssignment(BaseModel):
    """Schema for manual job assignment."""
    cleaner_id: int
    payment_amount: Optional[float] = None
    notes: Optional[str] = None


class BatchJobOffer(BaseModel):
    """Schema for batch job offer."""
    cleaner_id: int
    job_ids: List[int]
    message: Optional[str] = None
