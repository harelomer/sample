"""Job models for cleaning jobs and offers."""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, Text, DateTime, Enum
from sqlalchemy.orm import relationship
import enum

from app.models.base import Base, TimestampMixin


class JobStatus(str, enum.Enum):
    """Status of a cleaning job."""
    PENDING = "pending"  # Job created, not yet offered
    BATCHED = "batched"  # Added to batch for evening delivery
    OFFERED = "offered"  # Offered to a cleaner, awaiting response
    CONFIRMED = "confirmed"  # Cleaner accepted
    EN_ROUTE = "en_route"  # Cleaner on the way
    IN_PROGRESS = "in_progress"  # Cleaner is cleaning
    COMPLETED = "completed"  # Job finished
    CANCELLED = "cancelled"  # Job cancelled
    ESCALATED = "escalated"  # No cleaner available, needs manual intervention


class JobUrgency(str, enum.Enum):
    """Urgency level of a job."""
    NORMAL = "normal"  # Standard batched delivery
    SAME_DAY = "same_day"  # Same day, send immediately
    URGENT = "urgent"  # Last minute, aggressive reminders


class JobType(str, enum.Enum):
    """Type of cleaning job."""
    TURNOVER = "turnover"  # Between guests
    DEEP_CLEAN = "deep_clean"  # Thorough cleaning
    TOUCH_UP = "touch_up"  # Quick clean for issue
    RESTOCK = "restock"  # Just restock supplies


class Job(Base, TimestampMixin):
    """
    Represents a cleaning job that needs to be assigned and completed.
    """
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)

    # Property
    property_id = Column(Integer, ForeignKey("properties.id"), nullable=False, index=True)

    # Job details
    job_type = Column(String(50), default=JobType.TURNOVER.value)
    status = Column(String(50), default=JobStatus.PENDING.value, index=True)
    urgency = Column(String(50), default=JobUrgency.NORMAL.value)

    # Scheduling
    scheduled_date = Column(DateTime(timezone=True), nullable=False, index=True)
    scheduled_time = Column(String(10))  # "14:00"
    estimated_duration_minutes = Column(Integer, default=120)
    deadline = Column(DateTime(timezone=True))  # Must be done by (e.g., guest check-in)

    # Assignment
    assigned_cleaner_id = Column(Integer, ForeignKey("cleaners.id"), index=True)
    assignment_attempts = Column(Integer, default=0)
    max_assignment_attempts = Column(Integer, default=5)

    # Payment
    payment_amount = Column(Float)
    payment_status = Column(String(50), default="pending")  # pending, paid

    # Related booking
    guest_id = Column(Integer, ForeignKey("guests.id"), index=True)
    previous_guest_checkout = Column(DateTime(timezone=True))
    next_guest_checkin = Column(DateTime(timezone=True))

    # Execution tracking
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    actual_duration_minutes = Column(Integer)

    # Notes
    notes = Column(Text)  # Manager notes
    cleaner_notes = Column(Text)  # Notes from cleaner

    # Reminders
    eve_reminder_sent = Column(Boolean, nullable=True, default=False)  # Evening-before reminder sent

    # Batch tracking
    batch_id = Column(String(50), index=True)  # Groups jobs sent together

    # Relationships
    rental_property = relationship("Property", back_populates="jobs")
    assigned_cleaner = relationship("Cleaner", foreign_keys=[assigned_cleaner_id])
    guest = relationship("Guest")
    offers = relationship("JobOffer", back_populates="job", lazy="dynamic")
    status_history = relationship("JobStatusHistory", back_populates="job", lazy="dynamic")

    def __repr__(self):
        return f"<Job {self.id} {self.job_type} at property {self.property_id}>"

    @property
    def is_same_day(self) -> bool:
        """Check if job is for today."""
        if not self.scheduled_date:
            return False
        today = datetime.now(timezone.utc).date()
        return self.scheduled_date.date() == today

    @property
    def is_overdue(self) -> bool:
        """Check if job is past its deadline."""
        if not self.deadline:
            return False
        now = datetime.now(timezone.utc)
        deadline = self.deadline.replace(tzinfo=timezone.utc) if self.deadline.tzinfo is None else self.deadline
        return now > deadline

    def update_status(self, new_status: JobStatus, notes: str = None):
        """Update job status and create history entry."""
        old_status = self.status
        self.status = new_status.value
        # Status history is created separately via the service


class JobOffer(Base, TimestampMixin):
    """
    Tracks job offers to cleaners.

    A single job may be offered to multiple cleaners in sequence
    until one accepts.
    """
    __tablename__ = "job_offers"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)
    cleaner_id = Column(Integer, ForeignKey("cleaners.id"), nullable=False, index=True)

    # Offer details
    offered_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = Column(DateTime(timezone=True))  # When offer times out
    offered_amount = Column(Float)  # Payment offered

    # Response
    status = Column(String(50), default="pending")  # pending, accepted, rejected, expired, cancelled
    responded_at = Column(DateTime(timezone=True))
    response_message = Column(Text)  # Original response text

    # Tracking
    reminder_count = Column(Integer, default=0)
    last_reminder_at = Column(DateTime(timezone=True))

    # Part of batch?
    batch_id = Column(String(50), index=True)
    batch_position = Column(Integer)  # Position in batch message (1, 2, 3...)

    # Relationships
    job = relationship("Job", back_populates="offers")
    cleaner = relationship("Cleaner", back_populates="job_offers")

    def __repr__(self):
        return f"<JobOffer job={self.job_id} cleaner={self.cleaner_id} status={self.status}>"

    @property
    def is_expired(self) -> bool:
        """Check if offer has expired."""
        if not self.expires_at:
            return False
        now = datetime.now(timezone.utc)
        expires = self.expires_at.replace(tzinfo=timezone.utc) if self.expires_at.tzinfo is None else self.expires_at
        return now > expires and self.status == "pending"


class JobStatusHistory(Base, TimestampMixin):
    """
    Tracks status changes for jobs for auditing.
    """
    __tablename__ = "job_status_history"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)

    # Status change
    from_status = Column(String(50))
    to_status = Column(String(50), nullable=False)
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    # Context
    changed_by = Column(String(50))  # "system", "cleaner", "manager"
    notes = Column(Text)
    trigger_message_id = Column(Integer)  # Message that triggered change

    # Relationships
    job = relationship("Job", back_populates="status_history")

    def __repr__(self):
        return f"<JobStatusHistory job={self.job_id} {self.from_status} -> {self.to_status}>"
