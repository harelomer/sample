"""
Messaging Service for sending messages via WhatsApp (Green API) and Airbnb.

Handles all outbound communication to cleaners and guests.
"""

import logging
from typing import Optional, Dict, Any
from datetime import datetime
import httpx

from app.config import get_settings
from app.models.message import MessageDirection, MessageChannel, SenderType

logger = logging.getLogger(__name__)


class MessagingService:
    """Service for sending messages across different channels."""

    def __init__(self):
        """Initialize the messaging service."""
        self.settings = get_settings()
        self.http_client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        """Close the HTTP client."""
        await self.http_client.aclose()

    # WhatsApp (Green API) Methods

    async def send_whatsapp_message(
        self,
        chat_id: str,
        message: str
    ) -> Dict[str, Any]:
        """
        Send a WhatsApp message via Green API.

        Args:
            chat_id: WhatsApp chat ID (format: 15551234567@c.us)
            message: Message text to send

        Returns:
            API response with message ID
        """
        url = (
            f"{self.settings.green_api_host}/"
            f"waInstance{self.settings.green_api_instance_id}/"
            f"sendMessage/{self.settings.green_api_token}"
        )

        payload = {
            "chatId": chat_id,
            "message": message
        }

        try:
            response = await self.http_client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
            logger.info(f"WhatsApp message sent to {chat_id}: {result.get('idMessage')}")
            return {
                "success": True,
                "message_id": result.get("idMessage"),
                "channel": "whatsapp"
            }
        except httpx.HTTPError as e:
            logger.error(f"Error sending WhatsApp message: {e}")
            return {
                "success": False,
                "error": str(e),
                "channel": "whatsapp"
            }

    async def send_whatsapp_to_phone(
        self,
        phone: str,
        message: str
    ) -> Dict[str, Any]:
        """
        Send a WhatsApp message to a phone number.

        Args:
            phone: Phone number in E.164 format (+15551234567)
            message: Message text to send

        Returns:
            API response
        """
        # Convert E.164 to WhatsApp chat ID
        # Remove + and add @c.us
        phone_digits = phone.lstrip("+")
        chat_id = f"{phone_digits}@c.us"
        return await self.send_whatsapp_message(chat_id, message)

    # Airbnb Messaging Methods

    async def send_airbnb_message(
        self,
        thread_id: str,
        message: str
    ) -> Dict[str, Any]:
        """
        Send a message via Airbnb messaging API.

        Args:
            thread_id: Airbnb conversation thread ID
            message: Message text to send

        Returns:
            API response

        Note: Airbnb API specifics vary. This is a placeholder implementation.
        """
        # TODO: Implement actual Airbnb API integration
        # This requires Airbnb API credentials and proper OAuth setup
        logger.warning("Airbnb messaging not fully implemented")

        # Placeholder - in production, this would call the Airbnb API
        return {
            "success": False,
            "error": "Airbnb API not configured",
            "channel": "airbnb"
        }

    # Unified Messaging Methods

    async def send_to_cleaner(
        self,
        cleaner: Any,  # Cleaner model
        message: str
    ) -> Dict[str, Any]:
        """
        Send a message to a cleaner via their preferred channel.

        Args:
            cleaner: Cleaner model instance
            message: Message to send

        Returns:
            Send result
        """
        if cleaner.preferred_channel == "whatsapp" and cleaner.whatsapp_chat_id:
            return await self.send_whatsapp_message(cleaner.whatsapp_chat_id, message)
        elif cleaner.preferred_channel == "whatsapp" and cleaner.phone:
            return await self.send_whatsapp_to_phone(cleaner.phone, message)
        elif cleaner.preferred_channel == "airbnb" and cleaner.airbnb_user_id:
            return await self.send_airbnb_message(cleaner.airbnb_user_id, message)
        else:
            logger.error(f"No valid contact method for cleaner {cleaner.id}")
            return {
                "success": False,
                "error": "No valid contact method",
                "channel": None
            }

    async def send_to_guest(
        self,
        guest: Any,  # Guest model
        message: str
    ) -> Dict[str, Any]:
        """
        Send a message to a guest via available channel.

        Args:
            guest: Guest model instance
            message: Message to send

        Returns:
            Send result
        """
        # Prefer Airbnb for guest communication
        if guest.airbnb_thread_id:
            return await self.send_airbnb_message(guest.airbnb_thread_id, message)
        elif guest.whatsapp_chat_id:
            return await self.send_whatsapp_message(guest.whatsapp_chat_id, message)
        elif guest.phone:
            return await self.send_whatsapp_to_phone(guest.phone, message)
        else:
            logger.error(f"No valid contact method for guest {guest.id}")
            return {
                "success": False,
                "error": "No valid contact method",
                "channel": None
            }

    # Job Offer Messaging

    async def send_job_offer(
        self,
        cleaner: Any,
        job_message: str,
        job_ids: list
    ) -> Dict[str, Any]:
        """
        Send a job offer to a cleaner.

        Args:
            cleaner: Cleaner model instance
            job_message: Formatted job offer message
            job_ids: List of job IDs in this offer

        Returns:
            Send result with job tracking info
        """
        result = await self.send_to_cleaner(cleaner, job_message)
        result["job_ids"] = job_ids
        result["offer_type"] = "batch" if len(job_ids) > 1 else "single"
        return result

    async def send_job_confirmation(
        self,
        cleaner: Any,
        jobs: list,
        message: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send job confirmation to cleaner.

        Args:
            cleaner: Cleaner model instance
            jobs: List of confirmed jobs
            message: Optional custom message

        Returns:
            Send result
        """
        if not message:
            if len(jobs) == 1:
                job = jobs[0]
                message = f"Confirmed! {job.rental_property.short_name} on {job.scheduled_date.strftime('%A')} at {job.scheduled_time}."
            else:
                job_list = ", ".join(
                    f"{j.property.short_name} {j.scheduled_date.strftime('%a')}"
                    for j in jobs
                )
                message = f"Confirmed! You're set for: {job_list}"

        return await self.send_to_cleaner(cleaner, message)

    async def send_reminder(
        self,
        cleaner: Any,
        jobs: list,
        reminder_number: int = 1
    ) -> Dict[str, Any]:
        """
        Send a reminder about pending job offers using AI for natural tone.

        Args:
            cleaner: Cleaner model instance
            jobs: Jobs awaiting response
            reminder_number: Which reminder this is (1st, 2nd, etc.)

        Returns:
            Send result
        """
        from app.services.ai_service import AIService

        ai_service = AIService()
        cleaner_name = cleaner.name.split()[0] if cleaner.name else "there"

        if len(jobs) == 1:
            job = jobs[0]
            message = await ai_service.generate_conversational_message(
                "reminder",
                {
                    "cleaner_name": cleaner_name,
                    "property_name": job.rental_property.short_name if job.rental_property else "the property",
                    "date": job.scheduled_date.strftime('%A') if job.scheduled_date else "soon",
                }
            )
        else:
            message = await ai_service.generate_conversational_message(
                "reminder_batch",
                {
                    "cleaner_name": cleaner_name,
                    "job_count": len(jobs),
                }
            )

        return await self.send_to_cleaner(cleaner, message)

    # Guest Communication

    async def notify_guest_cleaning_status(
        self,
        guest: Any,
        status: str,
        details: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Notify guest about cleaning status.

        Args:
            guest: Guest model instance
            status: Status update type
            details: Additional details

        Returns:
            Send result
        """
        messages = {
            "started": "The cleaner has started preparing your place!",
            "almost_done": "Almost ready for you! The cleaner is finishing up.",
            "completed": "Your place is ready! See you soon.",
            "delayed": f"Quick update: {details}" if details else "The cleaning is running a bit behind."
        }

        message = messages.get(status, details or "Update on your place.")
        return await self.send_to_guest(guest, message)

    async def notify_guest_issue_update(
        self,
        guest: Any,
        issue_type: str,
        resolution: str
    ) -> Dict[str, Any]:
        """
        Notify guest about issue resolution.

        Args:
            guest: Guest model instance
            issue_type: Type of issue
            resolution: Resolution message

        Returns:
            Send result
        """
        return await self.send_to_guest(guest, resolution)
