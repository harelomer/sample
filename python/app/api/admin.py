"""
Admin API endpoints for dashboard operations.

Provides endpoints for:
- Manual job assignment/reassignment
- Viewing system status
- Running batch operations
- Managing escalated jobs
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import get_db
from app.models.job import Job, JobOffer, JobStatus, JobStatusHistory
from app.models.cleaner import Cleaner
from app.models.coordination import CoordinationEvent
from app.schemas.job import JobAssignment, BatchJobOffer
from app.schemas.cleaner import CleanerRanking
from app.services.assignment_service import AssignmentService
from app.services.scheduler_service import SchedulerService
from app.services.job_service import JobService
from app.security import require_admin_api_key

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_api_key)],
)


@router.get("/dashboard")
async def get_dashboard(
    db: AsyncSession = Depends(get_db)
):
    """
    Get dashboard overview statistics.

    Returns counts and summaries for the admin dashboard.
    """
    # Job statistics
    jobs_pending = await db.scalar(
        select(func.count(Job.id)).where(
            Job.status.in_([JobStatus.PENDING.value, JobStatus.BATCHED.value])
        )
    )
    jobs_offered = await db.scalar(
        select(func.count(Job.id)).where(Job.status == JobStatus.OFFERED.value)
    )
    jobs_confirmed = await db.scalar(
        select(func.count(Job.id)).where(Job.status == JobStatus.CONFIRMED.value)
    )
    jobs_in_progress = await db.scalar(
        select(func.count(Job.id)).where(
            Job.status.in_([JobStatus.EN_ROUTE.value, JobStatus.IN_PROGRESS.value])
        )
    )
    jobs_escalated = await db.scalar(
        select(func.count(Job.id)).where(Job.status == JobStatus.ESCALATED.value)
    )

    # Today's jobs
    today = datetime.now(timezone.utc).date()
    tomorrow = today + timedelta(days=1)
    jobs_today = await db.scalar(
        select(func.count(Job.id)).where(
            Job.scheduled_date >= datetime.combine(today, datetime.min.time()),
            Job.scheduled_date < datetime.combine(tomorrow, datetime.min.time())
        )
    )

    # Active cleaners
    active_cleaners = await db.scalar(
        select(func.count(Cleaner.id)).where(
            Cleaner.is_active == True,
            Cleaner.is_available == True
        )
    )

    # Open coordination events
    open_events = await db.scalar(
        select(func.count(CoordinationEvent.id)).where(
            CoordinationEvent.status.in_(["open", "assigned", "in_progress"])
        )
    )

    # Pending offers needing attention
    pending_offers = await db.scalar(
        select(func.count(JobOffer.id)).where(JobOffer.status == "pending")
    )

    return {
        "jobs": {
            "pending": jobs_pending or 0,
            "offered": jobs_offered or 0,
            "confirmed": jobs_confirmed or 0,
            "in_progress": jobs_in_progress or 0,
            "escalated": jobs_escalated or 0,
            "today": jobs_today or 0
        },
        "cleaners": {
            "active_available": active_cleaners or 0
        },
        "coordination": {
            "open_events": open_events or 0
        },
        "offers": {
            "pending_response": pending_offers or 0
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.post("/jobs/{job_id}/assign")
async def assign_job(
    job_id: int,
    assignment: Optional[JobAssignment] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Assign a job to a cleaner.

    If no cleaner_id is provided, automatically assigns to the best available cleaner.
    If cleaner_id is provided, assigns to that specific cleaner.
    """
    assignment_service = AssignmentService(db)
    job_service = JobService(db)

    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        if assignment and assignment.cleaner_id:
            # Manual assignment to specific cleaner
            result = await assignment_service.reassign_job(
                job_id=job_id,
                new_cleaner_id=assignment.cleaner_id,
                cancel_current=True
            )
            return {
                "success": True,
                "message": f"Job {job_id} assigned to cleaner {assignment.cleaner_id}",
                **result
            }
        else:
            # Auto-assign to best available cleaner
            result = await assignment_service.assign_job_to_best_cleaner(job, send_offer=True)
            if result:
                return {
                    "success": True,
                    "message": f"Job {job_id} assigned to {result['cleaner_name']}",
                    **result
                }
            else:
                raise HTTPException(
                    status_code=400,
                    detail="No available cleaners for this job"
                )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error assigning job {job_id}: {e}")
        raise HTTPException(status_code=500, detail="Assignment failed")


