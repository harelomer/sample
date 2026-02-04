"""
Application configuration and settings.
"""

from pydantic_settings import BaseSettings
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    app_name: str = "Property Management AI System"
    app_version: str = "1.0.0"
    debug: bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///./property_management.db"

    # Anthropic Claude API
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"
    claude_max_tokens: int = 1024

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
    default_response_timeout_hours: int = 24
    urgent_response_timeout_hours: int = 2
    reminder_interval_hours: int = 2

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
