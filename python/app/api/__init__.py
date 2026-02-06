"""API routes for the Property Management System."""

from app.api.webhooks import router as webhooks_router
from app.api.admin import router as admin_router
from app.api.properties import router as properties_router
from app.api.cleaners import router as cleaners_router
from app.api.jobs import router as jobs_router
from app.api.guests import router as guests_router
from app.api.house_book import router as house_book_router

__all__ = [
    "webhooks_router",
    "admin_router",
    "properties_router",
    "cleaners_router",
    "jobs_router",
    "guests_router",
    "house_book_router",
]
