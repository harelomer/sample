"""Pydantic schemas for Guest model."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class GuestBase(BaseModel):
    """Base schema for Guest."""
    name: str = Field(..., min_length=1, max_length=255)


class GuestCreate(GuestBase):
    """Schema for creating a new guest."""
    phone: Optional[str] = None
    email: Optional[str] = None
    airbnb_user_id: Optional[str] = None
    airbnb_thread_id: Optional[str] = None
    whatsapp_chat_id: Optional[str] = None
    property_id: int
    reservation_id: str
    check_in_date: datetime
    check_out_date: datetime
    check_in_time: str = "15:00"
    check_out_time: str = "11:00"
    adults: int = 1
    children: int = 0
    special_requests: Optional[str] = None


class GuestUpdate(BaseModel):
    """Schema for updating a guest."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    phone: Optional[str] = None
    email: Optional[str] = None
    airbnb_user_id: Optional[str] = None
    airbnb_thread_id: Optional[str] = None
    whatsapp_chat_id: Optional[str] = None
    check_in_date: Optional[datetime] = None
    check_out_date: Optional[datetime] = None
    check_in_time: Optional[str] = None
    check_out_time: Optional[str] = None
    status: Optional[str] = None
    special_requests: Optional[str] = None
    internal_notes: Optional[str] = None


class GuestResponse(GuestBase):
    """Schema for guest response."""
    id: int
    phone: Optional[str]
    email: Optional[str]
    airbnb_user_id: Optional[str]
    airbnb_thread_id: Optional[str]
    property_id: int
    reservation_id: str
    check_in_date: datetime
    check_out_date: datetime
    check_in_time: str
    check_out_time: str
    adults: int
    children: int
    status: str
    special_requests: Optional[str]
    is_currently_staying: bool
    stay_length_nights: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
