"""Property CRUD API endpoints."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import get_db
from app.models.property import Property
from app.schemas.property import (
    PropertyCreate,
    PropertyUpdate,
    PropertyResponse,
    PropertyListResponse,
)

router = APIRouter(prefix="/properties", tags=["properties"])


@router.post("/", response_model=PropertyResponse)
async def create_property(
    property_data: PropertyCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new property."""
    property_obj = Property(**property_data.model_dump())
    db.add(property_obj)
    await db.flush()
    await db.refresh(property_obj)
    return property_obj


@router.get("/", response_model=PropertyListResponse)
async def list_properties(
    city: Optional[str] = None,
    is_active: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """List properties with optional filters."""
    query = select(Property)

    if city:
        query = query.where(Property.city == city)
    if is_active is not None:
        query = query.where(Property.is_active == is_active)

    # Get total count
    count_query = select(func.count(Property.id))
    if city:
        count_query = count_query.where(Property.city == city)
    if is_active is not None:
        count_query = count_query.where(Property.is_active == is_active)
    total = await db.scalar(count_query) or 0

    # Get paginated results
    query = query.order_by(Property.name.asc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    properties = result.scalars().all()

    return PropertyListResponse(
        items=properties,
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/{property_id}", response_model=PropertyResponse)
async def get_property(
    property_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get a property by ID."""
    result = await db.execute(
        select(Property).where(Property.id == property_id)
    )
    property_obj = result.scalar_one_or_none()

    if not property_obj:
        raise HTTPException(status_code=404, detail="Property not found")

    return property_obj


@router.patch("/{property_id}", response_model=PropertyResponse)
async def update_property(
    property_id: int,
    property_data: PropertyUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update a property."""
    result = await db.execute(
        select(Property).where(Property.id == property_id)
    )
    property_obj = result.scalar_one_or_none()

    if not property_obj:
        raise HTTPException(status_code=404, detail="Property not found")

    update_data = property_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(property_obj, field, value)

    await db.flush()
    await db.refresh(property_obj)
    return property_obj


@router.delete("/{property_id}")
async def delete_property(
    property_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Delete a property (soft delete by setting is_active=False)."""
    result = await db.execute(
        select(Property).where(Property.id == property_id)
    )
    property_obj = result.scalar_one_or_none()

    if not property_obj:
        raise HTTPException(status_code=404, detail="Property not found")

    property_obj.is_active = False

    return {"success": True, "message": f"Property {property_id} deactivated"}
