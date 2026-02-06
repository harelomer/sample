"""
AI-Powered Property Management Communication System

Main FastAPI application entry point.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.models.base import init_db, get_db_session
from app.api import (
    webhooks_router,
    admin_router,
    properties_router,
    cleaners_router,
    jobs_router,
    guests_router,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Scheduler for background tasks
scheduler = AsyncIOScheduler()


async def run_startup_migrations():
    """Run database migrations on startup to add missing columns."""
    from sqlalchemy import text

    async with get_db_session() as db:
        # Migration: Add eve_reminder_sent column if missing
        try:
            # Check if column exists using PostgreSQL information_schema
            result = await db.execute(text("""
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'jobs'
                    AND column_name = 'eve_reminder_sent'
                )
            """))
            column_exists = result.scalar()

            if not column_exists:
                # Column doesn't exist, add it
                # Match the model definition: nullable=True, default=False
                await db.execute(text(
                    "ALTER TABLE jobs ADD COLUMN eve_reminder_sent BOOLEAN DEFAULT FALSE"
                ))
                await db.commit()
                logger.info("Migration: Successfully added eve_reminder_sent column to jobs table")
            else:
                logger.info("Migration: eve_reminder_sent column already exists, skipping")

        except Exception as e:
            logger.error(f"CRITICAL: Migration failed for eve_reminder_sent: {e}")
            # Roll back and re-raise to prevent app from starting with broken schema
            await db.rollback()
            raise RuntimeError(f"Database migration failed: {e}") from e


async def scheduled_batch_delivery():
    """Run batch job delivery at scheduled time."""
    from app.services.scheduler_service import SchedulerService

    logger.info("Running scheduled batch delivery")
    async with get_db_session() as db:
        service = SchedulerService(db)
        result = await service.run_batch_delivery()
        logger.info(f"Batch delivery result: {result}")


async def scheduled_reminder_check():
    """Run reminder check periodically."""
    from app.services.scheduler_service import SchedulerService

    logger.info("Running scheduled reminder check")
    async with get_db_session() as db:
        service = SchedulerService(db)
        result = await service.run_reminder_check()
        logger.info(f"Reminder check result: {result}")


async def scheduled_eve_of_job_reminder():
    """Send evening-before reminders for tomorrow's confirmed jobs."""
    from app.services.scheduler_service import SchedulerService

    logger.info("Running scheduled eve-of-job reminder")
    async with get_db_session() as db:
        service = SchedulerService(db)
        result = await service.run_eve_of_job_reminder()
        logger.info(f"Eve-of-job reminder result: {result}")


async def scheduled_expiration_check():
    """Run expiration check periodically."""
    from app.services.scheduler_service import SchedulerService

    logger.info("Running scheduled expiration check")
    async with get_db_session() as db:
        service = SchedulerService(db)
        result = await service.run_expiration_check()
        logger.info(f"Expiration check result: {result}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management."""
    settings = get_settings()

    # Initialize database
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized")

    # Run migrations to add any missing columns
    logger.info("Running database migrations...")
    await run_startup_migrations()
    logger.info("Migrations complete")

    # Configure scheduled tasks
    logger.info("Configuring scheduled tasks...")

    # Batch delivery at 6 PM daily in configured timezone
    import pytz
    delivery_tz = pytz.timezone(settings.batch_delivery_timezone)
    scheduler.add_job(
        scheduled_batch_delivery,
        CronTrigger(
            hour=settings.batch_delivery_hour,
            minute=settings.batch_delivery_minute,
            timezone=delivery_tz,
        ),
        id="batch_delivery",
        replace_existing=True
    )

    # Reminder check every 2 hours
    scheduler.add_job(
        scheduled_reminder_check,
        CronTrigger(hour="*/2"),
        id="reminder_check",
        replace_existing=True
    )

    # Eve-of-job reminder at 7 PM daily in configured timezone
    scheduler.add_job(
        scheduled_eve_of_job_reminder,
        CronTrigger(
            hour=settings.eve_reminder_hour,
            minute=0,
            timezone=delivery_tz,
        ),
        id="eve_of_job_reminder",
        replace_existing=True
    )

    # Expiration check every hour
    scheduler.add_job(
        scheduled_expiration_check,
        CronTrigger(hour="*"),
        id="expiration_check",
        replace_existing=True
    )

    scheduler.start()
    logger.info("Scheduler started")

    yield

    # Shutdown
    logger.info("Shutting down scheduler...")
    scheduler.shutdown()

    from app.dependencies import shutdown_services
    await shutdown_services()
    logger.info("Application shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="Property Management AI System",
    description="""
    AI-Powered Property Management Communication System

    Unified intelligent communication hub for managing Airbnb properties,
    coordinating with cleaners, and handling guest requests across multiple
    channels (WhatsApp, Airbnb messages).

    ## Features
    - Automatic job assignment to best available cleaner
    - AI-powered message interpretation
    - Batch evening job delivery
    - Progressive reminders and escalation
    - Guest issue coordination
    """,
    version="1.0.0",
    lifespan=lifespan
)

# Add CORS middleware
_settings = get_settings()
_cors_origins = _settings.cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    settings = get_settings()
    detail = f"{type(exc).__name__}: {exc}" if settings.debug else "Internal server error"
    return JSONResponse(
        status_code=500,
        content={"detail": detail}
    )


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "version": "1.0.0"
    }


# Include routers
app.include_router(webhooks_router)
app.include_router(admin_router)
app.include_router(properties_router)
app.include_router(cleaners_router)
app.include_router(jobs_router)
app.include_router(guests_router)


# Root endpoint - serve dashboard
@app.get("/")
async def root():
    """Serve the dashboard UI with API key injected server-side."""
    settings = get_settings()
    template_path = Path(__file__).parent / "templates" / "dashboard.html"
    html = template_path.read_text()
    # Inject the API key so the dashboard can authenticate without user input
    html = html.replace(
        "const API_BASE = '';",
        f"const API_BASE = '';\n        const SERVER_API_KEY = '{settings.admin_api_key or ''}';"
    )
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


# API info endpoint
@app.get("/api")
async def api_info():
    """API information endpoint."""
    return {
        "name": "Property Management AI System",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
