"""Cleaner models."""

from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship

from app.models.base import Base, TimestampMixin


class Cleaner(Base, TimestampMixin):
    """
    Represents a cleaner who can be assigned to cleaning jobs.

    Cleaners can communicate via WhatsApp or Airbnb messages.
    """
    __tablename__ = "cleaners"

    id = Column(Integer, primary_key=True, index=True)

    # Basic info
    name = Column(String(255), nullable=False, index=True)
    phone = Column(String(20), unique=True, index=True)  # E.164 format: +15551234567
    email = Column(String(255), unique=True)

    # Communication preferences
    preferred_channel = Column(String(20), default="whatsapp")  # whatsapp, airbnb
    whatsapp_chat_id = Column(String(50))  # Format: 15551234567@c.us
    airbnb_user_id = Column(String(100))

    # Availability
    is_active = Column(Boolean, default=True)
    is_available = Column(Boolean, default=True)  # Can toggle off for vacations
    availability_notes = Column(Text)  # "Only weekends", "Not after 5pm"

    # Performance metrics
    total_jobs_completed = Column(Integer, default=0)
    total_jobs_offered = Column(Integer, default=0)
    jobs_accepted = Column(Integer, default=0)
    jobs_rejected = Column(Integer, default=0)
    jobs_no_response = Column(Integer, default=0)
    average_rating = Column(Float, default=5.0)
    average_response_time_minutes = Column(Float)  # How fast they typically respond

    # Geographic preferences
    preferred_cities = Column(JSON, default=list)  # ["Oakland", "San Jose"]
    max_distance_miles = Column(Float)

    # Payment preferences
    default_rate = Column(Float)  # If different from property rate
    payment_method = Column(String(50))  # "venmo", "zelle", "cash"
    payment_info = Column(String(255))  # Venmo username, etc.

    # Relationships
    property_familiarities = relationship(
        "CleanerPropertyFamiliarity",
        back_populates="cleaner",
        lazy="dynamic"
    )
    job_offers = relationship("JobOffer", back_populates="cleaner", lazy="dynamic")
    messages = relationship("Message", back_populates="cleaner", lazy="dynamic")

    def __repr__(self):
        return f"<Cleaner {self.name} ({self.phone})>"

    @property
    def response_rate(self) -> float:
        """Calculate response rate as percentage."""
        if self.total_jobs_offered == 0:
            return 1.0  # New cleaners get benefit of doubt
        responded = self.jobs_accepted + self.jobs_rejected
        return responded / self.total_jobs_offered

    @property
    def acceptance_rate(self) -> float:
        """Calculate job acceptance rate."""
        responded = self.jobs_accepted + self.jobs_rejected
        if responded == 0:
            return 0.5  # Neutral for new cleaners
        return self.jobs_accepted / responded


class CleanerPropertyFamiliarity(Base, TimestampMixin):
    """
    Tracks how familiar a cleaner is with a specific property.

    Higher familiarity scores mean the cleaner knows the property well
    and should be preferred for assignments.
    """
    __tablename__ = "cleaner_property_familiarities"

    id = Column(Integer, primary_key=True, index=True)
    cleaner_id = Column(Integer, ForeignKey("cleaners.id"), nullable=False, index=True)
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False, index=True)

    # Familiarity metrics
    times_cleaned = Column(Integer, default=0)
    last_cleaned_at = Column(String(50))  # ISO datetime
    familiarity_score = Column(Float, default=0.0)  # 0.0 to 1.0

    # Notes
    notes = Column(Text)  # "Knows where spare keys are", "Has parking code"

    # Preferences
    is_preferred = Column(Boolean, default=False)  # Manually marked as preferred
    is_blacklisted = Column(Boolean, default=False)  # Don't assign to this property

    # Relationships
    cleaner = relationship("Cleaner", back_populates="property_familiarities")
    rental_property = relationship("Property", back_populates="cleaner_familiarities")

    def __repr__(self):
        return f"<CleanerPropertyFamiliarity cleaner={self.cleaner_id} property={self.property_id}>"

    def update_after_cleaning(self):
        """Update familiarity after a completed cleaning."""
        from datetime import datetime, timezone
        self.times_cleaned += 1
        self.last_cleaned_at = datetime.now(timezone.utc).isoformat()
        # Familiarity increases with each cleaning, max 1.0
        self.familiarity_score = min(1.0, 0.3 + (self.times_cleaned * 0.1))
