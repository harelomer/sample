"""
Coordination Service for guest-cleaner coordination events.

Handles:
- Missing items and supplies
- Early/late check-in/checkout requests
- Cleaning issues
- Maintenance problems
"""

import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.coordination import CoordinationEvent, EventType, EventPriority, EventStatus
from app.models.job import Job, JobStatus
from app.models.guest import Guest
from app.models.cleaner import Cleaner
from app.models.property import Property
from app.services.messaging_service import MessagingService
from app.services.ai_service import AIService

logger = logging.getLogger(__name__)


class CoordinationService:
    """Service for guest-cleaner coordination."""

    def __init__(
        self,
        db: AsyncSession,
        messaging_service: Optional[MessagingService] = None,
        ai_service: Optional[AIService] = None
    ):
        """Initialize with dependencies."""
        self.db = db
        self.messaging = messaging_service or MessagingService()
        self.ai = ai_service or AIService()

    async def process_guest_message(
        self,
        guest_id: int,
        message: str
    ) -> Dict[str, Any]:
        """
        Process an incoming guest message and create appropriate coordination event.

        Args:
            guest_id: Guest ID
            message: Message content

        Returns:
            Processing result with created event and responses
        """
        # Get guest with property info
        result = await self.db.execute(
            select(Guest)
            .options(selectinload(Guest.rental_property))
            .where(Guest.id == guest_id)
        )
        guest = result.scalar_one_or_none()

        if not guest:
            return {"success": False, "error": "Guest not found"}

        # Use AI to categorize the message
        property_info = {
            "name": guest.rental_property.name,
            "address": guest.rental_property.address,
            "wifi_name": guest.rental_property.wifi_name,
            "wifi_password": guest.rental_property.wifi_password
        } if guest.rental_property else {}

        stay_info = {
            "check_in_date": guest.check_in_date.isoformat() if guest.check_in_date else None,
            "check_out_date": guest.check_out_date.isoformat() if guest.check_out_date else None,
            "check_in_time": guest.check_in_time,
            "check_out_time": guest.check_out_time
        }

        interpretation = await self.ai.interpret_guest_message(
            message=message,
            context={},
            property_info=property_info,
            stay_info=stay_info
        )

        # Create coordination event based on interpretation
        event = await self._create_event_from_interpretation(
            guest=guest,
            message=message,
            interpretation=interpretation
        )

        # Handle the event based on type
        response = await self._handle_event(event, guest, interpretation)

        return {
            "success": True,
            "event_id": event.id if event else None,
            "event_type": interpretation.get("issue_type"),
            "priority": interpretation.get("priority"),
            "guest_response": response.get("guest_message"),
            "cleaner_notified": response.get("cleaner_notified", False),
            "actions_taken": response.get("actions", [])
        }

    async def _create_event_from_interpretation(
        self,
        guest: Guest,
        message: str,
        interpretation: Dict[str, Any]
    ) -> Optional[CoordinationEvent]:
        """Create a coordination event from AI interpretation."""
        issue_type = interpretation.get("issue_type", "other")
        priority = interpretation.get("priority", "medium")

        # Map issue types to event types
        type_mapping = {
            "missing_item": EventType.MISSING_ITEM.value,
            "cleaning_issue": EventType.CLEANING_ISSUE.value,
            "maintenance": EventType.MAINTENANCE.value,
            "early_checkin": EventType.EARLY_CHECKIN.value,
            "late_checkout": EventType.LATE_CHECKOUT.value,
            "early_checkout": EventType.EARLY_CHECKOUT.value,
            "schedule_change": EventType.SCHEDULE_CHANGE.value,
            "supply_restock": EventType.SUPPLY_RESTOCK.value,
        }

        event_type = type_mapping.get(issue_type, EventType.OTHER.value)

        # Don't create events for general questions or positive feedback
        if issue_type in ["general_question", "positive_feedback"]:
            return None

        # Find related job (most recent or upcoming cleaning)
        job_result = await self.db.execute(
            select(Job)
            .where(
                and_(
                    Job.property_id == guest.property_id,
                    Job.scheduled_date >= datetime.utcnow().date()
                )
            )
            .order_by(Job.scheduled_date.asc())
        )
        related_job = job_result.scalars().first()

        event = CoordinationEvent(
            event_type=event_type,
            priority=priority,
            property_id=guest.property_id,
            guest_id=guest.id,
            job_id=related_job.id if related_job else None,
            assigned_cleaner_id=related_job.assigned_cleaner_id if related_job else None,
            title=self._generate_event_title(event_type, interpretation),
            description=message,
            ai_categorized=True,
            ai_suggested_action=interpretation.get("suggested_action"),
            ai_confidence=80,  # Default confidence
            extra_data={
                "extracted_items": interpretation.get("extracted_items", []),
                "original_message": message
            }
        )

        self.db.add(event)
        await self.db.flush()

        logger.info(f"Created coordination event {event.id} for guest {guest.id}")
        return event

    def _generate_event_title(
        self,
        event_type: str,
        interpretation: Dict[str, Any]
    ) -> str:
        """Generate a title for the event."""
        items = interpretation.get("extracted_items", [])

        titles = {
            EventType.MISSING_ITEM.value: f"Missing: {', '.join(items)}" if items else "Missing item reported",
            EventType.CLEANING_ISSUE.value: "Cleaning issue reported",
            EventType.MAINTENANCE.value: f"Maintenance: {items[0]}" if items else "Maintenance issue",
            EventType.EARLY_CHECKIN.value: "Early check-in request",
            EventType.LATE_CHECKOUT.value: "Late checkout request",
            EventType.EARLY_CHECKOUT.value: "Guest checking out early",
            EventType.SUPPLY_RESTOCK.value: f"Restock needed: {', '.join(items)}" if items else "Supplies needed",
            EventType.SCHEDULE_CHANGE.value: "Schedule change request",
        }

        return titles.get(event_type, "Guest request")

    async def _handle_event(
        self,
        event: Optional[CoordinationEvent],
        guest: Guest,
        interpretation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Handle the coordination event appropriately.

        Args:
            event: Created event (may be None)
            guest: Guest model
            interpretation: AI interpretation

        Returns:
            Handling result
        """
        actions = []
        cleaner_notified = False
        guest_message = interpretation.get("suggested_response", "Thank you for letting us know.")

        if not event:
            # Just respond to guest, no event needed
            await self.messaging.send_to_guest(guest, guest_message)
            return {
                "guest_message": guest_message,
                "cleaner_notified": False,
                "actions": ["Responded to guest"]
            }

        # Handle based on event type
        if event.event_type == EventType.MISSING_ITEM.value:
            result = await self._handle_missing_item(event, guest, interpretation)
            cleaner_notified = result.get("cleaner_notified", False)
            actions.extend(result.get("actions", []))

        elif event.event_type == EventType.EARLY_CHECKIN.value:
            result = await self._handle_early_checkin(event, guest, interpretation)
            cleaner_notified = result.get("cleaner_notified", False)
            actions.extend(result.get("actions", []))

        elif event.event_type == EventType.EARLY_CHECKOUT.value:
            result = await self._handle_early_checkout(event, guest, interpretation)
            cleaner_notified = result.get("cleaner_notified", False)
            actions.extend(result.get("actions", []))

        elif event.event_type == EventType.MAINTENANCE.value:
            result = await self._handle_maintenance(event, guest, interpretation)
            actions.extend(result.get("actions", []))

        else:
            # Default handling - notify and respond
            await self.messaging.send_to_guest(guest, guest_message)
            actions.append("Responded to guest")

        event.guest_notified = True

        return {
            "guest_message": guest_message,
            "cleaner_notified": cleaner_notified,
            "actions": actions
        }

    async def _handle_missing_item(
        self,
        event: CoordinationEvent,
        guest: Guest,
        interpretation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle missing item report."""
        actions = []
        cleaner_notified = False

        items = interpretation.get("extracted_items", ["items"])
        items_str = ", ".join(items)

        # Get the cleaner who just cleaned (if any)
        if event.assigned_cleaner_id:
            cleaner_result = await self.db.execute(
                select(Cleaner).where(Cleaner.id == event.assigned_cleaner_id)
            )
            cleaner = cleaner_result.scalar_one_or_none()

            if cleaner:
                # Message cleaner
                property_name = guest.rental_property.short_name if guest.rental_property else "the property"
                cleaner_message = (
                    f"Hi! Guest at {property_name} needs {items_str}. "
                    f"Can you drop it off?"
                )
                await self.messaging.send_to_cleaner(cleaner, cleaner_message)
                event.cleaner_notified = True
                cleaner_notified = True
                actions.append(f"Notified cleaner about missing {items_str}")

        # Respond to guest
        guest_message = f"I've asked the cleaner to bring {items_str}. Will update you shortly!"
        await self.messaging.send_to_guest(guest, guest_message)
        actions.append("Responded to guest")

        event.status = EventStatus.IN_PROGRESS.value

        return {
            "cleaner_notified": cleaner_notified,
            "actions": actions
        }

    async def _handle_early_checkin(
        self,
        event: CoordinationEvent,
        guest: Guest,
        interpretation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle early check-in request."""
        actions = []
        cleaner_notified = False

        # Find the cleaning job for this guest's check-in
        job_result = await self.db.execute(
            select(Job)
            .options(selectinload(Job.assigned_cleaner))
            .where(
                and_(
                    Job.property_id == guest.property_id,
                    Job.guest_id == guest.id
                )
            )
        )
        job = job_result.scalars().first()

        if job and job.assigned_cleaner:
            # Ask cleaner about timing
            cleaner_message = (
                f"Guest wants early check-in. Can you confirm you'll be done in time? "
                f"Scheduled: {job.scheduled_time or 'TBD'}"
            )
            await self.messaging.send_to_cleaner(job.assigned_cleaner, cleaner_message)
            cleaner_notified = True
            actions.append("Asked cleaner about timing")

            # Respond to guest that we're checking
            guest_message = "Let me check with the cleaning team and get back to you!"
            event.status = EventStatus.WAITING_RESPONSE.value
        else:
            guest_message = "I'll check if early check-in is possible and let you know!"
            event.status = EventStatus.IN_PROGRESS.value
            actions.append("No cleaner assigned yet")

        await self.messaging.send_to_guest(guest, guest_message)
        actions.append("Responded to guest")

        return {
            "cleaner_notified": cleaner_notified,
            "actions": actions
        }

    async def _handle_early_checkout(
        self,
        event: CoordinationEvent,
        guest: Guest,
        interpretation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle early checkout notification."""
        actions = []
        cleaner_notified = False

        # Find upcoming cleaning job
        job_result = await self.db.execute(
            select(Job)
            .options(selectinload(Job.assigned_cleaner))
            .where(
                and_(
                    Job.property_id == guest.property_id,
                    Job.scheduled_date >= datetime.utcnow().date()
                )
            )
            .order_by(Job.scheduled_date.asc())
        )
        job = job_result.scalars().first()

        if job and job.assigned_cleaner:
            # Notify cleaner they can start early
            property_name = guest.rental_property.short_name if guest.rental_property else "the property"
            cleaner_message = (
                f"Guest at {property_name} left early. "
                f"You can start anytime now instead of waiting until {job.scheduled_time or 'later'}."
            )
            await self.messaging.send_to_cleaner(job.assigned_cleaner, cleaner_message)
            cleaner_notified = True
            actions.append("Notified cleaner about early access")

        # Respond to guest
        guest_message = "Thanks for letting us know! I'll update the cleaner."
        await self.messaging.send_to_guest(guest, guest_message)
        actions.append("Responded to guest")

        event.status = EventStatus.RESOLVED.value
        event.resolved_at = datetime.utcnow()
        event.resolution_outcome = "acknowledged"

        return {
            "cleaner_notified": cleaner_notified,
            "actions": actions
        }

    async def _handle_maintenance(
        self,
        event: CoordinationEvent,
        guest: Guest,
        interpretation: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle maintenance issue."""
        actions = []

        priority = interpretation.get("priority", "medium")
        items = interpretation.get("extracted_items", ["the issue"])

        # High priority issues need immediate attention
        if priority in ["urgent", "high"]:
            guest_message = f"I'm looking into {items[0] if items else 'this'} right away. Will get back to you shortly."
            actions.append("Flagged for immediate attention")
        else:
            guest_message = "Thanks for reporting this. I'll look into it and let you know."
            actions.append("Logged maintenance issue")

        await self.messaging.send_to_guest(guest, guest_message)
        actions.append("Responded to guest")

        event.status = EventStatus.ASSIGNED.value

        return {"actions": actions}

    async def resolve_event(
        self,
        event_id: int,
        outcome: str,
        notes: Optional[str] = None,
        notify_guest: bool = False,
        guest_message: Optional[str] = None
    ) -> CoordinationEvent:
        """
        Resolve a coordination event.

        Args:
            event_id: Event ID
            outcome: Resolution outcome (approved, denied, modified, resolved)
            notes: Resolution notes
            notify_guest: Whether to notify guest
            guest_message: Custom message for guest

        Returns:
            Updated event
        """
        result = await self.db.execute(
            select(CoordinationEvent)
            .options(selectinload(CoordinationEvent.guest))
            .where(CoordinationEvent.id == event_id)
        )
        event = result.scalar_one_or_none()

        if not event:
            raise ValueError(f"Event {event_id} not found")

        event.resolve(outcome, notes)

        if notify_guest and event.guest:
            message = guest_message or f"Update: Your request has been {outcome}."
            await self.messaging.send_to_guest(event.guest, message)
            event.guest_notified = True

        logger.info(f"Resolved event {event_id} with outcome: {outcome}")
        return event

    async def get_open_events(
        self,
        property_id: Optional[int] = None,
        priority: Optional[str] = None
    ) -> List[CoordinationEvent]:
        """Get open coordination events."""
        query = select(CoordinationEvent).where(
            CoordinationEvent.status.in_([
                EventStatus.OPEN.value,
                EventStatus.ASSIGNED.value,
                EventStatus.IN_PROGRESS.value,
                EventStatus.WAITING_RESPONSE.value
            ])
        )

        if property_id:
            query = query.where(CoordinationEvent.property_id == property_id)
        if priority:
            query = query.where(CoordinationEvent.priority == priority)

        query = query.order_by(
            CoordinationEvent.priority.desc(),
            CoordinationEvent.created_at.asc()
        )

        result = await self.db.execute(query)
        return list(result.scalars().all())
