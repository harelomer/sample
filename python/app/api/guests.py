"""Guest CRUD API endpoints."""

from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.base import get_db
from app.models.guest import Guest
from app.models.coordination import CoordinationEvent
from app.schemas.guest import GuestCreate, GuestUpdate, GuestResponse
from app.security import require_admin_api_key

router = APIRouter(
    prefix="/guests",
    tags=["guests"],
    dependencies=[Depends(require_admin_api_key)],
)


@router.post("/", response_model=GuestResponse)
async def create_guest(
    guest_data: GuestCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new guest reservation."""
    # Check for duplicate reservation ID
    existing = await db.execute(
        select(Guest).where(Guest.reservation_id == guest_data.reservation_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Reservation already exists")

    guest = Guest(**guest_data.model_dump())
    db.add(guest)
    await db.flush()
    await db.refresh(guest)
    return guest


@router.get("/")
async def list_guests(
    property_id: Optional[int] = None,
    status: Optional[str] = None,
    current_only: bool = Query(False, description="Only show current guests"),
    upcoming_only: bool = Query(False, description="Only show upcoming guests"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """List guests with optional filters."""
    query = select(Guest).options(selectinload(Guest.rental_property))

    conditions = []
    if property_id:
        conditions.append(Guest.property_id == property_id)
    if status:
        conditions.append(Guest.status == status)

    now = datetime.now(timezone.utc)
    if current_only:
        conditions.append(Guest.check_in_date <= now)
        conditions.append(Guest.check_out_date >= now)
        conditions.append(Guest.status == "checked_in")
    elif upcoming_only:
        conditions.append(Guest.check_in_date > now)

    if conditions:
        query = query.where(and_(*conditions))

    # Get total count
    count_query = select(func.count(Guest.id))
    if conditions:
        count_query = count_query.where(and_(*conditions))
    total = await db.scalar(count_query) or 0

    # Get paginated results
    query = query.order_by(Guest.check_in_date.asc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    guests = result.scalars().all()

    return {
        "items": [
            {
                "id": g.id,
                "name": g.name,
                "property_id": g.property_id,
                "property_name": g.rental_property.name if g.rental_property else None,
                "reservation_id": g.reservation_id,
                "check_in_date": g.check_in_date.isoformat() if g.check_in_date else None,
                "check_out_date": g.check_out_date.isoformat() if g.check_out_date else None,
                "check_in_time": g.check_in_time,
                "check_out_time": g.check_out_time,
                "status": g.status,
                "is_currently_staying": g.is_currently_staying,
                "stay_length_nights": g.stay_length_nights
            }
            for g in guests
        ],
        "total": total,
        "page": page,
        "page_size": page_size
    }


@router.get("/{guest_id}", response_model=GuestResponse)
async def get_guest(
    guest_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get a guest by ID."""
    result = await db.execute(
        select(Guest)
        .options(selectinload(Guest.rental_property))
        .where(Guest.id == guest_id)
    )
    guest = result.scalar_one_or_none()

    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")

    return guest


@router.patch("/{guest_id}", response_model=GuestResponse)
async def update_guest(
    guest_id: int,
    guest_data: GuestUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update a guest."""
    result = await db.execute(
        select(Guest).where(Guest.id == guest_id)
    )
    guest = result.scalar_one_or_none()

    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")

    update_data = guest_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(guest, field, value)

    await db.flush()
    await db.refresh(guest)
    return guest


@router.post("/{guest_id}/check-in")
async def check_in_guest(
    guest_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Mark guest as checked in."""
    result = await db.execute(
        select(Guest).where(Guest.id == guest_id)
    )
    guest = result.scalar_one_or_none()

    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")

    guest.status = "checked_in"

    return {"success": True, "message": f"Guest {guest_id} checked in"}


@router.post("/{guest_id}/check-out")
async def check_out_guest(
    guest_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Mark guest as checked out."""
    result = await db.execute(
        select(Guest).where(Guest.id == guest_id)
    )
    guest = result.scalar_one_or_none()

    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")

    guest.status = "checked_out"

    return {"success": True, "message": f"Guest {guest_id} checked out"}


@router.get("/{guest_id}/events")
async def get_guest_events(
    guest_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get coordination events for a guest."""
    result = await db.execute(
        select(CoordinationEvent)
        .where(CoordinationEvent.guest_id == guest_id)
        .order_by(CoordinationEvent.created_at.desc())
    )
    events = result.scalars().all()

    return {
        "guest_id": guest_id,
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "priority": e.priority,
                "status": e.status,
                "title": e.title,
                "description": e.description,
                "created_at": e.created_at.isoformat(),
                "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
                "resolution_outcome": e.resolution_outcome
            }
            for e in events
        ]
    }
