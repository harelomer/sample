"""Coordination models for guest-cleaner coordination events."""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime, Boolean, JSON
from sqlalchemy.orm import relationship
import enum

from app.models.base import Base, TimestampMixin


class EventType(str, enum.Enum):
    """Type of coordination event."""
    MISSING_ITEM = "missing_item"  # Guest reports something missing
    CLEANING_ISSUE = "cleaning_issue"  # Guest reports cleaning problem
    EARLY_CHECKIN = "early_checkin"  # Guest wants early check-in
    LATE_CHECKOUT = "late_checkout"  # Guest wants late checkout
    EARLY_CHECKOUT = "early_checkout"  # Guest left early
    MAINTENANCE = "maintenance"  # Maintenance issue
    SUPPLY_RESTOCK = "supply_restock"  # Need supplies restocked
    SCHEDULE_CHANGE = "schedule_change"  # Cleaning time change
    OTHER = "other"


class EventPriority(str, enum.Enum):
    """Priority of coordination event."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class EventStatus(str, enum.Enum):
    """Status of coordination event."""
    OPEN = "open"  # Just created
    ASSIGNED = "assigned"  # Assigned to someone
    IN_PROGRESS = "in_progress"  # Being handled
    WAITING_RESPONSE = "waiting_response"  # Waiting for cleaner/guest
    RESOLVED = "resolved"  # Completed
    CANCELLED = "cancelled"


class CoordinationEvent(Base, TimestampMixin):
    """
    Tracks coordination events between guests and cleaners.

    Examples:
    - Guest reports missing soap -> system messages cleaner to bring it
    - Guest wants early check-in -> system checks with cleaner
    - Cleaner running late -> system updates guest
    """
    __tablename__ = "coordination_events"

    id = Column(Integer, primary_key=True, index=True)

    # Event type and priority
    event_type = Column(String(50), nullable=False, index=True)
    priority = Column(String(20), default=EventPriority.MEDIUM.value)
    status = Column(String(50), default=EventStatus.OPEN.value, index=True)

    # Related entities
    property_id = Column(Integer, ForeignKey("properties.id"), index=True)
    guest_id = Column(Integer, ForeignKey("guests.id"), index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), index=True)
    assigned_cleaner_id = Column(Integer, ForeignKey("cleaners.id"), index=True)

    # Event details
    title = Column(String(255), nullable=False)
    description = Column(Text)

    # For specific event types
    requested_time = Column(DateTime)  # For early check-in requests
    original_time = Column(DateTime)  # Original scheduled time

    # Communication tracking
    guest_notified = Column(Boolean, default=False)
    cleaner_notified = Column(Boolean, default=False)
    guest_message_id = Column(Integer)  # Message sent to guest
    cleaner_message_id = Column(Integer)  # Message sent to cleaner

    # Resolution
    resolved_at = Column(DateTime)
    resolution_notes = Column(Text)
    resolution_outcome = Column(String(100))  # "approved", "denied", "modified"

    # AI tracking
    ai_categorized = Column(Boolean, default=False)
    ai_suggested_action = Column(Text)
    ai_confidence = Column(Integer)

    # Extra data
    extra_data = Column(JSON, default=dict)

    # Relationships
    rental_property = relationship("Property")
    guest = relationship("Guest", back_populates="coordination_events")
    job = relationship("Job")
    assigned_cleaner = relationship("Cleaner")

    def __repr__(self):
        return f"<CoordinationEvent {self.id} {self.event_type} {self.status}>"

    @property
    def needs_immediate_action(self) -> bool:
        """Check if event needs immediate attention."""
        return (
            self.priority in [EventPriority.HIGH.value, EventPriority.URGENT.value]
            and self.status in [EventStatus.OPEN.value, EventStatus.ASSIGNED.value]
        )

    def resolve(self, outcome: str, notes: str = None):
        """Mark event as resolved."""
        self.status = EventStatus.RESOLVED.value
        self.resolved_at = datetime.now(timezone.utc)
        self.resolution_outcome = outcome
        if notes:
            self.resolution_notes = notes
