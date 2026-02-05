"""
Scheduler Service for batch job delivery and reminders.

Handles:
- Batch evening job delivery (6 PM)
- Progressive reminders for non-responders
- Offer expiration checks
- Cascading to backup cleaners
"""

import logging
import uuid
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.job import Job, JobOffer, JobStatus, JobUrgency
from app.models.cleaner import Cleaner
from app.models.property import Property
from app.models.message import ConversationContext
from app.config import get_settings
from app.services.job_service import JobService
from app.services.assignment_service import AssignmentService

logger = logging.getLogger(__name__)


class SchedulerService:
    """Service for scheduled batch operations and reminders."""

    def __init__(self, db: AsyncSession):
        """Initialize with dependencies."""
        from app.dependencies import get_ai_service, get_messaging_service
        self.db = db
        self.settings = get_settings()
        self.messaging = get_messaging_service()
        self.ai = get_ai_service()
        self.job_service = JobService(db)

    async def run_batch_delivery(self) -> Dict[str, Any]:
        """
        Run the evening batch delivery of job offers.

        Groups pending jobs by best cleaner and sends batch messages.

        Returns:
            Summary of batch delivery results
        """
        logger.info("Starting batch job delivery")

        # Get all pending non-urgent jobs
        pending_jobs = await self.job_service.get_pending_jobs_for_batch()

        if not pending_jobs:
            logger.info("No pending jobs for batch delivery")
            return {"jobs_processed": 0, "cleaners_contacted": 0}

        # Generate unique batch ID for this run
        batch_id = f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # Group jobs by property city for assignment
        assignment_service = AssignmentService(self.db)

        # Get best cleaner for each job and group by cleaner
        cleaner_jobs: Dict[int, List[Job]] = defaultdict(list)
        job_rankings: Dict[int, Any] = {}

        for job in pending_jobs:
            rankings = await assignment_service.rank_cleaners_for_job(job)
            if rankings:
                best_cleaner_id = rankings[0].cleaner_id
                cleaner_jobs[best_cleaner_id].append(job)
                job_rankings[job.id] = rankings[0]
            else:
                logger.warning(f"No cleaner available for job {job.id}")

        # Send batch messages to each cleaner
        results = {
            "batch_id": batch_id,
            "jobs_processed": len(pending_jobs),
            "cleaners_contacted": 0,
            "messages_sent": 0,
            "failures": []
        }

        for cleaner_id, jobs in cleaner_jobs.items():
            try:
                send_result = await self._send_batch_to_cleaner(
                    cleaner_id=cleaner_id,
                    jobs=jobs,
                    batch_id=batch_id
                )
                if send_result["success"]:
                    results["cleaners_contacted"] += 1
                    results["messages_sent"] += 1
                else:
                    results["failures"].append({
                        "cleaner_id": cleaner_id,
                        "error": send_result.get("error")
                    })
            except Exception as e:
                logger.error(f"Error sending batch to cleaner {cleaner_id}: {e}")
                results["failures"].append({
                    "cleaner_id": cleaner_id,
                    "error": str(e)
                })

        logger.info(f"Batch delivery complete: {results}")
        return results

    async def _send_batch_to_cleaner(
        self,
        cleaner_id: int,
        jobs: List[Job],
        batch_id: str
    ) -> Dict[str, Any]:
        """
        Send a batch of job offers to a cleaner.

        Args:
            cleaner_id: Cleaner to send to
            jobs: Jobs to offer
            batch_id: Batch identifier

        Returns:
            Send result
        """
        # Get cleaner
        cleaner_result = await self.db.execute(
            select(Cleaner).where(Cleaner.id == cleaner_id)
        )
        cleaner = cleaner_result.scalar_one_or_none()

        if not cleaner:
            return {"success": False, "error": "Cleaner not found"}

        # Get properties for job info
        property_ids = [job.property_id for job in jobs]
        property_result = await self.db.execute(
            select(Property).where(Property.id.in_(property_ids))
        )
        properties = {p.id: p for p in property_result.scalars().all()}

        # Format job info
        job_infos = []
        for i, job in enumerate(jobs):
            prop = properties.get(job.property_id)
            job_infos.append({
                "property_name": prop.short_name if prop else f"Property #{job.property_id}",
                "date": job.scheduled_date.strftime("%a %b %d"),
                "time": job.scheduled_time or "TBD",
                "amount": job.payment_amount or 0
            })

            # Create job offer
            offer = await self.job_service.create_job_offer(
                job_id=job.id,
                cleaner_id=cleaner_id,
                expires_hours=self.settings.default_response_timeout_hours,
                batch_id=batch_id,
                batch_position=i + 1
            )

            # Update job batch info
            job.batch_id = batch_id
            job.status = JobStatus.BATCHED.value

        # Generate batch message
        message = await self.ai.generate_job_offer_message(
            jobs=job_infos,
            cleaner_name=cleaner.name.split()[0],
            is_batch=True
        )

        # Send message
        send_result = await self.messaging.send_job_offer(
            cleaner=cleaner,
            job_message=message,
            job_ids=[j.id for j in jobs]
        )

        # Update conversation context
        await self._update_conversation_context(
            cleaner=cleaner,
            jobs=jobs,
            batch_id=batch_id,
            message=message
        )

        return send_result

    async def _update_conversation_context(
        self,
        cleaner: Cleaner,
        jobs: List[Job],
        batch_id: str,
        message: str
    ):
        """Update conversation context after sending batch."""
        chat_id = cleaner.whatsapp_chat_id or f"phone_{cleaner.phone}"

        result = await self.db.execute(
            select(ConversationContext).where(
                ConversationContext.external_chat_id == chat_id
            )
        )
        context = result.scalar_one_or_none()

        if not context:
            context = ConversationContext(
                external_chat_id=chat_id,
                channel="whatsapp",
                participant_type="cleaner",
                cleaner_id=cleaner.id
            )
            self.db.add(context)

        # Update context
        context.active_batch_id = batch_id
        context.batch_jobs = [j.id for j in jobs]
        context.last_outbound_message = message
        context.last_outbound_at = datetime.now(timezone.utc)
        context.awaiting_response_for = "job_confirmation"
        context.conversation_state = "awaiting_job_response"

        # Get pending offers
        offer_result = await self.db.execute(
            select(JobOffer.id).where(
                and_(
                    JobOffer.cleaner_id == cleaner.id,
                    JobOffer.status == "pending"
                )
            )
        )
        context.active_job_offers = [r[0] for r in offer_result.all()]

    async def run_reminder_check(self) -> Dict[str, Any]:
        """
        Check for offers needing reminders and send them.

        Reminders are sent:
        - After 12 hours for normal jobs
        - After 2 hours for urgent jobs

        Returns:
            Summary of reminders sent
        """
        logger.info("Running reminder check")

        results = {
            "reminders_sent": 0,
            "offers_checked": 0,
            "failures": []
        }

        # Get pending offers
        pending_result = await self.db.execute(
            select(JobOffer)
            .options(
                selectinload(JobOffer.job).selectinload(Job.rental_property),
                selectinload(JobOffer.cleaner)
            )
            .where(JobOffer.status == "pending")
        )
        pending_offers = pending_result.scalars().all()

        now = datetime.now(timezone.utc)

        for offer in pending_offers:
            results["offers_checked"] += 1

            # Determine reminder threshold
            is_urgent = offer.job.urgency in [JobUrgency.URGENT.value, JobUrgency.SAME_DAY.value]
            reminder_threshold_hours = (
                self.settings.reminder_interval_hours if is_urgent
                else self.settings.reminder_interval_hours * 6  # 12 hours for normal
            )

            # Calculate time since last contact
            last_contact = offer.last_reminder_at or offer.offered_at
            hours_since_contact = (now - last_contact).total_seconds() / 3600

            if hours_since_contact >= reminder_threshold_hours:
                # Send reminder
                try:
                    await self._send_reminder(offer)
                    offer.reminder_count += 1
                    offer.last_reminder_at = now
                    results["reminders_sent"] += 1
                except Exception as e:
                    logger.error(f"Error sending reminder for offer {offer.id}: {e}")
                    results["failures"].append({
                        "offer_id": offer.id,
                        "error": str(e)
                    })

        logger.info(f"Reminder check complete: {results}")
        return results

    async def _send_reminder(self, offer: JobOffer):
        """Send reminder for a pending offer."""
        await self.messaging.send_reminder(
            cleaner=offer.cleaner,
            jobs=[offer.job],
            reminder_number=offer.reminder_count + 1
        )

    async def run_expiration_check(self) -> Dict[str, Any]:
        """
        Check for expired offers and cascade to next cleaner.

        Returns:
            Summary of expirations processed
        """
        logger.info("Running expiration check")

        # Expire old offers
        expired_count = await self.job_service.expire_old_offers()

        results = {
            "offers_expired": expired_count,
            "jobs_cascaded": 0,
            "jobs_escalated": 0
        }

        # Get jobs that need re-assignment
        result = await self.db.execute(
            select(Job)
            .options(selectinload(Job.rental_property))
            .where(Job.status == JobStatus.PENDING.value)
        )
        pending_jobs = result.scalars().all()

        assignment_service = AssignmentService(
            self.db, self.messaging, self.ai
        )

        for job in pending_jobs:
            # Check if this job had an expired offer
            offer_result = await self.db.execute(
                select(JobOffer).where(
                    and_(
                        JobOffer.job_id == job.id,
                        JobOffer.status == "expired"
                    )
                ).order_by(JobOffer.offered_at.desc())
            )
            latest_expired = offer_result.scalars().first()

            if latest_expired:
                # Cascade to next cleaner
                cascade_result = await assignment_service.cascade_to_next_cleaner(job)
                if cascade_result:
                    results["jobs_cascaded"] += 1
                else:
                    results["jobs_escalated"] += 1

        logger.info(f"Expiration check complete: {results}")
        return results

    async def process_urgent_jobs(self) -> Dict[str, Any]:
        """
        Process urgent/same-day jobs immediately.

        Called when urgent jobs are created to skip batching.

        Returns:
            Processing results
        """
        logger.info("Processing urgent jobs")

        urgent_jobs = await self.job_service.get_urgent_pending_jobs()

        results = {
            "jobs_found": len(urgent_jobs),
            "jobs_assigned": 0,
            "failures": []
        }

        assignment_service = AssignmentService(
            self.db, self.messaging, self.ai
        )

        for job in urgent_jobs:
            try:
                assign_result = await assignment_service.assign_job_to_best_cleaner(job)
                if assign_result:
                    results["jobs_assigned"] += 1
                else:
                    results["failures"].append({
                        "job_id": job.id,
                        "error": "No cleaner available"
                    })
            except Exception as e:
                logger.error(f"Error assigning urgent job {job.id}: {e}")
                results["failures"].append({
                    "job_id": job.id,
                    "error": str(e)
                })

        logger.info(f"Urgent job processing complete: {results}")
        return results
