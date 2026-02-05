"""Job CRUD API endpoints."""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.base import get_db
from app.models.job import Job, JobOffer, JobStatusHistory, JobStatus, JobUrgency
from app.schemas.job import (
    JobCreate,
    JobUpdate,
    JobResponse,
    JobListResponse,
)
from app.services.job_service import JobService
from app.security import require_admin_api_key

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    dependencies=[Depends(require_admin_api_key)],
)


@router.post("/", response_model=JobResponse)
async def create_job(
    job_data: JobCreate,
    auto_assign: bool = Query(True, description="Automatically assign to best cleaner (default: True)"),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new cleaning job.

    By default, the job will be immediately offered to the best available cleaner
    and a WhatsApp notification will be sent. Set auto_assign=false to skip this
    and wait for batch delivery at 6 PM.
    """
    job_service = JobService(db)
    job = await job_service.create_job(job_data)

    # Auto-assign to best cleaner and send notification
    if auto_assign:
        from app.services.assignment_service import AssignmentService
        assignment_service = AssignmentService(db)
        assign_result = await assignment_service.assign_job_to_best_cleaner(job, send_offer=True)
        if assign_result:
            # Reload job with updated assignment
            job = await job_service.get_job(job.id)

    # Reload job with relationships
    result = await db.execute(
        select(Job)
        .options(selectinload(Job.rental_property), selectinload(Job.assigned_cleaner))
        .where(Job.id == job.id)
    )
    job = result.scalar_one()

    return _format_job_response(job)


@router.get("/", response_model=JobListResponse)
async def list_jobs(
    status: Optional[str] = None,
    property_id: Optional[int] = None,
    cleaner_id: Optional[int] = None,
    urgency: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """List jobs with optional filters."""
    job_service = JobService(db)
    jobs, total = await job_service.get_jobs(
        status=status,
        property_id=property_id,
        cleaner_id=cleaner_id,
        urgency=urgency,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size
    )

    return JobListResponse(
        items=[_format_job_response(j) for j in jobs],
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get a job by ID."""
    job_service = JobService(db)
    job = await job_service.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return _format_job_response(job)


@router.patch("/{job_id}", response_model=JobResponse)
async def update_job(
    job_id: int,
    job_data: JobUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update a job."""
    job_service = JobService(db)
    job = await job_service.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    update_data = job_data.model_dump(exclude_unset=True)

    # Handle status change specially
    if "status" in update_data:
        new_status = JobStatus(update_data.pop("status"))
        await job_service.update_job_status(job_id, new_status, "manager")

    # Update other fields
    for field, value in update_data.items():
        setattr(job, field, value)

    await db.flush()
    await db.refresh(job)

    return _format_job_response(job)


@router.delete("/{job_id}")
async def cancel_job(
    job_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Cancel a job."""
    job_service = JobService(db)
    job = await job_service.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status in [JobStatus.IN_PROGRESS.value, JobStatus.COMPLETED.value]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job in {job.status} status"
        )

    await job_service.update_job_status(job_id, JobStatus.CANCELLED, "manager", "Job cancelled")

    return {"success": True, "message": f"Job {job_id} cancelled"}


@router.get("/{job_id}/offers")
async def get_job_offers(
    job_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get all offers for a job."""
    result = await db.execute(
        select(JobOffer)
        .options(selectinload(JobOffer.cleaner))
        .where(JobOffer.job_id == job_id)
        .order_by(JobOffer.offered_at.desc())
    )
    offers = result.scalars().all()

    return {
        "job_id": job_id,
        "offers": [
            {
                "id": offer.id,
                "cleaner_id": offer.cleaner_id,
                "cleaner_name": offer.cleaner.name if offer.cleaner else None,
                "status": offer.status,
                "offered_at": offer.offered_at.isoformat(),
                "expires_at": offer.expires_at.isoformat() if offer.expires_at else None,
                "responded_at": offer.responded_at.isoformat() if offer.responded_at else None,
                "response_message": offer.response_message,
                "reminder_count": offer.reminder_count
            }
            for offer in offers
        ]
    }


@router.get("/{job_id}/history")
async def get_job_history(
    job_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get status history for a job."""
    result = await db.execute(
        select(JobStatusHistory)
        .where(JobStatusHistory.job_id == job_id)
        .order_by(JobStatusHistory.changed_at.desc())
    )
    history = result.scalars().all()

    return {
        "job_id": job_id,
        "history": [
            {
                "from_status": h.from_status,
                "to_status": h.to_status,
                "changed_at": h.changed_at.isoformat(),
                "changed_by": h.changed_by,
                "notes": h.notes
            }
            for h in history
        ]
    }


@router.post("/{job_id}/complete")
async def complete_job(
    job_id: int,
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Mark a job as completed."""
    job_service = JobService(db)
    job = await job_service.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in [JobStatus.CONFIRMED.value, JobStatus.EN_ROUTE.value, JobStatus.IN_PROGRESS.value]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete job in {job.status} status"
        )

    await job_service.update_job_status(
        job_id,
        JobStatus.COMPLETED,
        "manager",
        notes or "Manually marked complete"
    )

    # Update cleaner familiarity
    if job.assigned_cleaner_id:
        from app.services.assignment_service import AssignmentService
        assignment_service = AssignmentService(db)
        await assignment_service.update_familiarity_after_cleaning(
            job.assigned_cleaner_id,
            job.property_id
        )

    return {"success": True, "message": f"Job {job_id} completed"}


def _format_job_response(job: Job) -> JobResponse:
    """Format job model to response schema."""
    return JobResponse(
        id=job.id,
        property_id=job.property_id,
        job_type=job.job_type,
        status=job.status,
        urgency=job.urgency,
        scheduled_date=job.scheduled_date,
        scheduled_time=job.scheduled_time,
        estimated_duration_minutes=job.estimated_duration_minutes,
        deadline=job.deadline,
        assigned_cleaner_id=job.assigned_cleaner_id,
        assigned_cleaner_name=job.assigned_cleaner.name if job.assigned_cleaner else None,
        assignment_attempts=job.assignment_attempts,
        payment_amount=job.payment_amount,
        payment_status=job.payment_status,
        guest_id=job.guest_id,
        previous_guest_checkout=job.previous_guest_checkout,
        next_guest_checkin=job.next_guest_checkin,
        started_at=job.started_at,
        completed_at=job.completed_at,
        actual_duration_minutes=job.actual_duration_minutes,
        notes=job.notes,
        cleaner_notes=job.cleaner_notes,
        batch_id=job.batch_id,
        is_same_day=job.is_same_day,
        is_overdue=job.is_overdue,
        property_name=job.rental_property.name if job.rental_property else None,
        created_at=job.created_at,
        updated_at=job.updated_at
    )
