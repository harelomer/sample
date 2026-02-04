"""Property model for Airbnb properties."""

from sqlalchemy import Column, Integer, String, Float, Boolean, Text, JSON
from sqlalchemy.orm import relationship

from app.models.base import Base, TimestampMixin


class Property(Base, TimestampMixin):
    """
    Represents an Airbnb property managed by the system.

    Example properties: "Oakland House", "San Jose Apt", etc.
    """
    __tablename__ = "properties"

    id = Column(Integer, primary_key=True, index=True)

    # Basic info
    name = Column(String(255), nullable=False, index=True)
    address = Column(String(500), nullable=False)
    city = Column(String(100), nullable=False, index=True)

    # Airbnb integration
    airbnb_listing_id = Column(String(100), unique=True, index=True)
    airbnb_thread_id = Column(String(100))  # For messaging

    # Property details
    bedrooms = Column(Integer, default=1)
    bathrooms = Column(Float, default=1.0)
    max_guests = Column(Integer, default=2)

    # Cleaning details
    standard_cleaning_rate = Column(Float, default=80.0)  # Default payment
    estimated_cleaning_duration_minutes = Column(Integer, default=120)  # 2 hours default

    # Access info
    access_instructions = Column(Text)  # How to get in
    special_notes = Column(Text)  # Special cleaning instructions

    # Status
    is_active = Column(Boolean, default=True)

    # WiFi and amenities info
    wifi_name = Column(String(100))
    wifi_password = Column(String(100))
    amenities = Column(JSON, default=dict)  # {"parking": "driveway", "washer": true}

    # Relationships
    jobs = relationship("Job", back_populates="property", lazy="dynamic")
    cleaner_familiarities = relationship(
        "CleanerPropertyFamiliarity",
        back_populates="property",
        lazy="dynamic"
    )
    guests = relationship("Guest", back_populates="property", lazy="dynamic")

    def __repr__(self):
        return f"<Property {self.name} ({self.city})>"

    @property
    def short_name(self):
        """Get a short name for the property for messages."""
        return self.name.split()[0] if self.name else f"Property #{self.id}"
