"""Pydantic schemas for Message model."""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class MessageCreate(BaseModel):
    """Schema for creating a message record."""
    channel: str
    direction: str
    sender_type: str
    cleaner_id: Optional[int] = None
    guest_id: Optional[int] = None
    external_message_id: Optional[str] = None
    external_chat_id: str
    external_sender_id: Optional[str] = None
    content: str
    content_type: str = "text"


class MessageResponse(BaseModel):
    """Schema for message response."""
    id: int
    channel: str
    direction: str
    sender_type: str
    cleaner_id: Optional[int]
    guest_id: Optional[int]
    external_chat_id: str
    content: str
    content_type: str
    ai_interpreted: bool
    ai_intent: Optional[str]
    ai_confidence: Optional[int]
    ai_extracted_data: Optional[Dict[str, Any]]
    processed: bool
    sent_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class AIInterpretation(BaseModel):
    """Schema for AI message interpretation result."""
    intent: str  # accept_job, reject_job, partial_accept, question, status_update, unclear
    confidence: int  # 0-100
    extracted_data: Dict[str, Any] = {}
    # For job responses
    accepted_job_ids: List[int] = []
    rejected_job_ids: List[int] = []
    # For questions
    question_type: Optional[str] = None
    question_about: Optional[str] = None
    # For status updates
    status_update: Optional[str] = None  # en_route, started, done
    # Suggested response
    suggested_response: str = ""
    # Additional context
    needs_clarification: bool = False
    clarification_question: Optional[str] = None


class ConversationContextResponse(BaseModel):
    """Schema for conversation context."""
    external_chat_id: str
    channel: str
    participant_type: Optional[str]
    cleaner_id: Optional[int]
    guest_id: Optional[int]
    active_job_offers: List[int]
    last_outbound_message: Optional[str]
    last_outbound_at: Optional[datetime]
    awaiting_response_for: Optional[str]
    conversation_state: str
    recent_messages: List[Dict[str, Any]]

    class Config:
        from_attributes = True
