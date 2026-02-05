"""
Webhook handlers for WhatsApp and Airbnb messages.

Handles incoming messages and processes them through the AI service.
"""

import logging
from datetime import datetime, timezone
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
from app.dependencies import get_ai_service, get_messaging_service
from app.services.job_service import JobService
from app.services.assignment_service import AssignmentService
from app.services.coordination_service import CoordinationService
from app.security import validate_webhook_request, rate_limit, sanitize_message_for_ai

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/webhook",
    tags=["webhooks"],
    dependencies=[Depends(validate_webhook_request), Depends(rate_limit)],
)


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

    # Deduplicate: Green API retries webhooks if response is slow
    if payload.idMessage:
        existing = await db.execute(
            select(Message.id).where(Message.external_message_id == payload.idMessage)
        )
        if existing.scalar_one_or_none():
            logger.info(f"Duplicate webhook ignored: {payload.idMessage}")
            return WebhookResponse(success=True, message="Duplicate webhook ignored")

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
        sent_at=datetime.now(timezone.utc)
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
    message.processed_at = datetime.now(timezone.utc)

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
        sent_at=payload.created_at or datetime.now(timezone.utc)
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
    message.processed_at = datetime.now(timezone.utc)

    return WebhookResponse(
        success=True,
        message="Message processed",
        message_id=message.id,
        action_taken=result.get("action_taken"),
        details=result.get("details", {})
    )


def _normalize_phone(phone: str) -> str:
    """Normalize phone number by stripping leading + and non-digit chars."""
    return phone.lstrip("+").strip()


async def _identify_sender(
    db: AsyncSession,
    phone: str,
    chat_id: str
) -> tuple[str, Optional[Cleaner], Optional[Guest]]:
    """Identify sender by phone number or chat ID."""
    normalized = _normalize_phone(phone)

    # Try to find cleaner
    # First try by chat ID, then by exact phone match (with and without +)
    cleaner_result = await db.execute(
        select(Cleaner).where(
            (Cleaner.whatsapp_chat_id == chat_id) |
            (Cleaner.phone == phone) |
            (Cleaner.phone == f"+{normalized}") |
            (Cleaner.phone == normalized)
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
            (Guest.phone == phone) |
            (Guest.phone == f"+{normalized}") |
            (Guest.phone == normalized)
        )
    )
    guest = guest_result.scalar_one_or_none()

    if guest:
        if not guest.whatsapp_chat_id:
            guest.whatsapp_chat_id = chat_id
        return SenderType.GUEST.value, None, guest

    return "unknown", None, None


async def _find_active_job(db: AsyncSession, cleaner_id: int) -> Optional[Job]:
    """Find a confirmed/active job for a cleaner."""
    result = await db.execute(
        select(Job).where(
            and_(
                Job.assigned_cleaner_id == cleaner_id,
                Job.status.in_([
                    JobStatus.CONFIRMED.value,
                    JobStatus.EN_ROUTE.value,
                    JobStatus.IN_PROGRESS.value,
                ])
            )
        )
    )
    return result.scalar_one_or_none()


async def _cancel_active_job(
    db: AsyncSession, job: Job, cleaner: Cleaner,
    job_service: JobService, assignment_service: AssignmentService,
    message: Message, message_text: str
):
    """Cancel an active job: mark offer cancelled, reset job, cascade."""
    accepted_offer_result = await db.execute(
        select(JobOffer).where(
            and_(
                JobOffer.job_id == job.id,
                JobOffer.cleaner_id == cleaner.id,
                JobOffer.status == "accepted"
            )
        )
    )
    accepted_offer = accepted_offer_result.scalar_one_or_none()
    if accepted_offer:
        accepted_offer.status = "cancelled"

    await job_service.update_job_status(
        job.id, JobStatus.CANCELLED, "cleaner", message_text, message.id
    )
    job.status = JobStatus.PENDING.value
    job.assigned_cleaner_id = None
    try:
        cascade_result = await assignment_service.cascade_to_next_cleaner(job)
        if cascade_result:
            logger.info(f"Cancelled job {job.id} reassigned to cleaner {cascade_result.get('cleaner_id')}")
    except Exception as e:
        logger.error(f"Auto-reassignment failed for cancelled job {job.id}: {e}")


