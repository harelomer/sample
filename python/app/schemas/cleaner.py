"""Pydantic schemas for Cleaner model."""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator
import phonenumbers


class CleanerBase(BaseModel):
    """Base schema for Cleaner."""
    name: str = Field(..., min_length=1, max_length=255)
    phone: str = Field(..., min_length=10, max_length=20)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """Validate and normalize phone number to E.164 format."""
        try:
            parsed = phonenumbers.parse(v, "US")
            if not phonenumbers.is_valid_number(parsed):
                raise ValueError("Invalid phone number")
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            raise ValueError("Invalid phone number format")


class CleanerCreate(CleanerBase):
    """Schema for creating a new cleaner."""
    email: Optional[str] = None
    preferred_channel: str = "whatsapp"
    whatsapp_chat_id: Optional[str] = None
    airbnb_user_id: Optional[str] = None
    availability_notes: Optional[str] = None
    preferred_cities: List[str] = Field(default_factory=list)
    max_distance_miles: Optional[float] = None
    default_rate: Optional[float] = None
    payment_method: Optional[str] = None
    payment_info: Optional[str] = None


class CleanerUpdate(BaseModel):
    """Schema for updating a cleaner."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    phone: Optional[str] = None
    email: Optional[str] = None
    preferred_channel: Optional[str] = None
    whatsapp_chat_id: Optional[str] = None
    airbnb_user_id: Optional[str] = None
    is_active: Optional[bool] = None
    is_available: Optional[bool] = None
    availability_notes: Optional[str] = None
    preferred_cities: Optional[List[str]] = None
    max_distance_miles: Optional[float] = None
    default_rate: Optional[float] = None
    payment_method: Optional[str] = None
    payment_info: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        """Validate phone if provided."""
        if v is None:
            return v
        try:
            parsed = phonenumbers.parse(v, "US")
            if not phonenumbers.is_valid_number(parsed):
                raise ValueError("Invalid phone number")
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except phonenumbers.NumberParseException:
            raise ValueError("Invalid phone number format")


class CleanerResponse(CleanerBase):
    """Schema for cleaner response."""
    id: int
    email: Optional[str]
    preferred_channel: str
    whatsapp_chat_id: Optional[str]
    airbnb_user_id: Optional[str]
    is_active: bool
    is_available: bool
    availability_notes: Optional[str]
    total_jobs_completed: int
    jobs_accepted: int
    jobs_rejected: int
    average_rating: float
    average_response_time_minutes: Optional[float]
    preferred_cities: List[str]
    response_rate: float
    acceptance_rate: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CleanerListResponse(BaseModel):
    """Schema for list of cleaners response."""
    items: List[CleanerResponse]
    total: int
    page: int
    page_size: int


class CleanerRanking(BaseModel):
    """Schema for cleaner ranking result."""
    cleaner_id: int
    cleaner_name: str
    score: float
    property_familiarity: float
    availability_score: float
    response_rate: float
    rating: float
    reasons: List[str]
