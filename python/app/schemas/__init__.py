"""Pydantic schemas for API validation."""

from app.schemas.property import (
    PropertyCreate,
    PropertyUpdate,
    PropertyResponse,
    PropertyListResponse,
)
from app.schemas.cleaner import (
    CleanerCreate,
    CleanerUpdate,
    CleanerResponse,
    CleanerListResponse,
    CleanerRanking,
)
from app.schemas.guest import (
    GuestCreate,
    GuestUpdate,
    GuestResponse,
)
from app.schemas.job import (
    JobCreate,
    JobUpdate,
    JobResponse,
    JobListResponse,
    JobAssignment,
    JobOfferResponse,
    BatchJobOffer,
)
from app.schemas.message import (
    MessageCreate,
    MessageResponse,
    AIInterpretation,
    ConversationContextResponse,
)
from app.schemas.webhook import (
    WhatsAppWebhook,
    AirbnbWebhook,
    WebhookResponse,
)
from app.schemas.coordination import (
    CoordinationEventCreate,
    CoordinationEventResponse,
    CoordinationEventUpdate,
)

__all__ = [
    # Property
    "PropertyCreate",
    "PropertyUpdate",
    "PropertyResponse",
    "PropertyListResponse",
    # Cleaner
    "CleanerCreate",
    "CleanerUpdate",
    "CleanerResponse",
    "CleanerListResponse",
    "CleanerRanking",
    # Guest
    "GuestCreate",
    "GuestUpdate",
    "GuestResponse",
    # Job
    "JobCreate",
    "JobUpdate",
    "JobResponse",
    "JobListResponse",
    "JobAssignment",
    "JobOfferResponse",
    "BatchJobOffer",
    # Message
    "MessageCreate",
    "MessageResponse",
    "AIInterpretation",
    "ConversationContextResponse",
    # Webhook
    "WhatsAppWebhook",
    "AirbnbWebhook",
    "WebhookResponse",
    # Coordination
    "CoordinationEventCreate",
    "CoordinationEventResponse",
    "CoordinationEventUpdate",
]
