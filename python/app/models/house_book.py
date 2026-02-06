"""House Book models for property-specific notes and instructions."""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime, Enum
from sqlalchemy.orm import relationship
import enum

from app.models.base import Base, TimestampMixin


class HouseBookEntryType(str, enum.Enum):
    """Type of house book entry."""
    STATIC = "static"  # Permanent house rules, instructions
    LIVE = "live"  # Temporary updates, deliveries


class HouseBookEntry(Base, TimestampMixin):
    """
    Property house book entry - contains instructions, updates, and notes
    for cleaners about a specific property.
    """
    __tablename__ = "house_book_entries"

    id = Column(Integer, primary_key=True, index=True)

    # Property
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False, index=True)

    # Entry details
    entry_type = Column(String(50), default=HouseBookEntryType.STATIC.value)
    title = Column(String(200))  # e.g., "House Rules", "Toilet Paper Delivery"
    content = Column(Text, nullable=False)  # The actual message

    # Status
    active = Column(Boolean, default=True, index=True)

    # For live entries - auto-expire after certain time
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Priority for display order
    priority = Column(Integer, default=0)  # Higher = more important

    # Relationships
    rental_property = relationship("Property", back_populates="house_book_entries")

    def __repr__(self):
        return f"<HouseBookEntry {self.id} {self.entry_type} for property {self.property_id}>"

    @property
    def is_expired(self) -> bool:
        """Check if this entry has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_active(self) -> bool:
        """Check if entry is active and not expired."""
        return self.active and not self.is_expired
