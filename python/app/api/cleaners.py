"""Cleaner CRUD API endpoints."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import get_db
from app.models.cleaner import Cleaner, CleanerPropertyFamiliarity
from app.schemas.cleaner import (
    CleanerCreate,
    CleanerUpdate,
    CleanerResponse,
    CleanerListResponse,
)
from app.security import require_admin_api_key

router = APIRouter(
    prefix="/cleaners",
    tags=["cleaners"],
    dependencies=[Depends(require_admin_api_key)],
)


@router.post("/", response_model=CleanerResponse)
async def create_cleaner(
    cleaner_data: CleanerCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new cleaner."""
    # Check for duplicate phone
    existing = await db.execute(
        select(Cleaner).where(Cleaner.phone == cleaner_data.phone)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Cleaner with this phone already exists")

    # Generate WhatsApp chat ID from phone
    phone_digits = cleaner_data.phone.lstrip("+")
    whatsapp_chat_id = cleaner_data.whatsapp_chat_id or f"{phone_digits}@c.us"

    cleaner = Cleaner(
        **cleaner_data.model_dump(exclude={"whatsapp_chat_id"}),
        whatsapp_chat_id=whatsapp_chat_id
    )
    db.add(cleaner)
    await db.flush()
    await db.refresh(cleaner)
    return cleaner


@router.get("/", response_model=CleanerListResponse)
async def list_cleaners(
    is_active: Optional[bool] = None,
    is_available: Optional[bool] = None,
    city: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """List cleaners with optional filters."""
    query = select(Cleaner)

    conditions = []
    if is_active is not None:
        conditions.append(Cleaner.is_active == is_active)
    if is_available is not None:
        conditions.append(Cleaner.is_available == is_available)
    if city:
        # Filter by preferred cities (JSON array contains)
        conditions.append(Cleaner.preferred_cities.contains([city]))

    if conditions:
        query = query.where(and_(*conditions))

    # Get total count
    count_query = select(func.count(Cleaner.id))
    if conditions:
        count_query = count_query.where(and_(*conditions))
    total = await db.scalar(count_query) or 0

    # Get paginated results
    query = query.order_by(Cleaner.name.asc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    cleaners = result.scalars().all()

    return CleanerListResponse(
        items=cleaners,
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/{cleaner_id}", response_model=CleanerResponse)
async def get_cleaner(
    cleaner_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get a cleaner by ID."""
    result = await db.execute(
        select(Cleaner).where(Cleaner.id == cleaner_id)
    )
    cleaner = result.scalar_one_or_none()

    if not cleaner:
        raise HTTPException(status_code=404, detail="Cleaner not found")

    return cleaner


@router.patch("/{cleaner_id}", response_model=CleanerResponse)
async def update_cleaner(
    cleaner_id: int,
    cleaner_data: CleanerUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update a cleaner."""
    result = await db.execute(
        select(Cleaner).where(Cleaner.id == cleaner_id)
    )
    cleaner = result.scalar_one_or_none()

    if not cleaner:
        raise HTTPException(status_code=404, detail="Cleaner not found")

    update_data = cleaner_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(cleaner, field, value)

    await db.flush()
    await db.refresh(cleaner)
    return cleaner


@router.delete("/{cleaner_id}")
async def delete_cleaner(
    cleaner_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Delete a cleaner (soft delete by setting is_active=False)."""
    result = await db.execute(
        select(Cleaner).where(Cleaner.id == cleaner_id)
    )
    cleaner = result.scalar_one_or_none()

    if not cleaner:
        raise HTTPException(status_code=404, detail="Cleaner not found")

    cleaner.is_active = False

    return {"success": True, "message": f"Cleaner {cleaner_id} deactivated"}


@router.get("/{cleaner_id}/stats")
async def get_cleaner_stats(
    cleaner_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get detailed statistics for a cleaner."""
    result = await db.execute(
        select(Cleaner).where(Cleaner.id == cleaner_id)
    )
    cleaner = result.scalar_one_or_none()

    if not cleaner:
        raise HTTPException(status_code=404, detail="Cleaner not found")

    # Get property familiarities
    fam_result = await db.execute(
        select(CleanerPropertyFamiliarity)
        .where(CleanerPropertyFamiliarity.cleaner_id == cleaner_id)
        .order_by(CleanerPropertyFamiliarity.familiarity_score.desc())
    )
    familiarities = fam_result.scalars().all()

    return {
        "cleaner_id": cleaner_id,
        "name": cleaner.name,
        "total_jobs_completed": cleaner.total_jobs_completed,
        "total_jobs_offered": cleaner.total_jobs_offered,
        "jobs_accepted": cleaner.jobs_accepted,
        "jobs_rejected": cleaner.jobs_rejected,
        "jobs_no_response": cleaner.jobs_no_response,
        "response_rate": cleaner.response_rate,
        "acceptance_rate": cleaner.acceptance_rate,
        "average_rating": cleaner.average_rating,
        "average_response_time_minutes": cleaner.average_response_time_minutes,
        "property_familiarities": [
            {
                "property_id": f.property_id,
                "times_cleaned": f.times_cleaned,
                "familiarity_score": f.familiarity_score,
                "is_preferred": f.is_preferred,
                "is_blacklisted": f.is_blacklisted
            }
            for f in familiarities
        ]
    }


@router.post("/{cleaner_id}/properties/{property_id}/familiarity")
async def set_property_familiarity(
    cleaner_id: int,
    property_id: int,
    is_preferred: Optional[bool] = None,
    is_blacklisted: Optional[bool] = None,
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Set or update cleaner's familiarity with a property."""
    # Verify cleaner exists
    cleaner_result = await db.execute(
        select(Cleaner).where(Cleaner.id == cleaner_id)
    )
    if not cleaner_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Cleaner not found")

    # Get or create familiarity record
    fam_result = await db.execute(
        select(CleanerPropertyFamiliarity).where(
            and_(
                CleanerPropertyFamiliarity.cleaner_id == cleaner_id,
                CleanerPropertyFamiliarity.property_id == property_id
            )
        )
    )
    familiarity = fam_result.scalar_one_or_none()

    if not familiarity:
        familiarity = CleanerPropertyFamiliarity(
            cleaner_id=cleaner_id,
            property_id=property_id
        )
        db.add(familiarity)

    if is_preferred is not None:
        familiarity.is_preferred = is_preferred
    if is_blacklisted is not None:
        familiarity.is_blacklisted = is_blacklisted
    if notes is not None:
        familiarity.notes = notes

    await db.flush()

    return {
        "success": True,
        "cleaner_id": cleaner_id,
        "property_id": property_id,
        "is_preferred": familiarity.is_preferred,
        "is_blacklisted": familiarity.is_blacklisted
    }