@router.get("/jobs/{job_id}/rank-cleaners")
async def rank_cleaners_for_job(
    job_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Get ranked list of cleaners for a specific job.

    Useful for seeing who would be assigned next or for manual selection.
    """
    job_service = JobService(db)
    assignment_service = AssignmentService(db)

    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    rankings = await assignment_service.rank_cleaners_for_job(job)

    return {
        "job_id": job_id,
        "property_id": job.property_id,
        "rankings": [
            {
                "rank": i + 1,
                "cleaner_id": r.cleaner_id,
                "cleaner_name": r.cleaner_name,
                "score": r.score,
                "property_familiarity": r.property_familiarity,
                "availability_score": r.availability_score,
                "response_rate": r.response_rate,
                "rating": r.rating,
                "reasons": r.reasons
            }
            for i, r in enumerate(rankings)
        ]
    }


@router.post("/jobs/{job_id}/escalate")
async def escalate_job(
    job_id: int,
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Manually escalate a job that needs attention.

    Use when automatic assignment has failed or special handling is needed.
    """
    job_service = JobService(db)

    try:
        job = await job_service.update_job_status(
            job_id=job_id,
            new_status=JobStatus.ESCALATED,
            changed_by="manager",
            notes=notes or "Manually escalated"
        )
        return {
            "success": True,
            "message": f"Job {job_id} escalated",
            "job_id": job.id,
            "status": job.status
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/batch/run")
async def run_batch_delivery(
    db: AsyncSession = Depends(get_db)
):
    """
    Manually trigger batch job delivery.

    Normally runs automatically at 6 PM, but can be triggered manually.
    """
    scheduler_service = SchedulerService(db)
    result = await scheduler_service.run_batch_delivery()

    return {
        "success": True,
        "message": "Batch delivery completed",
        **result
    }


@router.post("/reminders/run")
async def run_reminder_check(
    db: AsyncSession = Depends(get_db)
):
    """
    Manually trigger reminder check.

    Sends reminders to cleaners who haven't responded to offers.
    """
    scheduler_service = SchedulerService(db)
    result = await scheduler_service.run_reminder_check()

    return {
        "success": True,
        "message": "Reminder check completed",
        **result
    }


@router.post("/expirations/run")
async def run_expiration_check(
    db: AsyncSession = Depends(get_db)
):
    """
    Manually trigger offer expiration check.

    Expires old offers and cascades jobs to next cleaner.
    """
    scheduler_service = SchedulerService(db)
    result = await scheduler_service.run_expiration_check()

    return {
        "success": True,
        "message": "Expiration check completed",
        **result
    }


@router.get("/escalated-jobs")
async def get_escalated_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """
    Get list of escalated jobs that need manual attention.
    """
    job_service = JobService(db)
    jobs, total = await job_service.get_jobs(
        status=JobStatus.ESCALATED.value,
        page=page,
        page_size=page_size
    )

    return {
        "items": [
            {
                "id": job.id,
                "property_id": job.property_id,
                "property_name": job.rental_property.name if job.rental_property else None,
                "scheduled_date": job.scheduled_date.isoformat() if job.scheduled_date else None,
                "scheduled_time": job.scheduled_time,
                "urgency": job.urgency,
                "assignment_attempts": job.assignment_attempts,
                "payment_amount": job.payment_amount,
                "notes": job.notes,
                "created_at": job.created_at.isoformat()
            }
            for job in jobs
        ],
        "total": total,
        "page": page,
        "page_size": page_size
    }


@router.get("/pending-offers")
async def get_pending_offers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """
    Get list of pending job offers awaiting cleaner response.
    """
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(JobOffer)
        .options(
            selectinload(JobOffer.job).selectinload(Job.rental_property),
            selectinload(JobOffer.cleaner)
        )
        .where(JobOffer.status == "pending")
        .order_by(JobOffer.offered_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    offers = result.scalars().all()

    total_result = await db.execute(
        select(func.count(JobOffer.id)).where(JobOffer.status == "pending")
    )
    total = total_result.scalar() or 0

    now = datetime.now(timezone.utc)
    return {
        "items": [
            {
                "id": offer.id,
                "job_id": offer.job_id,
                "cleaner_id": offer.cleaner_id,
                "cleaner_name": offer.cleaner.name if offer.cleaner else None,
                "property_name": offer.job.rental_property.name if offer.job and offer.job.rental_property else None,
                "scheduled_date": offer.job.scheduled_date.isoformat() if offer.job and offer.job.scheduled_date else None,
                "offered_at": offer.offered_at.isoformat(),
                "expires_at": offer.expires_at.isoformat() if offer.expires_at else None,
                "hours_pending": round((now - offer.offered_at).total_seconds() / 3600, 1),
                "reminder_count": offer.reminder_count,
                "batch_id": offer.batch_id
            }
            for offer in offers
        ],
        "total": total,
        "page": page,
        "page_size": page_size
    }


@router.post("/reset-database")
async def reset_database(
    db: AsyncSession = Depends(get_db)
):
    """
    Reset the database by dropping and recreating all tables.

    WARNING: This deletes ALL data. Only available in debug mode.
    """
    from app.config import get_settings
    from app.models.base import Base, get_engine

    settings = get_settings()
    if not settings.debug:
        raise HTTPException(
            status_code=403,
            detail="Database reset is only available in debug mode."
        )

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database reset - all tables dropped and recreated")
    return {
        "success": True,
        "message": "Database has been reset. All data deleted."
    }


@router.delete("/jobs/all")
async def delete_all_jobs(
    db: AsyncSession = Depends(get_db)
):
    """
    Delete ALL jobs and their related offers/history from the database.

    WARNING: This permanently removes all job data.
    """
    # Delete related records first (foreign key constraints)
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(JobStatusHistory))
    await db.execute(sql_delete(JobOffer))
    await db.execute(sql_delete(Job))
    await db.commit()

    logger.info("All jobs, offers, and status history deleted")
    return {
        "success": True,
        "message": "All jobs have been deleted"
    }


@router.post("/offers/{offer_id}/cancel")
async def cancel_offer(
    offer_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Cancel a pending job offer.
    """
    result = await db.execute(
        select(JobOffer).where(JobOffer.id == offer_id)
    )
    offer = result.scalar_one_or_none()

    if not offer:
        raise HTTPException(status_code=404, detail="Offer not found")

    if offer.status != "pending":
        raise HTTPException(status_code=400, detail=f"Cannot cancel offer in {offer.status} status")

    offer.status = "cancelled"

    # Reset job to pending
    job_result = await db.execute(select(Job).where(Job.id == offer.job_id))
    job = job_result.scalar_one_or_none()
    if job:
        job.status = JobStatus.PENDING.value

    return {
        "success": True,
        "message": f"Offer {offer_id} cancelled",
        "job_id": offer.job_id
    }
