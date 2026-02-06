"""Service for managing property house book entries."""

from datetime import datetime, timezone, timedelta
from typing import List, Optional
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.house_book import HouseBookEntry, HouseBookEntryType


class HouseBookService:
    """Service for managing property house book entries."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_entry(
        self,
        property_id: int,
        content: str,
        entry_type: str = HouseBookEntryType.STATIC.value,
        title: Optional[str] = None,
        expires_hours: Optional[int] = None,
        priority: int = 0,
    ) -> HouseBookEntry:
        """
        Create a new house book entry.

        Args:
            property_id: Property ID
            content: Entry content/message
            entry_type: static or live
            title: Optional title for the entry
            expires_hours: For live entries, hours until expiration
            priority: Display priority (higher = more important)

        Returns:
            Created HouseBookEntry
        """
        expires_at = None
        if expires_hours and entry_type == HouseBookEntryType.LIVE.value:
            expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_hours)

        entry = HouseBookEntry(
            property_id=property_id,
            entry_type=entry_type,
            title=title,
            content=content,
            expires_at=expires_at,
            priority=priority,
            active=True,
        )

        self.db.add(entry)
        await self.db.flush()
        return entry

    async def get_active_entries(
        self,
        property_id: int,
        entry_type: Optional[str] = None,
    ) -> List[HouseBookEntry]:
        """
        Get active house book entries for a property.

        Args:
            property_id: Property ID
            entry_type: Filter by entry type (optional)

        Returns:
            List of active, non-expired entries
        """
        conditions = [
            HouseBookEntry.property_id == property_id,
            HouseBookEntry.active == True,
        ]

        if entry_type:
            conditions.append(HouseBookEntry.entry_type == entry_type)

        result = await self.db.execute(
            select(HouseBookEntry)
            .where(and_(*conditions))
            .order_by(HouseBookEntry.priority.desc(), HouseBookEntry.created_at.desc())
        )
        entries = result.scalars().all()

        # Filter out expired entries
        now = datetime.now(timezone.utc)
        active_entries = [
            entry for entry in entries
            if entry.expires_at is None or entry.expires_at > now
        ]

        return active_entries

    async def update_entry(
        self,
        entry_id: int,
        content: Optional[str] = None,
        title: Optional[str] = None,
        active: Optional[bool] = None,
        priority: Optional[int] = None,
    ) -> Optional[HouseBookEntry]:
        """
        Update a house book entry.

        Args:
            entry_id: Entry ID
            content: New content (optional)
            title: New title (optional)
            active: New active status (optional)
            priority: New priority (optional)

        Returns:
            Updated entry or None if not found
        """
        result = await self.db.execute(
            select(HouseBookEntry).where(HouseBookEntry.id == entry_id)
        )
        entry = result.scalar_one_or_none()

        if not entry:
            return None

        if content is not None:
            entry.content = content
        if title is not None:
            entry.title = title
        if active is not None:
            entry.active = active
        if priority is not None:
            entry.priority = priority

        await self.db.flush()
        return entry

    async def delete_entry(self, entry_id: int) -> bool:
        """
        Delete a house book entry.

        Args:
            entry_id: Entry ID

        Returns:
            True if deleted, False if not found
        """
        result = await self.db.execute(
            select(HouseBookEntry).where(HouseBookEntry.id == entry_id)
        )
        entry = result.scalar_one_or_none()

        if not entry:
            return False

        await self.db.delete(entry)
        await self.db.flush()
        return True

    async def get_entry(self, entry_id: int) -> Optional[HouseBookEntry]:
        """
        Get a specific house book entry by ID.

        Args:
            entry_id: Entry ID

        Returns:
            HouseBookEntry or None if not found
        """
        result = await self.db.execute(
            select(HouseBookEntry).where(HouseBookEntry.id == entry_id)
        )
        return result.scalar_one_or_none()

    async def expire_old_entries(self) -> int:
        """
        Clean up expired live entries by marking them inactive.

        Returns:
            Number of entries expired
        """
        now = datetime.now(timezone.utc)

        result = await self.db.execute(
            select(HouseBookEntry).where(
                and_(
                    HouseBookEntry.entry_type == HouseBookEntryType.LIVE.value,
                    HouseBookEntry.expires_at.isnot(None),
                    HouseBookEntry.expires_at < now,
                    HouseBookEntry.active == True,
                )
            )
        )
        expired_entries = result.scalars().all()

        count = 0
        for entry in expired_entries:
            entry.active = False
            count += 1

        await self.db.flush()
        return count
