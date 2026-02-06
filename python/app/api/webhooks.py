"""
Webhook handlers for WhatsApp and Airbnb messages.

Handles incoming messages and processes them through the AI service.
"""

import re
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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


async def _find_active_jobs(db: AsyncSession, cleaner_id: int) -> list[Job]:
    """Find all confirmed/active jobs for a cleaner."""
    result = await db.execute(
        select(Job)
        .options(selectinload(Job.rental_property))
        .where(
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
    return list(result.scalars().all())


def _describe_jobs(jobs: list[Job]) -> str:
    """Build a human-readable list of jobs for disambiguation."""
    parts = []
    for j in jobs:
        name = j.rental_property.short_name if j.rental_property else f"Job #{j.id}"
        date = j.scheduled_date.strftime("%A %b %d") if j.scheduled_date else "TBD"
        parts.append(f"{name} on {date}")
    return ", ".join(parts)


def _match_jobs_from_message(
    message_text: str,
    pending_offers: list,
    pending_jobs: list[dict],
) -> dict:
    """
    Deterministic job matching from the cleaner's message text.

    The AI classifies intent; this function determines WHICH jobs the
    cleaner wants by analyzing the actual words they used — not position
    numbers the cleaner never saw.

    Strategies (tried in order):
      1. Date matching:  "the 26" → Feb 26 job
      2. Ordinal refs:   "the last one" → last job in list
      3. Digit-only msg: "2" → 2nd job (reply to numbered list)
      4. Property name:  "the villa" → matched by name (only if names differ)

    Returns:
        {"accepted": [offer, ...], "rejected": [offer, ...], "matched": bool}
    """
    msg = message_text.lower().strip()
    n = len(pending_offers)

    if n == 0:
        return {"accepted": [], "rejected": [], "matched": False}

    # Extract day-of-month from each job's formatted date string
    offer_days = []
    for job_info in pending_jobs:
        date_str = job_info.get("date", "")
        day_match = re.search(r'\b(\d{1,2})\b', date_str)
        offer_days.append(int(day_match.group(1)) if day_match else None)

    accepted_indices = set()

    # --- Strategy 1: date matching ---
    # Extract numbers from the message and match to job dates
    msg_numbers = [int(x) for x in re.findall(r'\d+', msg)]
    for num in msg_numbers:
        for i, day in enumerate(offer_days):
            if day is not None and day == num:
                accepted_indices.add(i)

    # --- Strategy 2: ordinal references ---
    if not accepted_indices:
        if "last one" in msg or "the last" in msg:
            accepted_indices.add(n - 1)
        elif "first one" in msg or "the first" in msg:
            accepted_indices.add(0)

    # --- Strategy 3: digit-only message (reply to numbered list) ---
    if not accepted_indices and msg.isdigit():
        idx = int(msg) - 1  # 1-based → 0-based
        if 0 <= idx < n:
            accepted_indices.add(idx)

    # --- Strategy 4: property name matching (only if names differ) ---
    if not accepted_indices:
        prop_names = [j.get("property_name", "").lower() for j in pending_jobs]
        if len(set(prop_names)) > 1:
            for i, name in enumerate(prop_names):
                if name and name in msg:
                    accepted_indices.add(i)

    if accepted_indices:
        accepted = [pending_offers[i] for i in sorted(accepted_indices)]
        rejected = [pending_offers[i] for i in range(n) if i not in accepted_indices]
        return {"accepted": accepted, "rejected": rejected, "matched": True}

    return {"accepted": [], "rejected": [], "matched": False}


def _match_by_day_numbers(
    day_numbers: list[int],
    pending_offers: list,
    pending_jobs: list[dict],
) -> dict:
    """
    Match day-of-month numbers (from AI) against pending job dates.

    The AI returns accepted_jobs/rejected_jobs as day-of-month numbers
    (e.g. [26] for Feb 26).  This resolves those to actual offers.
    """
    n = len(pending_offers)
    if not day_numbers or n == 0:
        return {"accepted": [], "rejected": [], "matched": False}

    day_set = set(day_numbers)
    accepted_indices = set()

    for i, job_info in enumerate(pending_jobs):
        date_str = job_info.get("date", "")
        day_match = re.search(r'\b(\d{1,2})\b', date_str)
        if day_match and int(day_match.group(1)) in day_set:
            accepted_indices.add(i)

    if accepted_indices:
        accepted = [pending_offers[i] for i in sorted(accepted_indices)]
        rejected = [pending_offers[i] for i in range(n) if i not in accepted_indices]
        return {"accepted": accepted, "rejected": rejected, "matched": True}

    return {"accepted": [], "rejected": [], "matched": False}


def _build_numbered_list(pending_jobs: list[dict]) -> str:
    """Build a numbered list of jobs for clarification messages."""
    return ", ".join(
        f"{i + 1}) {j.get('property_name', 'Property')} on {j.get('date', 'TBD')}"
        for i, j in enumerate(pending_jobs)
    )


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
        elif len(pending_offers) == 1:
            # Single offer — accept it
            await job_service.accept_job_offer(pending_offers[0].id, message_text)
            result["details"]["accepted_offers"] = 1
            if context:
                context.conversation_state = "idle"
                context.awaiting_response_for = None
        else:
            # Multiple pending — check if conversation shows partial intent
            recent_inbound = [
                m for m in (context_dict.get("recent_messages") or [])
                if m.get("direction") == "inbound"
            ][-4:]
            has_partial_signal = any(
                "only" in m.get("content", "").lower()
                for m in recent_inbound
            )
            if has_partial_signal:
                # Cleaner said "only X" recently — try to match which jobs
                only_msg = next(
                    (m.get("content", "") for m in reversed(recent_inbound)
                     if "only" in m.get("content", "").lower()),
                    ""
                )
                match = _match_jobs_from_message(only_msg, pending_offers, pending_jobs)
                if match["matched"]:
                    for offer in match["accepted"]:
                        await job_service.accept_job_offer(offer.id, message_text)
                    for offer in match["rejected"]:
                        await job_service.reject_job_offer(offer.id, message_text)
                        try:
                            await assignment_service.cascade_to_next_cleaner(offer.job)
                        except Exception as e:
                            logger.error(f"Auto-reassignment failed for job {offer.job_id}: {e}")
                    result["action_taken"] = "partial_accept"
                    result["details"]["accepted_count"] = len(match["accepted"])
                    result["details"]["rejected_count"] = len(match["rejected"])
                    if not response:
                        response = "Noted. Assignments updated."
                else:
                    jobs_desc = _build_numbered_list(pending_jobs)
                    response = f"Which jobs do you want? {jobs_desc}. Reply with the numbers, or 'all'."
                    result["action_taken"] = "needs_clarification"
                    result["details"]["reason"] = "partial_signal_detected"
                    result["details"]["pending_count"] = len(pending_offers)
            else:
                # No partial signals — accept all
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
            # No pending offers — look for active/confirmed jobs to cancel
            active_jobs = await _find_active_jobs(db, cleaner.id)
            if len(active_jobs) == 1:
                await _cancel_active_job(db, active_jobs[0], cleaner, job_service, assignment_service, message, message_text)
                result["action_taken"] = "job_cancelled"
                result["details"]["job_id"] = active_jobs[0].id
            elif len(active_jobs) > 1:
                response = f"You have {len(active_jobs)} active jobs: {_describe_jobs(active_jobs)}. Which one can't you do?"
                result["action_taken"] = "needs_clarification"
                result["details"]["reason"] = "multiple_active_jobs"
                result["details"]["active_job_count"] = len(active_jobs)
            else:
                result["action_taken"] = "no_action"
                result["details"]["reason"] = "no_active_jobs"

    elif intent == "partial_accept":
        # Check if we have pending offers or should look for reclaimable jobs
        if not pending_offers:
            # No pending offers — cleaner might be changing their mind about a rejected job
            # Check if there's a reclaimable job that matches the message
            reclaimable = await _find_reclaimable_job(db, cleaner.id)
            if reclaimable:
                old_offer, job = reclaimable
                # Check if message mentions this job (by property name or date)
                message_lower = message_text.lower()
                property_name = job.rental_property.short_name if job.rental_property else ""
                job_date_day = job.scheduled_date.day if job.scheduled_date else None

                # Simple matching: check if property name or date is in message
                matches_property = property_name and property_name.lower() in message_lower
                matches_date = job_date_day and str(job_date_day) in message_text

                if matches_property or matches_date or "also" in message_lower or "both" in message_lower:
                    # Message indicates they want this job
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
                    # Always provide clear confirmation for reclaimed jobs
                    response = f"Great! I've booked you for the {property_name or 'job'} on {job.scheduled_date.strftime('%A %b %d') if job.scheduled_date else 'TBD'}."
                else:
                    # Message doesn't clearly match — ask for confirmation
                    result["action_taken"] = "no_action"
                    result["details"]["reason"] = "unclear_reclaim_intent"
            else:
                # No pending or reclaimable jobs
                result["action_taken"] = "no_action"
                result["details"]["reason"] = "no_jobs_to_accept"
                response = "There are no open job offers right now. We'll reach out when something is available."
        else:
            # Has pending offers — normal partial accept flow
            # Layer 1: deterministic matching from the cleaner's actual message
            match = _match_jobs_from_message(message_text, pending_offers, pending_jobs)

            # Layer 2: AI's resolved day-of-month numbers (handles "next week", etc.)
            if not match["matched"] and interpretation.accepted_job_ids:
                match = _match_by_day_numbers(
                    interpretation.accepted_job_ids, pending_offers, pending_jobs
                )

            if match["matched"]:
                for offer in match["accepted"]:
                    await job_service.accept_job_offer(offer.id, message_text)
                for offer in match["rejected"]:
                    await job_service.reject_job_offer(offer.id, message_text)
                    try:
                        await assignment_service.cascade_to_next_cleaner(offer.job)
                    except Exception as e:
                        logger.error(f"Auto-reassignment failed for job {offer.job_id}: {e}")
                result["details"]["accepted_count"] = len(match["accepted"])
                result["details"]["rejected_count"] = len(match["rejected"])
                if not response:
                    response = "Noted. Assignments updated."
            else:
                # Neither matcher could resolve — ask for clarification
                jobs_desc = _build_numbered_list(pending_jobs)
                response = f"Which job do you want? {jobs_desc}. Reply with the number."
                result["action_taken"] = "needs_clarification"

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
            active_jobs = await _find_active_jobs(db, cleaner.id)
            if len(active_jobs) == 1:
                await job_service.update_job_status(
                    active_jobs[0].id, new_status, "cleaner", message_text, message.id
                )
                result["details"]["job_id"] = active_jobs[0].id
                result["details"]["new_status"] = new_status.value
            elif len(active_jobs) > 1:
                response = f"You have {len(active_jobs)} active jobs: {_describe_jobs(active_jobs)}. Which one are you updating?"
                result["action_taken"] = "needs_clarification"
                result["details"]["reason"] = "multiple_active_jobs"
                result["details"]["active_job_count"] = len(active_jobs)

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
