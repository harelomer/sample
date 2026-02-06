"""API endpoints for property house book management."""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import get_db
from app.models.house_book import HouseBookEntry, HouseBookEntryType
from app.services.house_book_service import HouseBookService
from app.dependencies import require_admin_api_key


router = APIRouter(prefix="/house-book", tags=["house-book"])


# Pydantic schemas
class HouseBookEntryCreate(BaseModel):
    property_id: int
    content: str
    entry_type: str = HouseBookEntryType.STATIC.value
    title: Optional[str] = None
    expires_hours: Optional[int] = None
    priority: int = 0


class HouseBookEntryUpdate(BaseModel):
    content: Optional[str] = None
    title: Optional[str] = None
    active: Optional[bool] = None
    priority: Optional[int] = None


class HouseBookEntryResponse(BaseModel):
    id: int
    property_id: int
    entry_type: str
    title: Optional[str]
    content: str
    active: bool
    expires_at: Optional[str]
    priority: int
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


@router.post("/entries", response_model=HouseBookEntryResponse)
async def create_house_book_entry(
    entry: HouseBookEntryCreate,
    db: AsyncSession = Depends(get_db),
    _api_key: str = Depends(require_admin_api_key),
):
    """Create a new house book entry."""
    service = HouseBookService(db)

    new_entry = await service.create_entry(
        property_id=entry.property_id,
        content=entry.content,
        entry_type=entry.entry_type,
        title=entry.title,
        expires_hours=entry.expires_hours,
        priority=entry.priority,
    )

    await db.commit()
    return new_entry


@router.get("/properties/{property_id}/entries", response_model=List[HouseBookEntryResponse])
async def get_property_house_book_entries(
    property_id: int,
    entry_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _api_key: str = Depends(require_admin_api_key),
):
    """Get all active house book entries for a property."""
    service = HouseBookService(db)
    entries = await service.get_active_entries(property_id, entry_type)
    return entries


@router.get("/entries/{entry_id}", response_model=HouseBookEntryResponse)
async def get_house_book_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    _api_key: str = Depends(require_admin_api_key),
):
    """Get a specific house book entry by ID."""
    service = HouseBookService(db)
    entry = await service.get_entry(entry_id)

    if not entry:
        raise HTTPException(status_code=404, detail="House book entry not found")

    return entry


@router.patch("/entries/{entry_id}", response_model=HouseBookEntryResponse)
async def update_house_book_entry(
    entry_id: int,
    updates: HouseBookEntryUpdate,
    db: AsyncSession = Depends(get_db),
    _api_key: str = Depends(require_admin_api_key),
):
    """Update a house book entry."""
    service = HouseBookService(db)

    updated_entry = await service.update_entry(
        entry_id=entry_id,
        content=updates.content,
        title=updates.title,
        active=updates.active,
        priority=updates.priority,
    )

    if not updated_entry:
        raise HTTPException(status_code=404, detail="House book entry not found")

    await db.commit()
    return updated_entry


@router.delete("/entries/{entry_id}")
async def delete_house_book_entry(
    entry_id: int,
    db: AsyncSession = Depends(get_db),
    _api_key: str = Depends(require_admin_api_key),
):
    """Delete a house book entry."""
    service = HouseBookService(db)
    deleted = await service.delete_entry(entry_id)

    if not deleted:
        raise HTTPException(status_code=404, detail="House book entry not found")

    await db.commit()
    return {"status": "deleted", "entry_id": entry_id}


@router.post("/cleanup-expired")
async def cleanup_expired_entries(
    db: AsyncSession = Depends(get_db),
    _api_key: str = Depends(require_admin_api_key),
):
    """Clean up expired live entries."""
    service = HouseBookService(db)
    count = await service.expire_old_entries()
    await db.commit()
    return {"expired_count": count}
