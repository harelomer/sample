"""Guest model for Airbnb guests."""

from datetime import datetime
from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime
from sqlalchemy.orm import relationship

from app.models.base import Base, TimestampMixin


class Guest(Base, TimestampMixin):
    """
    Represents a guest currently staying at or booked for a property.

    Guests communicate via Airbnb messages or WhatsApp.
    """
    __tablename__ = "guests"

    id = Column(Integer, primary_key=True, index=True)

    # Basic info
    name = Column(String(255), nullable=False)
    phone = Column(String(20), index=True)  # E.164 format
    email = Column(String(255))

    # Airbnb info
    airbnb_user_id = Column(String(100), index=True)
    airbnb_thread_id = Column(String(100), index=True)  # Conversation thread

    # WhatsApp
    whatsapp_chat_id = Column(String(50))  # Format: 15551234567@c.us

    # Current/upcoming stay
    property_id = Column(Integer, ForeignKey("properties.id"), index=True)
    reservation_id = Column(String(100), unique=True, index=True)

    # Stay dates
    check_in_date = Column(DateTime, nullable=False)
    check_out_date = Column(DateTime, nullable=False)
    check_in_time = Column(String(10), default="15:00")  # 3 PM default
    check_out_time = Column(String(10), default="11:00")  # 11 AM default

    # Guest count
    adults = Column(Integer, default=1)
    children = Column(Integer, default=0)

    # Status
    status = Column(String(50), default="upcoming")  # upcoming, checked_in, checked_out

    # Notes
    special_requests = Column(Text)
    internal_notes = Column(Text)

    # Relationships
    rental_property = relationship("Property", back_populates="guests")
    messages = relationship("Message", back_populates="guest", lazy="dynamic")
    coordination_events = relationship("CoordinationEvent", back_populates="guest", lazy="dynamic")

    def __repr__(self):
        return f"<Guest {self.name} at property {self.property_id}>"

    @property
    def is_currently_staying(self) -> bool:
        """Check if guest is currently at the property."""
        now = datetime.utcnow()
        return (
            self.check_in_date <= now <= self.check_out_date
            and self.status == "checked_in"
        )

    @property
    def stay_length_nights(self) -> int:
        """Calculate length of stay in nights."""
        if self.check_out_date and self.check_in_date:
            delta = self.check_out_date - self.check_in_date
            return delta.days
        return 0
