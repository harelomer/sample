"""Pydantic schemas for webhook payloads."""

from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


# WhatsApp (Green API) Webhook Schemas

class WhatsAppSenderData(BaseModel):
    """Sender data from WhatsApp webhook."""
    chatId: str
    sender: str
    senderName: Optional[str] = None


class WhatsAppTextMessageData(BaseModel):
    """Text message data from WhatsApp."""
    textMessage: str


class WhatsAppMessageData(BaseModel):
    """Message data from WhatsApp webhook."""
    typeMessage: str
    textMessageData: Optional[WhatsAppTextMessageData] = None
    # Can extend for other message types (image, location, etc.)


class WhatsAppWebhook(BaseModel):
    """
    WhatsApp webhook payload from Green API.

    Example:
    {
        "typeWebhook": "incomingMessageReceived",
        "instanceData": {...},
        "timestamp": 1234567890,
        "idMessage": "msg_123",
        "senderData": {
            "chatId": "15551234567@c.us",
            "sender": "15551234567@c.us",
            "senderName": "Maria"
        },
        "messageData": {
            "typeMessage": "textMessage",
            "textMessageData": {
                "textMessage": "ok"
            }
        }
    }
    """
    typeWebhook: str
    instanceData: Optional[Dict[str, Any]] = None
    timestamp: Optional[int] = None
    idMessage: Optional[str] = None
    senderData: WhatsAppSenderData
    messageData: WhatsAppMessageData

    @property
    def chat_id(self) -> str:
        """Get normalized chat ID."""
        return self.senderData.chatId

    @property
    def sender_phone(self) -> str:
        """Extract phone number from sender."""
        sender = self.senderData.sender
        # Remove @c.us suffix
        if "@" in sender:
            return sender.split("@")[0]
        return sender

    @property
    def message_text(self) -> Optional[str]:
        """Get message text if it's a text message."""
        if self.messageData.typeMessage == "textMessage" and self.messageData.textMessageData:
            return self.messageData.textMessageData.textMessage
        return None


# Airbnb Webhook Schemas

class AirbnbWebhook(BaseModel):
    """
    Airbnb message webhook payload.

    Note: Actual format varies based on Airbnb API version.
    This is a generalized schema.
    """
    message_id: Optional[str] = None
    thread_id: str
    user_id: str
    message: str
    created_at: Optional[datetime] = None
    reservation_id: Optional[str] = None
    listing_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def chat_id(self) -> str:
        """Get chat ID (thread_id for Airbnb)."""
        return self.thread_id


# Generic Webhook Response

class WebhookResponse(BaseModel):
    """Response schema for webhook endpoints."""
    success: bool
    message: str = "OK"
    message_id: Optional[int] = None
    action_taken: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
