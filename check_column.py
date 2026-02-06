#!/usr/bin/env python3
"""
Quick script to check if eve_reminder_sent column exists.
Run with: python check_column.py
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'python'))

from sqlalchemy import text
from app.models.base import get_db_session


async def check_column():
    """Check if eve_reminder_sent column exists."""
    async with get_db_session() as db:
        result = await db.execute(text("""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'jobs'
                AND column_name = 'eve_reminder_sent'
            )
        """))
        exists = result.scalar()

        if exists:
            print("✅ Column 'eve_reminder_sent' EXISTS - no action needed!")
            print("Your app should work fine.")
        else:
            print("❌ Column 'eve_reminder_sent' MISSING - you need to add it.")
            print("\nRun this SQL in Render's database shell:")
            print("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS eve_reminder_sent BOOLEAN DEFAULT FALSE;")


if __name__ == "__main__":
    asyncio.run(check_column())
