"""Pydantic schemas for webhook payloads."""

from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


# WhatsApp (Green API) Webhook Schemas

class WhatsAppSenderData(BaseModel):
    """Sender data from WhatsApp webhook."""
    model_config = ConfigDict(extra="allow")

    chatId: Optional[str] = None
    sender: Optional[str] = None
    senderName: Optional[str] = None
    chatName: Optional[str] = None


class WhatsAppTextMessageData(BaseModel):
    """Text message data from WhatsApp."""
    model_config = ConfigDict(extra="allow")

    textMessage: Optional[str] = None


class WhatsAppExtendedTextMessageData(BaseModel):
    """Extended text message data."""
    model_config = ConfigDict(extra="allow")

    text: Optional[str] = None


class WhatsAppMessageData(BaseModel):
    """Message data from WhatsApp webhook."""
    model_config = ConfigDict(extra="allow")

    typeMessage: Optional[str] = None
    textMessageData: Optional[WhatsAppTextMessageData] = None
    extendedTextMessageData: Optional[WhatsAppExtendedTextMessageData] = None


class WhatsAppWebhook(BaseModel):
    """
    WhatsApp webhook payload from Green API.
    Made flexible to handle various Green API formats.
    """
    model_config = ConfigDict(extra="allow")

    typeWebhook: Optional[str] = None
    instanceData: Optional[Dict[str, Any]] = None
    timestamp: Optional[int] = None
    idMessage: Optional[str] = None
    senderData: Optional[WhatsAppSenderData] = None
    messageData: Optional[WhatsAppMessageData] = None

    @property
    def chat_id(self) -> str:
        """Get normalized chat ID."""
        if self.senderData and self.senderData.chatId:
            return self.senderData.chatId
        if self.senderData and self.senderData.sender:
            return self.senderData.sender
        return ""

    @property
    def sender_phone(self) -> str:
        """Extract phone number from sender."""
        sender = ""
        if self.senderData:
            sender = self.senderData.sender or self.senderData.chatId or ""
        # Remove @c.us suffix
        if "@" in sender:
            return sender.split("@")[0]
        return sender

    @property
    def message_text(self) -> Optional[str]:
        """Get message text if it's a text message."""
        if not self.messageData:
            return None
        # Try regular text message
        if self.messageData.textMessageData and self.messageData.textMessageData.textMessage:
            return self.messageData.textMessageData.textMessage
        # Try extended text message
        if self.messageData.extendedTextMessageData and self.messageData.extendedTextMessageData.text:
            return self.messageData.extendedTextMessageData.text
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
