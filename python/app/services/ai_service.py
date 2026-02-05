"""
AI Service using OpenAI API for message interpretation.

This service handles all AI-powered message interpretation, including:
- Understanding cleaner responses (accept, reject, partial)
- Categorizing guest issues
- Generating appropriate responses
"""

import json
import logging
from typing import Optional, Dict, Any, List
from openai import AsyncOpenAI

from app.config import get_settings
from app.schemas.message import AIInterpretation

logger = logging.getLogger(__name__)


class AIService:
    """Service for AI-powered message interpretation using OpenAI."""

    def __init__(self):
        """Initialize the AI service with OpenAI client."""
        self.settings = get_settings()
        self.client = AsyncOpenAI(api_key=self.settings.openai_api_key)
        self.model = self.settings.openai_model
        self.max_tokens = self.settings.openai_max_tokens

    async def interpret_cleaner_message(
        self,
        message: str,
        context: Dict[str, Any],
        pending_jobs: List[Dict[str, Any]]
    ) -> AIInterpretation:
        """
        Interpret a cleaner's message using conversation context.

        Args:
            message: The cleaner's message text
            context: Conversation context including recent messages
            pending_jobs: List of jobs currently offered to this cleaner

        Returns:
            AIInterpretation with intent, extracted data, and suggested response
        """
        system_prompt = self._get_cleaner_interpretation_prompt()
        user_prompt = self._format_cleaner_context(message, context, pending_jobs)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )

            # Parse the JSON response
            result = self._parse_ai_response(response.choices[0].message.content)
            return self._create_interpretation(result)

        except Exception as e:
            logger.error(f"Error interpreting cleaner message: {e}")
            # Return unclear interpretation on error
            return AIInterpretation(
                intent="unclear",
                confidence=0,
                needs_clarification=True,
                clarification_question="Your message was unclear. Can you take the job? Please reply yes or no.",
                suggested_response="Your message was unclear. Can you take the job? Please reply yes or no."
            )

    async def interpret_guest_message(
        self,
        message: str,
        context: Dict[str, Any],
        property_info: Dict[str, Any],
        stay_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Interpret a guest's message and categorize the issue.

        Args:
            message: The guest's message text
            context: Conversation context
            property_info: Information about the property
            stay_info: Guest's stay details

        Returns:
            Dictionary with issue categorization and suggested actions
        """
        system_prompt = self._get_guest_interpretation_prompt()
        user_prompt = self._format_guest_context(message, context, property_info, stay_info)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )

            return self._parse_ai_response(response.choices[0].message.content)

        except Exception as e:
            logger.error(f"Error interpreting guest message: {e}")
            return {
                "issue_type": "unknown",
                "priority": "medium",
                "needs_cleaner": False,
                "suggested_response": "Thank you for letting us know. I'll look into this right away.",
                "suggested_action": "notify_manager"
            }

    async def generate_job_offer_message(
        self,
        jobs: List[Dict[str, Any]],
        cleaner_name: str,
        is_batch: bool = False
    ) -> str:
        """
        Generate a natural job offer message for a cleaner using AI.

        Args:
            jobs: List of job details
            cleaner_name: Cleaner's first name
            is_batch: Whether this is a batch of multiple jobs

        Returns:
            Natural-sounding message string
        """
        if not jobs:
            return ""

        system_prompt = """You are a property management scheduling system messaging a cleaner on WhatsApp.
Be direct, clear, and professional. No small talk, no filler.
No emojis. No markdown. No bullet points or numbered lists.
Keep it short. Always include the key details: property name, date, time, and pay.
Address the cleaner by name and ask clearly if they can take the job."""

        if len(jobs) == 1 and not is_batch:
            job = jobs[0]
            user_prompt = (
                f"Text {cleaner_name} about a cleaning job:\n"
                f"Property: {job['property_name']}\n"
                f"Date: {job['date']}\n"
                f"Time: {job['time']}\n"
                f"Pay: ${job['amount']}\n"
                f"Ask if they can take it."
            )
        else:
            jobs_info = "\n".join(
                f"- {job['property_name']} on {job['date']} at {job['time']} for ${job['amount']}"
                for job in jobs
            )
            user_prompt = (
                f"Text {cleaner_name} about {len(jobs)} cleaning jobs available:\n"
                f"{jobs_info}\n"
                f"Ask which ones they can take."
            )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=256,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Error generating job offer message: {e}")
            # Fallback to simple template
            if len(jobs) == 1:
                job = jobs[0]
                return (
                    f"{cleaner_name}, cleaning job available: {job['property_name']} on "
                    f"{job['date']} at {job['time']}. Pay: ${job['amount']}. Can you take it?"
                )
            lines = [f"{cleaner_name}, {len(jobs)} cleaning jobs available:"]
            for job in jobs:
                lines.append(f"{job['property_name']} - {job['date']} {job['time']} ${job['amount']}")
            lines.append("Which ones can you take?")
            return "\n".join(lines)

    async def generate_conversational_message(
        self,
        message_type: str,
        data: Dict[str, Any]
    ) -> str:
        """
        Generate a natural conversational message for any scenario.

        Args:
            message_type: Type of message (multi_job_confirm, reminder, etc.)
            data: Context data for the message

        Returns:
            Natural-sounding message
        """
        system_prompt = """You are a property management scheduling system messaging a cleaner directly on WhatsApp.
Be direct, clear, and professional. No small talk, no filler, no greetings like "Hey! Hope you're doing well".
No emojis. No markdown. No numbered lists.
You are writing TO the cleaner. Never refer to them in third person."""

        prompts = {
            "multi_job_confirm": (
                f"Ask {data.get('cleaner_name', 'the cleaner')} directly to confirm: "
                f"they said yes to {data.get('job_count', 'multiple')} jobs. "
                f"The jobs are:\n{data.get('jobs_description', '')}\n"
                f"Ask them to confirm if they want all of them or which specific ones."
            ),
            "reminder": (
                f"Send {data.get('cleaner_name', 'the cleaner')} a reminder about "
                f"the cleaning job at {data.get('property_name', 'a property')} "
                f"on {data.get('date', 'soon')}. Ask if they can confirm."
            ),
            "reminder_batch": (
                f"Send {data.get('cleaner_name', 'the cleaner')} a reminder about "
                f"{data.get('job_count', 'some')} jobs that need a response. "
                f"Ask them to reply."
            ),
            "clarification": (
                f"{data.get('cleaner_name', 'Hi')}, your last message was unclear: "
                f"\"{data.get('original_message', '')}\"\n"
                f"Ask them directly: can you take the job? Yes or no."
            ),
        }

        user_prompt = prompts.get(message_type, f"Generate a message about: {data}")

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=256,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Error generating conversational message: {e}")
            return self._get_fallback_conversational(message_type, data)

    def _get_fallback_conversational(self, message_type: str, data: Dict[str, Any]) -> str:
        """Fallback messages when AI generation fails."""
        fallbacks = {
            "multi_job_confirm": (
                f"Please confirm: do you want all "
                f"{data.get('job_count', 'the')} jobs, or just some of them? "
                f"Reply with which ones you can take."
            ),
            "reminder": (
                f"Reminder: {data.get('property_name', 'cleaning job')} "
                f"on {data.get('date', 'the scheduled date')}. Can you confirm?"
            ),
            "reminder_batch": (
                f"Still need your response on {data.get('job_count', 'the')} "
                f"jobs sent earlier. Please reply."
            ),
            "clarification": (
                "Your last message was unclear. Can you take the job? Please reply yes or no."
            ),
        }
        return fallbacks.get(message_type, "Please reply to confirm.")

    async def generate_response(
        self,
        context: str,
        intent: str,
        data: Dict[str, Any]
    ) -> str:
        """
        Generate an appropriate response based on interpretation.

        Args:
            context: The conversation context
            intent: The interpreted intent
            data: Additional data for response generation

        Returns:
            Generated response message
        """
        system_prompt = """You are a property management scheduling system messaging a cleaner directly on WhatsApp.
Be direct, clear, and professional. No small talk, no filler.
No emojis. No markdown. Keep it short.
You are writing TO the cleaner. Never refer to them in third person."""

        prompts = {
            "accept_job": "The cleaner already said yes. Confirm they are booked. Do NOT ask them to confirm again.",
            "reject_job": "The cleaner declined the job. Acknowledge briefly and let them know it will be reassigned.",
            "partial_accept": "The cleaner accepted some jobs and declined others. Confirm which are booked and note the rest will be reassigned.",
            "question": "Answer the cleaner's question based on context.",
            "status_update": "Acknowledge the status update briefly.",
            "unclear": "Ask the cleaner to clarify: can they take the job? Yes or no."
        }

        user_prompt = f"""Context: {context}
Intent: {intent}
Data: {json.dumps(data)}

{prompts.get(intent, 'Generate an appropriate response.')}"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                max_tokens=256,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Error generating response: {e}")
            return self._get_fallback_response(intent)

    def _get_cleaner_interpretation_prompt(self) -> str:
        """Get the system prompt for cleaner message interpretation."""
        return """You are an AI assistant interpreting messages from cleaners in a property management system.

Your task is to interpret cleaner responses to job offers. Cleaners often respond briefly and informally.

Common response patterns:
- Acceptance: "yes", "sure", "I can do it", "confirmed", "👍" (ONLY when responding to a job offer)
- Rejection: "can't", "no", "not available", "busy", "pass"
- Partial (for batches): "only 1 and 3", "just the oakland one", "all except friday"
- Questions: "what time?", "which property?", "how much?", "what job?"
- Status updates: "on my way", "here", "started", "done", "finished"
- Acknowledgment: "cool", "thanks", "ok thanks", "thank you", "sounds good", "great", "got it", "its ok thank you"

IMPORTANT: If there are NO pending job offers listed, casual or positive messages like "cool", "thanks", "ok", "sounds good" are acknowledgments, NOT job acceptances.

You must respond with valid JSON in this exact format:
{
    "intent": "accept_job|reject_job|partial_accept|question|status_update|acknowledgment|unclear",
    "confidence": 0-100,
    "accepted_jobs": [list of job numbers/ids if partial],
    "rejected_jobs": [list of job numbers/ids if partial],
    "question_type": "time|location|payment|other" (if question),
    "status": "en_route|arrived|started|completed" (if status update),
    "needs_clarification": true/false,
    "clarification_question": "question to ask if unclear"
}

Always consider the conversation context to understand references like "it", "that one", "the first one"."""

    def _get_guest_interpretation_prompt(self) -> str:
        """Get the system prompt for guest message interpretation."""
        return """You are an AI assistant categorizing guest messages for a property management system.

Categorize guest messages into:
- missing_item: Something missing (soap, towels, etc.)
- cleaning_issue: Problem with cleanliness
- maintenance: Something broken (WiFi, AC, appliances)
- early_checkin: Request to check in early
- late_checkout: Request to check out late
- early_checkout: Notifying of early departure
- schedule_change: Other schedule modifications
- general_question: Questions about property
- positive_feedback: Compliments
- other: Anything else

Determine priority:
- urgent: Safety issues, no hot water, no AC in extreme weather
- high: WiFi down, missing essentials, major cleaning issues
- medium: Missing non-essentials, minor cleaning issues
- low: General questions, positive feedback

Respond with valid JSON:
{
    "issue_type": "category from above",
    "priority": "urgent|high|medium|low",
    "needs_cleaner": true/false,
    "needs_manager": true/false,
    "suggested_response": "response to guest",
    "suggested_action": "action to take",
    "extracted_items": ["list of mentioned items if applicable"]
}"""

    def _format_cleaner_context(
        self,
        message: str,
        context: Dict[str, Any],
        pending_jobs: List[Dict[str, Any]]
    ) -> str:
        """Format context for cleaner message interpretation."""
        parts = [f"Cleaner's message: \"{message}\""]

        if context.get("last_outbound_message"):
            parts.append(f"\nLast message sent to cleaner: \"{context['last_outbound_message']}\"")

        if pending_jobs:
            parts.append("\nPending job offers:")
            for i, job in enumerate(pending_jobs, 1):
                parts.append(f"  {i}. {job.get('property_name', 'Property')} on {job.get('date', 'TBD')} at {job.get('time', 'TBD')}")

        if context.get("recent_messages"):
            parts.append("\nRecent conversation:")
            for msg in context["recent_messages"][-5:]:
                direction = "→" if msg.get("direction") == "outbound" else "←"
                parts.append(f"  {direction} {msg.get('content', '')[:100]}")

        return "\n".join(parts)

    def _format_guest_context(
        self,
        message: str,
        context: Dict[str, Any],
        property_info: Dict[str, Any],
        stay_info: Dict[str, Any]
    ) -> str:
        """Format context for guest message interpretation."""
        parts = [f"Guest's message: \"{message}\""]

        if property_info:
            parts.append(f"\nProperty: {property_info.get('name', 'Unknown')}")
            parts.append(f"Address: {property_info.get('address', 'Unknown')}")

        if stay_info:
            parts.append(f"\nCheck-in: {stay_info.get('check_in_date', 'Unknown')}")
            parts.append(f"Check-out: {stay_info.get('check_out_date', 'Unknown')}")

        return "\n".join(parts)

    def _parse_ai_response(self, response_text: str) -> Dict[str, Any]:
        """Parse JSON response from AI."""
        try:
            # Try to extract JSON from the response
            text = response_text.strip()

            # Handle markdown code blocks
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0]
            elif "```" in text:
                text = text.split("```")[1].split("```")[0]

            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"Could not parse AI response as JSON: {response_text[:200]}")
            return {"intent": "unclear", "confidence": 0}

    def _create_interpretation(self, result: Dict[str, Any]) -> AIInterpretation:
        """Create AIInterpretation from parsed result."""
        return AIInterpretation(
            intent=result.get("intent", "unclear"),
            confidence=result.get("confidence", 0),
            accepted_job_ids=result.get("accepted_jobs", []),
            rejected_job_ids=result.get("rejected_jobs", []),
            question_type=result.get("question_type"),
            question_about=result.get("question_about"),
            status_update=result.get("status"),
            needs_clarification=result.get("needs_clarification", False),
            clarification_question=result.get("clarification_question"),
            suggested_response=result.get("suggested_response", "")
        )

    def _get_fallback_response(self, intent: str) -> str:
        """Get fallback response when AI generation fails."""
        fallbacks = {
            "accept_job": "Confirmed, you're booked. We'll send details before the job.",
            "reject_job": "Understood. Job will be reassigned.",
            "partial_accept": "Noted. Assignments updated.",
            "question": "Checking on that. Will follow up shortly.",
            "status_update": "Noted, thank you.",
            "unclear": "Your message was unclear. Can you take the job? Please reply yes or no.",
            "acknowledgment": ""
        }
        return fallbacks.get(intent, "Message received.")
