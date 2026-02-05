"""
Job Service for managing cleaning jobs.

Handles job creation, status updates, and job-related queries.
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.job import Job, JobOffer, JobStatusHistory, JobStatus, JobUrgency
from app.models.property import Property
from app.models.cleaner import Cleaner
from app.schemas.job import JobCreate, JobUpdate

logger = logging.getLogger(__name__)


class JobService:
    """Service for managing cleaning jobs."""

    def __init__(self, db: AsyncSession):
        """Initialize with database session."""
        self.db = db

    async def create_job(self, job_data: JobCreate) -> Job:
        """
        Create a new cleaning job.

        Args:
            job_data: Job creation data

        Returns:
            Created job instance
        """
        # Get property for default values
        property_result = await self.db.execute(
            select(Property).where(Property.id == job_data.property_id)
        )
        property_obj = property_result.scalar_one_or_none()

        if not property_obj:
            raise ValueError(f"Property {job_data.property_id} not found")

        # Set default payment if not provided
        payment_amount = job_data.payment_amount
        if payment_amount is None:
            payment_amount = property_obj.standard_cleaning_rate

        # Determine urgency based on date
        urgency = job_data.urgency
        if job_data.scheduled_date.date() == datetime.utcnow().date():
            urgency = JobUrgency.SAME_DAY.value

        job = Job(
            property_id=job_data.property_id,
            job_type=job_data.job_type,
            urgency=urgency,
            scheduled_date=job_data.scheduled_date,
            scheduled_time=job_data.scheduled_time,
            estimated_duration_minutes=job_data.estimated_duration_minutes or property_obj.estimated_cleaning_duration_minutes,
            deadline=job_data.deadline,
            payment_amount=payment_amount,
            guest_id=job_data.guest_id,
            previous_guest_checkout=job_data.previous_guest_checkout,
            next_guest_checkin=job_data.next_guest_checkin,
            notes=job_data.notes
        )

        self.db.add(job)
        await self.db.flush()

        # Create status history entry
        await self._create_status_history(job.id, None, JobStatus.PENDING.value, "system", "Job created")

        logger.info(f"Created job {job.id} for property {job_data.property_id}")
        return job

    async def get_job(self, job_id: int) -> Optional[Job]:
        """Get a job by ID."""
        result = await self.db.execute(
            select(Job)
            .options(selectinload(Job.rental_property), selectinload(Job.assigned_cleaner))
            .where(Job.id == job_id)
        )
        return result.scalar_one_or_none()

    async def get_jobs(
        self,
        status: Optional[str] = None,
        property_id: Optional[int] = None,
        cleaner_id: Optional[int] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        urgency: Optional[str] = None,
        page: int = 1,
        page_size: int = 50
    ) -> tuple[List[Job], int]:
        """
        Get jobs with filters.

        Returns:
            Tuple of (jobs list, total count)
        """
        query = select(Job).options(
            selectinload(Job.rental_property),
            selectinload(Job.assigned_cleaner)
        )

        conditions = []
        if status:
            conditions.append(Job.status == status)
        if property_id:
            conditions.append(Job.property_id == property_id)
        if cleaner_id:
            conditions.append(Job.assigned_cleaner_id == cleaner_id)
        if date_from:
            conditions.append(Job.scheduled_date >= date_from)
        if date_to:
            conditions.append(Job.scheduled_date <= date_to)
        if urgency:
            conditions.append(Job.urgency == urgency)

        if conditions:
            query = query.where(and_(*conditions))

        # Get total count
        count_result = await self.db.execute(
            select(Job.id).where(and_(*conditions)) if conditions else select(Job.id)
        )
        total = len(count_result.all())

        # Get paginated results
        query = query.order_by(Job.scheduled_date.asc())
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await self.db.execute(query)
        jobs = result.scalars().all()

        return list(jobs), total

    async def get_pending_jobs_for_batch(self) -> List[Job]:
        """
        Get jobs that are pending and should be batched for delivery.

        Returns jobs that are:
        - Status: PENDING
        - Urgency: NORMAL (not same-day or urgent)
        - Scheduled for future dates
        """
        result = await self.db.execute(
            select(Job)
            .options(selectinload(Job.rental_property))
            .where(
                and_(
                    Job.status == JobStatus.PENDING.value,
                    Job.urgency == JobUrgency.NORMAL.value,
                    Job.scheduled_date > datetime.utcnow()
                )
            )
            .order_by(Job.scheduled_date.asc())
        )
        return list(result.scalars().all())

    async def get_urgent_pending_jobs(self) -> List[Job]:
        """Get urgent/same-day jobs that need immediate assignment."""
        result = await self.db.execute(
            select(Job)
            .options(selectinload(Job.rental_property))
            .where(
                and_(
                    Job.status == JobStatus.PENDING.value,
                    or_(
                        Job.urgency == JobUrgency.URGENT.value,
                        Job.urgency == JobUrgency.SAME_DAY.value
                    )
                )
            )
            .order_by(Job.scheduled_date.asc())
        )
        return list(result.scalars().all())

    async def update_job_status(
        self,
        job_id: int,
        new_status: JobStatus,
        changed_by: str = "system",
        notes: Optional[str] = None,
        message_id: Optional[int] = None
    ) -> Job:
        """
        Update job status with history tracking.

        Args:
            job_id: Job ID
            new_status: New status
            changed_by: Who changed it (system, cleaner, manager)
            notes: Optional notes
            message_id: Optional triggering message ID

        Returns:
            Updated job
        """
        job = await self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        old_status = job.status
        job.status = new_status.value

        # Update timestamps based on status
        if new_status == JobStatus.IN_PROGRESS:
            job.started_at = datetime.utcnow()
        elif new_status == JobStatus.COMPLETED:
            job.completed_at = datetime.utcnow()
            if job.started_at:
                duration = (job.completed_at - job.started_at).total_seconds() / 60
                job.actual_duration_minutes = int(duration)

        # Create history entry
        await self._create_status_history(
            job_id, old_status, new_status.value, changed_by, notes, message_id
        )

        logger.info(f"Job {job_id} status changed: {old_status} -> {new_status.value}")
        return job

    async def assign_cleaner(
        self,
        job_id: int,
        cleaner_id: int,
        payment_amount: Optional[float] = None
    ) -> Job:
        """
        Assign a cleaner to a job.

        Args:
            job_id: Job ID
            cleaner_id: Cleaner ID
            payment_amount: Optional override payment

        Returns:
            Updated job
        """
        job = await self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        job.assigned_cleaner_id = cleaner_id
        if payment_amount:
            job.payment_amount = payment_amount

        await self._create_status_history(
            job_id, job.status, job.status, "manager",
            f"Assigned to cleaner {cleaner_id}"
        )

        logger.info(f"Job {job_id} assigned to cleaner {cleaner_id}")
        return job

    async def create_job_offer(
        self,
        job_id: int,
        cleaner_id: int,
        expires_hours: int = 24,
        batch_id: Optional[str] = None,
        batch_position: Optional[int] = None
    ) -> JobOffer:
        """
        Create a job offer for a cleaner.

        Args:
            job_id: Job ID
            cleaner_id: Cleaner ID to offer to
            expires_hours: Hours until offer expires
            batch_id: Optional batch ID if part of batch
            batch_position: Position in batch

        Returns:
            Created job offer
        """
        job = await self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        offer = JobOffer(
            job_id=job_id,
            cleaner_id=cleaner_id,
            offered_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=expires_hours),
            offered_amount=job.payment_amount,
            batch_id=batch_id,
            batch_position=batch_position
        )

        self.db.add(offer)

        # Update job status
        job.status = JobStatus.OFFERED.value
        job.assignment_attempts += 1

        # Update cleaner stats
        cleaner_result = await self.db.execute(
            select(Cleaner).where(Cleaner.id == cleaner_id)
        )
        cleaner = cleaner_result.scalar_one_or_none()
        if cleaner:
            cleaner.total_jobs_offered += 1

        await self.db.flush()
        logger.info(f"Created offer for job {job_id} to cleaner {cleaner_id}")
        return offer

    async def get_pending_offers_for_cleaner(self, cleaner_id: int) -> List[JobOffer]:
        """Get all pending job offers for a cleaner."""
        result = await self.db.execute(
            select(JobOffer)
            .options(selectinload(JobOffer.job).selectinload(Job.rental_property))
            .where(
                and_(
                    JobOffer.cleaner_id == cleaner_id,
                    JobOffer.status == "pending"
                )
            )
            .order_by(JobOffer.offered_at.desc())
        )
        return list(result.scalars().all())

    async def accept_job_offer(
        self,
        offer_id: int,
        response_message: Optional[str] = None
    ) -> JobOffer:
        """
        Accept a job offer.

        Args:
            offer_id: Offer ID
            response_message: Original response message

        Returns:
            Updated offer
        """
        result = await self.db.execute(
            select(JobOffer)
            .options(selectinload(JobOffer.job), selectinload(JobOffer.cleaner))
            .where(JobOffer.id == offer_id)
        )
        offer = result.scalar_one_or_none()

        if not offer:
            raise ValueError(f"Offer {offer_id} not found")

        offer.status = "accepted"
        offer.responded_at = datetime.utcnow()
        offer.response_message = response_message

        # Update job
        offer.job.assigned_cleaner_id = offer.cleaner_id
        offer.job.status = JobStatus.CONFIRMED.value

        # Update cleaner stats
        offer.cleaner.jobs_accepted += 1

        await self._create_status_history(
            offer.job_id, JobStatus.OFFERED.value, JobStatus.CONFIRMED.value,
            "cleaner", f"Accepted by cleaner {offer.cleaner_id}"
        )

        logger.info(f"Offer {offer_id} accepted for job {offer.job_id}")
        return offer

    async def reject_job_offer(
        self,
        offer_id: int,
        response_message: Optional[str] = None
    ) -> JobOffer:
        """
        Reject a job offer.

        Args:
            offer_id: Offer ID
            response_message: Original response message

        Returns:
            Updated offer
        """
        result = await self.db.execute(
            select(JobOffer)
            .options(selectinload(JobOffer.job), selectinload(JobOffer.cleaner))
            .where(JobOffer.id == offer_id)
        )
        offer = result.scalar_one_or_none()

        if not offer:
            raise ValueError(f"Offer {offer_id} not found")

        offer.status = "rejected"
        offer.responded_at = datetime.utcnow()
        offer.response_message = response_message

        # Update cleaner stats
        offer.cleaner.jobs_rejected += 1

        # Reset job status for re-assignment
        offer.job.status = JobStatus.PENDING.value

        logger.info(f"Offer {offer_id} rejected for job {offer.job_id}")
        return offer

    async def expire_old_offers(self) -> int:
        """
        Expire offers that have passed their expiration time.

        Returns:
            Number of offers expired
        """
        result = await self.db.execute(
            select(JobOffer)
            .options(selectinload(JobOffer.cleaner))
            .where(
                and_(
                    JobOffer.status == "pending",
                    JobOffer.expires_at < datetime.utcnow()
                )
            )
        )
        offers = result.scalars().all()

        count = 0
        for offer in offers:
            offer.status = "expired"
            offer.cleaner.jobs_no_response += 1
            count += 1

        logger.info(f"Expired {count} job offers")
        return count

    async def _create_status_history(
        self,
        job_id: int,
        from_status: Optional[str],
        to_status: str,
        changed_by: str,
        notes: Optional[str] = None,
        message_id: Optional[int] = None
    ):
        """Create job status history entry."""
        history = JobStatusHistory(
            job_id=job_id,
            from_status=from_status,
            to_status=to_status,
            changed_by=changed_by,
            notes=notes,
            trigger_message_id=message_id
        )
        self.db.add(history)
