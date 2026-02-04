"""
Webhook handlers for WhatsApp and Airbnb messages.

Handles incoming messages and processes them through the AI service.
"""

import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import get_db
from app.models.cleaner import Cleaner
from app.models.guest import Guest
from app.models.job import Job, JobOffer, JobStatus
from app.models.message import Message, ConversationContext, MessageDirection, MessageChannel, SenderType
from app.schemas.webhook import WhatsAppWebhook, AirbnbWebhook, WebhookResponse
from app.services.ai_service import AIService
from app.services.messaging_service import MessagingService
from app.services.job_service import JobService
from app.services.coordination_service import CoordinationService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhooks"])


@router.post("/whatsapp", response_model=WebhookResponse)
async def handle_whatsapp_webhook(
    payload: WhatsAppWebhook,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle incoming WhatsApp messages via Green API webhook.

    This endpoint:
    1. Identifies the sender (cleaner or guest)
    2. Stores the message
    3. Uses AI to interpret the message
    4. Takes appropriate action based on interpretation
    """
    # Only process incoming messages
    if payload.typeWebhook != "incomingMessageReceived":
        return WebhookResponse(success=True, message="Ignored non-message webhook")

    message_text = payload.message_text
    if not message_text:
        return WebhookResponse(success=True, message="Ignored non-text message")

    chat_id = payload.chat_id
    sender_phone = payload.sender_phone
    sender_name = payload.senderData.senderName

    logger.info(f"Received WhatsApp message from {sender_phone}: {message_text[:50]}...")

    # Identify sender
    sender_type, cleaner, guest = await _identify_sender(db, sender_phone, chat_id)

    # Store the message
    message = Message(
        channel=MessageChannel.WHATSAPP.value,
        direction=MessageDirection.INBOUND.value,
        sender_type=sender_type,
        cleaner_id=cleaner.id if cleaner else None,
        guest_id=guest.id if guest else None,
        external_message_id=payload.idMessage,
        external_chat_id=chat_id,
        external_sender_id=sender_phone,
        content=message_text,
        content_type="text",
        sent_at=datetime.utcnow()
    )
    db.add(message)
    await db.flush()

    # Process message based on sender type
    if sender_type == SenderType.CLEANER.value and cleaner:
        result = await _process_cleaner_message(
            db=db,
            message=message,
            cleaner=cleaner,
            message_text=message_text,
            chat_id=chat_id
        )
    elif sender_type == SenderType.GUEST.value and guest:
        result = await _process_guest_message(
            db=db,
            message=message,
            guest=guest,
            message_text=message_text
        )
    else:
        # Unknown sender - could be new contact
        logger.warning(f"Unknown sender: {sender_phone}")
        result = {
            "action_taken": "unknown_sender",
            "details": {"phone": sender_phone, "name": sender_name}
        }

    message.processed = True
    message.processed_at = datetime.utcnow()

    return WebhookResponse(
        success=True,
        message="Message processed",
        message_id=message.id,
        action_taken=result.get("action_taken"),
        details=result.get("details", {})
    )


@router.post("/airbnb", response_model=WebhookResponse)
async def handle_airbnb_webhook(
    payload: AirbnbWebhook,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle incoming Airbnb messages.

    Similar flow to WhatsApp but with Airbnb-specific identifiers.
    """
    message_text = payload.message
    thread_id = payload.thread_id
    user_id = payload.user_id

    logger.info(f"Received Airbnb message from {user_id}: {message_text[:50]}...")

    # Find guest by Airbnb user ID or thread ID
    guest_result = await db.execute(
        select(Guest).where(
            (Guest.airbnb_user_id == user_id) |
            (Guest.airbnb_thread_id == thread_id)
        )
    )
    guest = guest_result.scalar_one_or_none()

    # Store the message
    message = Message(
        channel=MessageChannel.AIRBNB.value,
        direction=MessageDirection.INBOUND.value,
        sender_type=SenderType.GUEST.value if guest else "unknown",
        guest_id=guest.id if guest else None,
        external_message_id=payload.message_id,
        external_chat_id=thread_id,
        external_sender_id=user_id,
        content=message_text,
        content_type="text",
        sent_at=payload.created_at or datetime.utcnow()
    )
    db.add(message)
    await db.flush()

    # Process guest message
    if guest:
        result = await _process_guest_message(
            db=db,
            message=message,
            guest=guest,
            message_text=message_text
        )
    else:
        logger.warning(f"Unknown Airbnb user: {user_id}")
        result = {
            "action_taken": "unknown_sender",
            "details": {"user_id": user_id, "thread_id": thread_id}
        }

    message.processed = True
    message.processed_at = datetime.utcnow()

    return WebhookResponse(
        success=True,
        message="Message processed",
        message_id=message.id,
        action_taken=result.get("action_taken"),
        details=result.get("details", {})
    )


async def _identify_sender(
    db: AsyncSession,
    phone: str,
    chat_id: str
) -> tuple[str, Optional[Cleaner], Optional[Guest]]:
    """Identify sender by phone number or chat ID."""
    # Try to find cleaner
    # First try by chat ID, then by phone
    cleaner_result = await db.execute(
        select(Cleaner).where(
            (Cleaner.whatsapp_chat_id == chat_id) |
            (Cleaner.phone.contains(phone))
        )
    )
    cleaner = cleaner_result.scalar_one_or_none()

    if cleaner:
        # Update chat ID if not set
        if not cleaner.whatsapp_chat_id:
            cleaner.whatsapp_chat_id = chat_id
        return SenderType.CLEANER.value, cleaner, None

    # Try to find guest
    guest_result = await db.execute(
        select(Guest).where(
            (Guest.whatsapp_chat_id == chat_id) |
            (Guest.phone.contains(phone))
        )
    )
    guest = guest_result.scalar_one_or_none()

    if guest:
        if not guest.whatsapp_chat_id:
            guest.whatsapp_chat_id = chat_id
        return SenderType.GUEST.value, None, guest

    return "unknown", None, None


async def _process_cleaner_message(
    db: AsyncSession,
    message: Message,
    cleaner: Cleaner,
    message_text: str,
    chat_id: str
) -> dict:
    """Process a message from a cleaner."""
    ai_service = AIService()
    messaging_service = MessagingService()
    job_service = JobService(db)

    # Get conversation context
    context_result = await db.execute(
        select(ConversationContext).where(
            ConversationContext.external_chat_id == chat_id
        )
    )
    context = context_result.scalar_one_or_none()

    context_dict = {}
    if context:
        context_dict = {
            "last_outbound_message": context.last_outbound_message,
            "awaiting_response_for": context.awaiting_response_for,
            "recent_messages": context.recent_messages or []
        }

    # Get pending job offers for this cleaner
    pending_offers = await job_service.get_pending_offers_for_cleaner(cleaner.id)
    pending_jobs = []
    for offer in pending_offers:
        job = offer.job
        pending_jobs.append({
            "offer_id": offer.id,
            "job_id": job.id,
            "property_name": job.property.short_name if job.property else f"Property #{job.property_id}",
            "date": job.scheduled_date.strftime("%A %b %d") if job.scheduled_date else "TBD",
            "time": job.scheduled_time or "TBD",
            "batch_position": offer.batch_position
        })

    # Interpret the message
    interpretation = await ai_service.interpret_cleaner_message(
        message=message_text,
        context=context_dict,
        pending_jobs=pending_jobs
    )

    # Update message with AI interpretation
    message.ai_interpreted = True
    message.ai_intent = interpretation.intent
    message.ai_confidence = interpretation.confidence
    message.ai_extracted_data = {
        "accepted_jobs": interpretation.accepted_job_ids,
        "rejected_jobs": interpretation.rejected_job_ids,
        "status_update": interpretation.status_update
    }

    # Take action based on intent
    result = {"action_taken": interpretation.intent, "details": {}}

    if interpretation.intent == "accept_job":
        # Accept all pending offers
        for offer in pending_offers:
            await job_service.accept_job_offer(offer.id, message_text)
        result["details"]["accepted_offers"] = len(pending_offers)

        # Send confirmation
        response = await ai_service.generate_response(
            context=context_dict.get("last_outbound_message", ""),
            intent="accept_job",
            data={"jobs": pending_jobs}
        )
        await messaging_service.send_to_cleaner(cleaner, response)

    elif interpretation.intent == "reject_job":
        # Reject all pending offers
        for offer in pending_offers:
            await job_service.reject_job_offer(offer.id, message_text)
        result["details"]["rejected_offers"] = len(pending_offers)

        # Send acknowledgment
        response = await ai_service.generate_response(
            context=context_dict.get("last_outbound_message", ""),
            intent="reject_job",
            data={}
        )
        await messaging_service.send_to_cleaner(cleaner, response)

    elif interpretation.intent == "partial_accept":
        # Handle partial acceptance
        accepted_positions = interpretation.accepted_job_ids
        rejected_positions = interpretation.rejected_job_ids

        for offer in pending_offers:
            pos = offer.batch_position
            if pos in accepted_positions:
                await job_service.accept_job_offer(offer.id, message_text)
            elif pos in rejected_positions:
                await job_service.reject_job_offer(offer.id, message_text)

        result["details"]["accepted"] = accepted_positions
        result["details"]["rejected"] = rejected_positions

        # Send response
        response = await ai_service.generate_response(
            context=context_dict.get("last_outbound_message", ""),
            intent="partial_accept",
            data={"accepted": accepted_positions, "rejected": rejected_positions}
        )
        await messaging_service.send_to_cleaner(cleaner, response)

    elif interpretation.intent == "status_update":
        # Update job status
        status_map = {
            "en_route": JobStatus.EN_ROUTE,
            "arrived": JobStatus.IN_PROGRESS,
            "started": JobStatus.IN_PROGRESS,
            "completed": JobStatus.COMPLETED,
            "done": JobStatus.COMPLETED
        }

        new_status = status_map.get(interpretation.status_update)
        if new_status:
            # Find active job for this cleaner
            active_job_result = await db.execute(
                select(Job).where(
                    and_(
                        Job.assigned_cleaner_id == cleaner.id,
                        Job.status.in_([
                            JobStatus.CONFIRMED.value,
                            JobStatus.EN_ROUTE.value,
                            JobStatus.IN_PROGRESS.value
                        ])
                    )
                )
            )
            active_job = active_job_result.scalar_one_or_none()

            if active_job:
                await job_service.update_job_status(
                    active_job.id, new_status, "cleaner", message_text, message.id
                )
                result["details"]["job_id"] = active_job.id
                result["details"]["new_status"] = new_status.value

        # Acknowledge
        response = await ai_service.generate_response(
            context="",
            intent="status_update",
            data={"status": interpretation.status_update}
        )
        await messaging_service.send_to_cleaner(cleaner, response)

    elif interpretation.intent == "question":
        # Answer the question
        response = await ai_service.generate_response(
            context=str(pending_jobs),
            intent="question",
            data={"question_type": interpretation.question_type}
        )
        await messaging_service.send_to_cleaner(cleaner, response)
        result["details"]["question_type"] = interpretation.question_type

    elif interpretation.needs_clarification:
        # Ask for clarification
        await messaging_service.send_to_cleaner(
            cleaner,
            interpretation.clarification_question or "I'm not sure I understood. Could you please clarify?"
        )

    # Update conversation context
    if context:
        context.add_message_to_history({
            "direction": "inbound",
            "content": message_text,
            "timestamp": datetime.utcnow().isoformat(),
            "intent": interpretation.intent
        })

    return result


async def _process_guest_message(
    db: AsyncSession,
    message: Message,
    guest: Guest,
    message_text: str
) -> dict:
    """Process a message from a guest."""
    coordination_service = CoordinationService(db)

    result = await coordination_service.process_guest_message(
        guest_id=guest.id,
        message=message_text
    )

    # Update message with processing info
    message.ai_interpreted = True
    message.ai_intent = result.get("event_type", "unknown")
    message.processing_notes = str(result.get("actions_taken", []))

    return {
        "action_taken": result.get("event_type"),
        "details": {
            "event_id": result.get("event_id"),
            "priority": result.get("priority"),
            "cleaner_notified": result.get("cleaner_notified")
        }
    }
