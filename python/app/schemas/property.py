"""Pydantic schemas for Property model."""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class PropertyBase(BaseModel):
    """Base schema for Property."""
    name: str = Field(..., min_length=1, max_length=255)
    address: str = Field(..., min_length=1, max_length=500)
    city: str = Field(..., min_length=1, max_length=100)


class PropertyCreate(PropertyBase):
    """Schema for creating a new property."""
    airbnb_listing_id: Optional[str] = None
    airbnb_thread_id: Optional[str] = None
    bedrooms: int = 1
    bathrooms: float = 1.0
    max_guests: int = 2
    standard_cleaning_rate: float = 80.0
    estimated_cleaning_duration_minutes: int = 120
    access_instructions: Optional[str] = None
    special_notes: Optional[str] = None
    wifi_name: Optional[str] = None
    wifi_password: Optional[str] = None
    amenities: Dict[str, Any] = Field(default_factory=dict)


class PropertyUpdate(BaseModel):
    """Schema for updating a property."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    address: Optional[str] = Field(None, min_length=1, max_length=500)
    city: Optional[str] = Field(None, min_length=1, max_length=100)
    airbnb_listing_id: Optional[str] = None
    airbnb_thread_id: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[float] = None
    max_guests: Optional[int] = None
    standard_cleaning_rate: Optional[float] = None
    estimated_cleaning_duration_minutes: Optional[int] = None
    access_instructions: Optional[str] = None
    special_notes: Optional[str] = None
    wifi_name: Optional[str] = None
    wifi_password: Optional[str] = None
    amenities: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class PropertyResponse(PropertyBase):
    """Schema for property response."""
    id: int
    airbnb_listing_id: Optional[str]
    bedrooms: int
    bathrooms: float
    max_guests: int
    standard_cleaning_rate: float
    estimated_cleaning_duration_minutes: int
    access_instructions: Optional[str]
    special_notes: Optional[str]
    wifi_name: Optional[str]
    amenities: Dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PropertyListResponse(BaseModel):
    """Schema for list of properties response."""
    items: List[PropertyResponse]
    total: int
    page: int
    page_size: int
