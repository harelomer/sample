"""
Application-level dependency management.

Provides singleton instances of services to avoid re-instantiation
on every request and to manage resource lifecycle (e.g., HTTP clients).
"""

import logging
from typing import Optional

from app.services.ai_service import AIService
from app.services.messaging_service import MessagingService

logger = logging.getLogger(__name__)

# Singleton instances
_ai_service: Optional[AIService] = None
_messaging_service: Optional[MessagingService] = None


def get_ai_service() -> AIService:
    """Get or create the singleton AIService instance."""
    global _ai_service
    if _ai_service is None:
        _ai_service = AIService()
    return _ai_service


def get_messaging_service() -> MessagingService:
    """Get or create the singleton MessagingService instance."""
    global _messaging_service
    if _messaging_service is None:
        _messaging_service = MessagingService()
    return _messaging_service


async def shutdown_services():
    """Clean up service resources on application shutdown."""
    global _messaging_service, _ai_service
    if _messaging_service is not None:
        await _messaging_service.close()
        _messaging_service = None
    _ai_service = None
    logger.info("Services shut down")
