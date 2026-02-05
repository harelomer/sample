"""
Assignment Service for ranking cleaners and assigning jobs.

Implements the cleaner ranking algorithm based on:
- Property familiarity
- Availability
- Response rate
- Rating
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.cleaner import Cleaner, CleanerPropertyFamiliarity
from app.models.job import Job, JobOffer, JobStatus
from app.models.property import Property
from app.config import get_settings
from app.schemas.cleaner import CleanerRanking
from app.services.job_service import JobService

logger = logging.getLogger(__name__)


class AssignmentService:
    """Service for cleaner ranking and job assignment."""

    def __init__(self, db: AsyncSession):
        """Initialize with dependencies."""
        from app.dependencies import get_ai_service, get_messaging_service
        self.db = db
        self.settings = get_settings()
        self.messaging = get_messaging_service()
        self.ai = get_ai_service()
        self.job_service = JobService(db)

    async def rank_cleaners_for_job(
        self,
        job: Job,
        exclude_cleaner_ids: Optional[List[int]] = None
    ) -> List[CleanerRanking]:
        """
        Rank available cleaners for a specific job.

        Args:
            job: The job to assign
            exclude_cleaner_ids: Cleaner IDs to exclude (already rejected, etc.)

        Returns:
            List of CleanerRanking sorted by score (highest first)
        """
        exclude_ids = exclude_cleaner_ids or []

        # Get all active, available cleaners
        query = select(Cleaner).where(
            and_(
                Cleaner.is_active == True,
                Cleaner.is_available == True
            )
        )

        if exclude_ids:
            query = query.where(Cleaner.id.notin_(exclude_ids))

        result = await self.db.execute(query)
        cleaners = result.scalars().all()

        # Get property for familiarity lookup
        property_result = await self.db.execute(
            select(Property).where(Property.id == job.property_id)
        )
        property_obj = property_result.scalar_one_or_none()

        rankings = []
        for cleaner in cleaners:
            ranking = await self._calculate_cleaner_score(cleaner, job, property_obj)
            rankings.append(ranking)

        # Sort by score descending
        rankings.sort(key=lambda r: r.score, reverse=True)
        return rankings

    async def _calculate_cleaner_score(
        self,
        cleaner: Cleaner,
        job: Job,
        property_obj: Optional[Property]
    ) -> CleanerRanking:
        """
        Calculate overall score for a cleaner for a specific job.

        Uses weighted factors:
        - Property familiarity (40%)
        - Availability score (30%)
        - Response rate (20%)
        - Rating (10%)
        """
        weights = self.settings.cleaner_ranking_weights
        reasons = []

        # Get property familiarity
        familiarity_result = await self.db.execute(
            select(CleanerPropertyFamiliarity).where(
                and_(
                    CleanerPropertyFamiliarity.cleaner_id == cleaner.id,
                    CleanerPropertyFamiliarity.property_id == job.property_id
                )
            )
        )
        familiarity = familiarity_result.scalar_one_or_none()

        familiarity_score = 0.0
        if familiarity:
            if familiarity.is_blacklisted:
                # Return zero score for blacklisted
                return CleanerRanking(
                    cleaner_id=cleaner.id,
                    cleaner_name=cleaner.name,
                    score=0.0,
                    property_familiarity=0.0,
                    availability_score=0.0,
                    response_rate=0.0,
                    rating=0.0,
                    reasons=["Blacklisted for this property"]
                )
            familiarity_score = familiarity.familiarity_score
            if familiarity.is_preferred:
                familiarity_score = 1.0
                reasons.append("Preferred cleaner for this property")
            elif familiarity.times_cleaned > 0:
                reasons.append(f"Cleaned this property {familiarity.times_cleaned} times")

        # Availability score based on city preferences
        availability_score = 0.5  # Default neutral
        if property_obj and cleaner.preferred_cities:
            if property_obj.city in cleaner.preferred_cities:
                availability_score = 1.0
                reasons.append(f"Prefers working in {property_obj.city}")
            else:
                availability_score = 0.3

        # Response rate
        response_rate = cleaner.response_rate
        if response_rate >= 0.9:
            reasons.append("Excellent response rate")
        elif response_rate < 0.5:
            reasons.append("Low response rate")

        # Rating (normalized to 0-1 from 1-5)
        rating_score = (cleaner.average_rating - 1) / 4  # Convert 1-5 to 0-1
        if cleaner.average_rating >= 4.8:
            reasons.append("Top-rated cleaner")

        # Calculate weighted score
        total_score = (
            familiarity_score * weights["property_familiarity"] +
            availability_score * weights["availability_score"] +
            response_rate * weights["response_rate"] +
            rating_score * weights["rating"]
        )

        return CleanerRanking(
            cleaner_id=cleaner.id,
            cleaner_name=cleaner.name,
            score=round(total_score, 3),
            property_familiarity=round(familiarity_score, 3),
            availability_score=round(availability_score, 3),
            response_rate=round(response_rate, 3),
            rating=round(rating_score, 3),
            reasons=reasons if reasons else ["Standard ranking"]
        )

    async def assign_job_to_best_cleaner(
        self,
        job: Job,
        send_offer: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Assign a job to the best available cleaner.

        Args:
            job: Job to assign
            send_offer: Whether to send the offer message

        Returns:
            Assignment result or None if no cleaner available
        """
        # Get cleaners who already rejected this job
        rejected_result = await self.db.execute(
            select(JobOffer.cleaner_id).where(
                and_(
                    JobOffer.job_id == job.id,
                    JobOffer.status.in_(["rejected", "expired"])
                )
            )
        )
        excluded_ids = [r[0] for r in rejected_result.all()]

        # Rank cleaners
        rankings = await self.rank_cleaners_for_job(job, exclude_cleaner_ids=excluded_ids)

        if not rankings:
            logger.warning(f"No available cleaners for job {job.id}")
            return None

        # Try to assign to top-ranked cleaner
        best_cleaner = rankings[0]

        # Get cleaner model
        cleaner_result = await self.db.execute(
            select(Cleaner).where(Cleaner.id == best_cleaner.cleaner_id)
        )
        cleaner = cleaner_result.scalar_one_or_none()

        if not cleaner:
            return None

        # Get property for message
        property_result = await self.db.execute(
            select(Property).where(Property.id == job.property_id)
        )
        property_obj = property_result.scalar_one_or_none()

        # Create job offer
        expires_hours = (
            self.settings.urgent_response_timeout_hours
            if job.urgency in ["urgent", "same_day"]
            else self.settings.default_response_timeout_hours
        )

        offer = await self.job_service.create_job_offer(
            job_id=job.id,
            cleaner_id=cleaner.id,
            expires_hours=expires_hours
        )

        result = {
            "job_id": job.id,
            "cleaner_id": cleaner.id,
            "cleaner_name": cleaner.name,
            "offer_id": offer.id,
            "ranking": best_cleaner,
            "message_sent": False
        }

        # Send offer message
        if send_offer:
            job_info = {
                "property_name": property_obj.short_name if property_obj else f"Property #{job.property_id}",
                "date": job.scheduled_date.strftime("%A %b %d"),
                "time": job.scheduled_time or "TBD",
                "amount": job.payment_amount or 0
            }

            message = await self.ai.generate_job_offer_message(
                jobs=[job_info],
                cleaner_name=cleaner.name.split()[0]
            )

            send_result = await self.messaging.send_job_offer(
                cleaner=cleaner,
                job_message=message,
                job_ids=[job.id]
            )

            result["message_sent"] = send_result.get("success", False)
            result["message_id"] = send_result.get("message_id")

        logger.info(f"Assigned job {job.id} to cleaner {cleaner.name} (offer {offer.id})")
        return result

    async def cascade_to_next_cleaner(
        self,
        job: Job
    ) -> Optional[Dict[str, Any]]:
        """
        Cascade job to next available cleaner after rejection/expiration.

        Args:
            job: Job to cascade

        Returns:
            Assignment result or None if no more cleaners
        """
        if job.assignment_attempts >= job.max_assignment_attempts:
            # Max attempts reached, escalate
            job.status = JobStatus.ESCALATED.value
            logger.warning(f"Job {job.id} escalated - max assignment attempts reached")
            return None

        return await self.assign_job_to_best_cleaner(job)

    async def reassign_job(
        self,
        job_id: int,
        new_cleaner_id: int,
        cancel_current: bool = True
    ) -> Dict[str, Any]:
        """
        Manually reassign a job to a different cleaner.

        Args:
            job_id: Job to reassign
            new_cleaner_id: New cleaner ID
            cancel_current: Whether to cancel current offer

        Returns:
            Reassignment result
        """
        job = await self.job_service.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        # Cancel current offer if exists
        if cancel_current:
            current_offer_result = await self.db.execute(
                select(JobOffer).where(
                    and_(
                        JobOffer.job_id == job_id,
                        JobOffer.status == "pending"
                    )
                )
            )
            current_offer = current_offer_result.scalar_one_or_none()
            if current_offer:
                current_offer.status = "cancelled"
                logger.info(f"Cancelled existing offer {current_offer.id}")

        # Get new cleaner
        cleaner_result = await self.db.execute(
            select(Cleaner).where(Cleaner.id == new_cleaner_id)
        )
        cleaner = cleaner_result.scalar_one_or_none()

        if not cleaner:
            raise ValueError(f"Cleaner {new_cleaner_id} not found")

        # Create new offer
        offer = await self.job_service.create_job_offer(
            job_id=job_id,
            cleaner_id=new_cleaner_id
        )

        # Get property for message
        property_result = await self.db.execute(
            select(Property).where(Property.id == job.property_id)
        )
        property_obj = property_result.scalar_one_or_none()

        # Send offer
        job_info = {
            "property_name": property_obj.short_name if property_obj else f"Property #{job.property_id}",
            "date": job.scheduled_date.strftime("%A %b %d"),
            "time": job.scheduled_time or "TBD",
            "amount": job.payment_amount or 0
        }

        message = await self.ai.generate_job_offer_message(
            jobs=[job_info],
            cleaner_name=cleaner.name.split()[0]
        )

        send_result = await self.messaging.send_job_offer(
            cleaner=cleaner,
            job_message=message,
            job_ids=[job_id]
        )

        return {
            "job_id": job_id,
            "old_cleaner_id": job.assigned_cleaner_id,
            "new_cleaner_id": new_cleaner_id,
            "offer_id": offer.id,
            "message_sent": send_result.get("success", False)
        }

    async def update_familiarity_after_cleaning(
        self,
        cleaner_id: int,
        property_id: int
    ):
        """Update cleaner's familiarity with property after completing a job."""
        result = await self.db.execute(
            select(CleanerPropertyFamiliarity).where(
                and_(
                    CleanerPropertyFamiliarity.cleaner_id == cleaner_id,
                    CleanerPropertyFamiliarity.property_id == property_id
                )
            )
        )
        familiarity = result.scalar_one_or_none()

        if familiarity:
            familiarity.update_after_cleaning()
        else:
            # Create new familiarity record
            familiarity = CleanerPropertyFamiliarity(
                cleaner_id=cleaner_id,
                property_id=property_id,
                times_cleaned=1,
                familiarity_score=0.3,
                last_cleaned_at=datetime.now(timezone.utc).isoformat()
            )
            self.db.add(familiarity)

        logger.info(f"Updated familiarity for cleaner {cleaner_id} at property {property_id}")
