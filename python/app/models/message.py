"""Message models for conversation tracking."""

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime, Boolean, JSON
from sqlalchemy.orm import relationship, attributes
import enum

from app.models.base import Base, TimestampMixin


class MessageDirection(str, enum.Enum):
    """Direction of message."""
    INBOUND = "inbound"  # From user to system
    OUTBOUND = "outbound"  # From system to user


class MessageChannel(str, enum.Enum):
    """Communication channel."""
    WHATSAPP = "whatsapp"
    AIRBNB = "airbnb"
    SMS = "sms"
    EMAIL = "email"


class SenderType(str, enum.Enum):
    """Type of message sender."""
    CLEANER = "cleaner"
    GUEST = "guest"
    MANAGER = "manager"
    SYSTEM = "system"


class Message(Base, TimestampMixin):
    """
    Stores all messages for conversation history and context.

    This is essential for AI to understand context when interpreting
    messages like "ok" or "can't".
    """
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)

    # Channel info
    channel = Column(String(20), nullable=False, index=True)  # whatsapp, airbnb
    direction = Column(String(20), nullable=False)  # inbound, outbound

    # Sender/recipient identification
    sender_type = Column(String(20), index=True)  # cleaner, guest, manager, system
    cleaner_id = Column(Integer, ForeignKey("cleaners.id"), index=True)
    guest_id = Column(Integer, ForeignKey("guests.id"), index=True)

    # External IDs
    external_message_id = Column(String(100), index=True)  # WhatsApp/Airbnb message ID
    external_chat_id = Column(String(100), index=True)  # Chat/thread ID
    external_sender_id = Column(String(100))  # Phone number or user ID

    # Content
    content = Column(Text, nullable=False)
    content_type = Column(String(50), default="text")  # text, image, location

    # AI interpretation
    ai_interpreted = Column(Boolean, default=False)
    ai_intent = Column(String(100))  # accept_job, reject_job, question, status_update
    ai_confidence = Column(Integer)  # 0-100
    ai_extracted_data = Column(JSON)  # {"accepted_jobs": [1, 3], "rejected_jobs": [2]}

    # Context
    related_job_ids = Column(JSON, default=list)  # Jobs this message relates to
    related_offer_ids = Column(JSON, default=list)  # Job offers referenced

    # Processing
    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime(timezone=True))
    processing_notes = Column(Text)

    # Timestamps
    sent_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    delivered_at = Column(DateTime(timezone=True))
    read_at = Column(DateTime(timezone=True))

    # Relationships
    cleaner = relationship("Cleaner", back_populates="messages")
    guest = relationship("Guest", back_populates="messages")

    def __repr__(self):
        return f"<Message {self.id} {self.channel} {self.direction}>"


class ConversationContext(Base, TimestampMixin):
    """
    Maintains conversation context for AI interpretation.

    When a cleaner replies "ok", we need to know what was just offered
    to them to correctly interpret the response.
    """
    __tablename__ = "conversation_contexts"

    id = Column(Integer, primary_key=True, index=True)

    # Identification
    external_chat_id = Column(String(100), unique=True, index=True)  # WhatsApp/Airbnb chat ID
    channel = Column(String(20), nullable=False)

    # Participant
    participant_type = Column(String(20))  # cleaner, guest
    cleaner_id = Column(Integer, ForeignKey("cleaners.id"), index=True)
    guest_id = Column(Integer, ForeignKey("guests.id"), index=True)

    # Current context
    active_job_offers = Column(JSON, default=list)  # Job offer IDs currently pending
    last_outbound_message = Column(Text)  # What we last said to them
    last_outbound_at = Column(DateTime(timezone=True))
    awaiting_response_for = Column(String(100))  # "job_confirmation", "question_answer"

    # Batch context
    active_batch_id = Column(String(50))
    batch_jobs = Column(JSON, default=list)  # Jobs in current batch

    # Recent history (for AI context)
    recent_messages = Column(JSON, default=list)  # Last N messages as list of dicts

    # State
    conversation_state = Column(String(50), default="idle")  # idle, awaiting_job_response, etc.

    def __repr__(self):
        return f"<ConversationContext {self.external_chat_id}>"

    def add_message_to_history(self, message_dict: dict, max_messages: int = 20):
        """Add message to recent history, maintaining max size."""
        if self.recent_messages is None:
            self.recent_messages = []
        # Create a new list to ensure SQLAlchemy detects the mutation
        updated = list(self.recent_messages)
        updated.append(message_dict)
        if len(updated) > max_messages:
            updated = updated[-max_messages:]
        self.recent_messages = updated
        attributes.flag_modified(self, "recent_messages")

    def get_context_for_ai(self) -> str:
        """Format context for AI interpretation."""
        context_parts = []

        if self.last_outbound_message:
            context_parts.append(f"Last message sent to user: {self.last_outbound_message}")

        if self.awaiting_response_for:
            context_parts.append(f"Awaiting response for: {self.awaiting_response_for}")

        if self.active_job_offers:
            context_parts.append(f"Pending job offer IDs: {self.active_job_offers}")

        if self.batch_jobs:
            context_parts.append(f"Batch contains jobs: {self.batch_jobs}")

        return "\n".join(context_parts)
