"""
Application configuration and settings.
"""

from pydantic_settings import BaseSettings
from typing import List, Optional
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    app_name: str = "Property Management AI System"
    app_version: str = "1.0.0"
    debug: bool = False

    # Security
    admin_api_key: str = ""  # Required for admin endpoints; set via ADMIN_API_KEY env var
    webhook_secret: str = ""  # Shared secret for webhook validation; set via WEBHOOK_SECRET env var
    cors_origins: List[str] = ["http://localhost:3000", "http://localhost:8000"]

    # Rate limiting
    rate_limit_per_minute: int = 60

    # Database
    database_url: str = "sqlite+aiosqlite:///./property_management.db"

    # OpenAI API
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_max_tokens: int = 1024

    # WhatsApp - Green API
    green_api_instance_id: str = ""
    green_api_token: str = ""
    green_api_host: str = "https://api.green-api.com"

    # Airbnb API (placeholder - specific endpoints vary)
    airbnb_api_key: Optional[str] = None
    airbnb_api_secret: Optional[str] = None
    airbnb_webhook_secret: Optional[str] = None

    # Scheduling
    batch_delivery_hour: int = 18  # 6 PM for batch job delivery
    batch_delivery_minute: int = 0
    batch_delivery_timezone: str = "America/Los_Angeles"  # Property-local timezone
    default_response_timeout_hours: int = 24
    urgent_response_timeout_hours: int = 2
    reminder_interval_hours: int = 2
    eve_reminder_hour: int = 19  # 7 PM for evening-before job reminders

    # Job assignment
    max_assignment_attempts: int = 5
    cleaner_ranking_weights: dict = {
        "property_familiarity": 0.4,
        "availability_score": 0.3,
        "response_rate": 0.2,
        "rating": 0.1
    }

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