async def _find_reclaimable_job(
    db: AsyncSession, cleaner_id: int
) -> Optional[tuple]:
    """
    Find a job this cleaner recently rejected that is still unassigned.

    Only returns jobs in PENDING or ESCALATED status — meaning no other
    cleaner currently has a pending offer or has confirmed the job.
    """
    result = await db.execute(
        select(JobOffer, Job)
        .join(Job, JobOffer.job_id == Job.id)
        .where(
            and_(
                JobOffer.cleaner_id == cleaner_id,
                JobOffer.status == "rejected",
                Job.status.in_([JobStatus.PENDING.value, JobStatus.ESCALATED.value]),
            )
        )
        .order_by(JobOffer.responded_at.desc())
        .limit(1)
    )
    row = result.first()
    if row:
        return row[0], row[1]
    return None


async def _process_cleaner_message(
    db: AsyncSession,
    message: Message,
    cleaner: Cleaner,
    message_text: str,
    chat_id: str
) -> dict:
    """Process a message from a cleaner."""
    ai_service = get_ai_service()
    messaging_service = get_messaging_service()
    job_service = JobService(db)
    assignment_service = AssignmentService(db)

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
            "property_name": job.rental_property.short_name if job.rental_property else f"Property #{job.property_id}",
            "date": job.scheduled_date.strftime("%A %b %d") if job.scheduled_date else "TBD",
            "time": job.scheduled_time or "TBD",
            "batch_position": offer.batch_position
        })

    # Sanitize message before AI interpretation
    sanitized_text = sanitize_message_for_ai(message_text)

    # Interpret the message
    interpretation = await ai_service.interpret_cleaner_message(
        message=sanitized_text,
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

    # --- Act on intent ---
    # AI classified what the cleaner wants from the conversation.
    # Now the handler checks actual job state and decides what to do.
    result = {"action_taken": interpretation.intent, "details": {}}
    response = interpretation.suggested_response or ""
    intent = interpretation.intent

    if intent == "acknowledgment":
        result["action_taken"] = "acknowledgment"
        response = ""

    elif intent == "accept_job":
        if not pending_offers:
            # No pending offers — check if cleaner recently rejected a job
            # that's still unassigned (not offered to or confirmed by anyone else)
            reclaimable = await _find_reclaimable_job(db, cleaner.id)
            if reclaimable:
                old_offer, job = reclaimable
                old_offer.status = "accepted"
                old_offer.responded_at = datetime.now(timezone.utc)
                old_offer.response_message = message_text
                job.status = JobStatus.CONFIRMED.value
                job.assigned_cleaner_id = cleaner.id
                result["action_taken"] = "accept_job"
                result["details"]["accepted_offers"] = 1
                result["details"]["reclaimed"] = True
                result["details"]["job_id"] = job.id
                if context:
                    context.conversation_state = "idle"
                    context.awaiting_response_for = None
            else:
                result["action_taken"] = "no_action"
                result["details"]["reason"] = "no_pending_offers"
                response = "There are no open job offers right now. We'll reach out when something is available."
        elif len(pending_offers) > 1 and not (
            context and context.conversation_state == "awaiting_multi_job_confirmation"
        ):
            jobs_desc = ", ".join(
                f"{j.get('property_name', 'Property')} on {j.get('date', 'TBD')} at {j.get('time', 'TBD')}"
                for j in pending_jobs
            )
            response = await ai_service.generate_conversational_message(
                "multi_job_confirm",
                {
                    "cleaner_name": cleaner.name.split()[0] if cleaner.name else "there",
                    "job_count": len(pending_offers),
                    "jobs_description": jobs_desc,
                }
            )
            if context:
                context.conversation_state = "awaiting_multi_job_confirmation"
                context.awaiting_response_for = "multi_job_confirmation"
            result["action_taken"] = "awaiting_multi_job_confirmation"
            result["details"]["pending_count"] = len(pending_offers)
        else:
            for offer in pending_offers:
                await job_service.accept_job_offer(offer.id, message_text)
            result["details"]["accepted_offers"] = len(pending_offers)
            if context:
                context.conversation_state = "idle"
                context.awaiting_response_for = None

    elif intent == "reject_job":
        if pending_offers:
            for offer in pending_offers:
                await job_service.reject_job_offer(offer.id, message_text)
                try:
                    cascade_result = await assignment_service.cascade_to_next_cleaner(offer.job)
                    if cascade_result:
                        logger.info(f"Job {offer.job.id} reassigned to cleaner {cascade_result.get('cleaner_id')}")
                except Exception as e:
                    logger.error(f"Auto-reassignment failed for job {offer.job_id}: {e}")
            result["details"]["rejected_offers"] = len(pending_offers)
        else:
            # No pending offers — look for an active/confirmed job to cancel
            active_job = await _find_active_job(db, cleaner.id)
            if active_job:
                await _cancel_active_job(db, active_job, cleaner, job_service, assignment_service, message, message_text)
                result["action_taken"] = "job_cancelled"
                result["details"]["job_id"] = active_job.id
            else:
                result["action_taken"] = "no_action"
                result["details"]["reason"] = "no_active_jobs"

    elif intent == "partial_accept":
        accepted_positions = interpretation.accepted_job_ids
        rejected_positions = interpretation.rejected_job_ids

        for offer in pending_offers:
            pos = offer.batch_position
            if pos in accepted_positions:
                await job_service.accept_job_offer(offer.id, message_text)
            elif pos in rejected_positions:
                await job_service.reject_job_offer(offer.id, message_text)
                # Auto-reassign rejected jobs from partial accept
                try:
                    await assignment_service.cascade_to_next_cleaner(offer.job)
                except Exception as e:
                    logger.error(f"Auto-reassignment failed for job {offer.job_id}: {e}")

        result["details"]["accepted"] = accepted_positions
        result["details"]["rejected"] = rejected_positions

    elif intent == "status_update":
        status_map = {
            "en_route": JobStatus.EN_ROUTE,
            "arrived": JobStatus.IN_PROGRESS,
            "started": JobStatus.IN_PROGRESS,
            "completed": JobStatus.COMPLETED,
            "done": JobStatus.COMPLETED
        }
        new_status = status_map.get(interpretation.status_update)
        if new_status:
            active_job = await _find_active_job(db, cleaner.id)
            if active_job:
                await job_service.update_job_status(
                    active_job.id, new_status, "cleaner", message_text, message.id
                )
                result["details"]["job_id"] = active_job.id
                result["details"]["new_status"] = new_status.value

    elif intent == "question":
        result["details"]["question_type"] = interpretation.question_type

    elif interpretation.needs_clarification:
        if not response:
            response = interpretation.clarification_question or "Can you clarify? Are you able to take the job?"

    # Send the AI's response (if any)
    if response:
        await messaging_service.send_to_cleaner(cleaner, response)
        result["_outbound_message"] = response

    # Update conversation context
    if context:
        context.add_message_to_history({
            "direction": "inbound",
            "content": message_text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "intent": interpretation.intent
        })
        # Track outbound response so AI has full conversation next time
        if result.get("_outbound_message"):
            context.last_outbound_message = result["_outbound_message"]
            context.last_outbound_at = datetime.now(timezone.utc)
            context.add_message_to_history({
                "direction": "outbound",
                "content": result["_outbound_message"],
                "timestamp": datetime.now(timezone.utc).isoformat()
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

    # Sanitize message before AI interpretation
    sanitized_text = sanitize_message_for_ai(message_text)

    result = await coordination_service.process_guest_message(
        guest_id=guest.id,
        message=sanitized_text
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
