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
            # Fallback: use keyword matching for common messages
            return self._keyword_fallback(message, pending_jobs)

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
            "cancel_job": "The cleaner is cancelling a job they previously accepted. Acknowledge the cancellation and let them know the job will be reassigned. Do NOT mention any job IDs or database IDs.",
            "need_time": "The cleaner needs time to decide. Acknowledge briefly and let them know to reply when ready. One short sentence only.",
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
        return """You are a property management scheduling assistant on WhatsApp messaging a cleaner.

STEP 1 — Read the conversation history and the cleaner's latest message. Understand what they want.
STEP 2 — Classify the intent based on what the cleaner is saying, NOT based on job state.
STEP 3 — Write a short reply. Direct, professional, no emojis, one to two sentences max.

INTENTS — pick the one that fits the MESSAGE:
- accept_job: Cleaner agrees / says yes ("yes", "sure", "I can do it", "ok I'll take it")
- reject_job: Cleaner declines or cancels ("can't", "no", "my schedule changed", "actually I cant")
- partial_accept: Cleaner accepts some jobs but rejects others from a batch
- need_time: Cleaner is undecided ("let me check", "I dont know yet", "maybe")
- question: Cleaner asks something ("what time?", "which property?", "how much?")
- status_update: Cleaner reports progress ("on my way", "here", "done", "finished")
- acknowledgment: Casual reply that needs no action ("cool", "thanks", "got it", "ok thank you")
- unclear: Cannot determine intent even with context

IMPORTANT:
- Classify based on what the cleaner MEANS, not on what jobs exist
- "I cant come" / "actually I cant" / "need to cancel" is ALWAYS reject_job, never acknowledgment
- "yes" / "sure" / "I can do it" is ALWAYS accept_job — the system will handle whether jobs exist
- For acknowledgment: set suggested_response to "" (empty)
- For accept_job: confirm the booking briefly. Do NOT ask them to confirm again
- For reject_job: acknowledge the cancellation briefly
- Do NOT mention job IDs or database details

PARTIAL ACCEPT — when cleaner picks specific jobs from a batch:
- "Only the 14", "Only feb 25", "Only the last one", "Just the first one" → partial_accept
- The cleaner OFTEN refers to jobs BY DATE ("the 26", "feb 25", "the 14th", "only the 12").
  You MUST look at the numbered job list, find which job matches that date, and return
  that job's POSITION NUMBER — NOT the date itself.
- accepted_jobs and rejected_jobs contain POSITION NUMBERS (1, 2, 3...), NEVER dates.
- All jobs not accepted must go in rejected_jobs. Never leave any out.
- ALWAYS set a suggested_response confirming which job is booked.
- Example: Jobs are "1. Villa on Feb 12" and "2. Lodge on Feb 26".
  Cleaner says "only the 26". Feb 26 = position 2 → accepted_jobs: [2], rejected_jobs: [1]
  Cleaner says "only the 12" → Feb 12 = position 1 → accepted_jobs: [1], rejected_jobs: [2]
  Cleaner says "only the last one" (last in list = 2) → accepted_jobs: [2], rejected_jobs: [1]
- If cleaner says "yes" AFTER previously saying "only X" in the conversation, treat as CONFIRMING
  the partial choice — classify as partial_accept, not accept_job

Respond with valid JSON:
{
    "intent": "accept_job|reject_job|partial_accept|need_time|question|status_update|acknowledgment|unclear",
    "confidence": 0-100,
    "suggested_response": "your reply to the cleaner (empty string for acknowledgment)",
    "accepted_jobs": [],
    "rejected_jobs": [],
    "question_type": "time|location|payment|other",
    "status": "en_route|arrived|started|completed",
    "needs_clarification": false,
    "clarification_question": ""
}"""

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
        """Format context for cleaner message interpretation.

        Conversation history comes first so the AI understands the flow.
        Job details come second as reference for generating responses.
        """
        parts = []

        # 1. Conversation history — the AI reads this to understand context
        if context.get("recent_messages"):
            parts.append("Conversation history:")
            for msg in context["recent_messages"][-6:]:
                direction = "System" if msg.get("direction") == "outbound" else "Cleaner"
                parts.append(f"  {direction}: {msg.get('content', '')[:150]}")

        # 2. The new message to interpret
        parts.append(f"\nCleaner's new message: \"{message}\"")

        # 3. Job info — for response generation, not for intent classification
        if pending_jobs:
            parts.append(f"\nJob offers awaiting response ({len(pending_jobs)}):")
            for i, job in enumerate(pending_jobs, 1):
                parts.append(f"  Position {i}: {job.get('property_name', 'Property')} on {job.get('date', 'TBD')} at {job.get('time', 'TBD')}")
            if len(pending_jobs) > 1:
                parts.append("  (Use position numbers 1, 2, ... in accepted_jobs/rejected_jobs — not dates)")

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

    def _keyword_fallback(
        self,
        message: str,
        pending_jobs: List[Dict[str, Any]]
    ) -> AIInterpretation:
        """Rule-based fallback when AI API call fails. Handles common messages."""
        import re
        msg = message.lower().strip()
        has_pending = len(pending_jobs) > 0
        words = set(re.findall(r'[a-z\']+', msg))

        def has_phrase(phrase):
            return phrase in msg

        def has_word(word):
            return word in words

        # Partial accept — "only" patterns with multiple pending jobs
        if has_word("only") and has_pending and len(pending_jobs) > 1:
            positions = [j.get("batch_position", i + 1) for i, j in enumerate(pending_jobs)]
            if has_phrase("last one") or has_phrase("the last"):
                last_pos = max(positions)
                return AIInterpretation(
                    intent="partial_accept", confidence=70,
                    accepted_job_ids=[last_pos],
                    rejected_job_ids=[p for p in positions if p != last_pos],
                    suggested_response="Confirmed for the last job. The other will be reassigned."
                )
            elif has_phrase("first one") or has_phrase("the first"):
                first_pos = min(positions)
                return AIInterpretation(
                    intent="partial_accept", confidence=70,
                    accepted_job_ids=[first_pos],
                    rejected_job_ids=[p for p in positions if p != first_pos],
                    suggested_response="Confirmed for the first job. The other will be reassigned."
                )
            else:
                # "only the 26" / "only feb 25" — try to match a number in the
                # message to a day-of-month in the pending jobs
                nums_in_msg = re.findall(r'\d+', msg)
                if nums_in_msg:
                    for num_str in nums_in_msg:
                        num = int(num_str)
                        for i, j in enumerate(pending_jobs):
                            date_str = j.get("date", "")
                            day_match = re.search(r'\b(\d{1,2})\b', date_str)
                            if day_match and int(day_match.group(1)) == num:
                                matched_pos = positions[i]
                                return AIInterpretation(
                                    intent="partial_accept", confidence=75,
                                    accepted_job_ids=[matched_pos],
                                    rejected_job_ids=[p for p in positions if p != matched_pos],
                                    suggested_response=f"Confirmed for the {date_str} job. The other will be reassigned."
                                )
                # Could not match — ask with numbers
                jobs_list = ", ".join(
                    f"{i + 1}) {j.get('property_name', 'Job')} on {j.get('date', 'TBD')}"
                    for i, j in enumerate(pending_jobs)
                )
                return AIInterpretation(
                    intent="unclear", confidence=40, needs_clarification=True,
                    suggested_response=f"Which job do you want? {jobs_list}. Reply with the number."
                )

        # Number-only reply — cleaner picking a job from a numbered list
        if msg.strip().isdigit() and has_pending and len(pending_jobs) > 1:
            num = int(msg.strip())
            positions = [j.get("batch_position", i + 1) for i, j in enumerate(pending_jobs)]
            if 1 <= num <= len(pending_jobs):
                accepted_pos = positions[num - 1]
                return AIInterpretation(
                    intent="partial_accept", confidence=75,
                    accepted_job_ids=[accepted_pos],
                    rejected_job_ids=[p for p in positions if p != accepted_pos],
                    suggested_response=f"Confirmed for job {num}. The other will be reassigned."
                )

        # Check rejection FIRST (before acceptance) — "no" / "cant" should reject
        reject_phrases = ["cant come", "can't come", "schedule changed",
                          "not available", "cant do", "can't do"]
        reject_single = ["no", "cant", "can't", "cannot", "wont", "won't",
                         "busy", "pass", "decline", "cancel"]
        is_reject = any(has_phrase(p) for p in reject_phrases) or any(has_word(w) for w in reject_single)

        # Check need_time BEFORE acceptance — "let me check" is not acceptance
        time_phrases = ["let me check", "not sure", "dont know", "don't know",
                        "let me think", "ill let you know", "i'll let you know",
                        "give me a minute", "give me a sec"]
        time_single = ["maybe", "thinking", "unsure"]
        is_need_time = any(has_phrase(p) for p in time_phrases) or any(has_word(w) for w in time_single)

        if is_need_time:
            return AIInterpretation(
                intent="need_time", confidence=70,
                suggested_response="No problem, let us know when you decide."
            )

        if is_reject:
            if has_pending:
                return AIInterpretation(
                    intent="reject_job", confidence=70,
                    suggested_response="Understood. Job will be reassigned."
                )
            else:
                return AIInterpretation(
                    intent="reject_job", confidence=70,
                    suggested_response="Understood, job cancelled. It will be reassigned."
                )

        # "all" with pending = accept all (response to "which jobs?")
        if has_pending and msg.strip() == "all":
            return AIInterpretation(
                intent="accept_job", confidence=75,
                suggested_response="Confirmed, you're booked for all jobs."
            )

        # Acceptance keywords — always classify as accept_job, handler decides
        # what to do (accept pending, reclaim rejected, or "no offers")
        accept_phrases = ["i can do", "can do it", "i can", "i will", "ill do",
                          "i'll do", "count me in"]
        accept_single = ["yes", "yeah", "yep", "yea", "sure", "confirm",
                         "confirmed", "accepted", "absolutely"]
        is_accept = any(has_phrase(p) for p in accept_phrases) or any(has_word(w) for w in accept_single)
        if is_accept:
            return AIInterpretation(
                intent="accept_job", confidence=70,
                suggested_response="Confirmed, you're booked. We'll send details before the job."
            )

        # "ok" / "okay" with pending jobs = acceptance
        if has_pending and (has_word("ok") or has_word("okay")):
            return AIInterpretation(
                intent="accept_job", confidence=60,
                suggested_response="Confirmed, you're booked. We'll send details before the job."
            )

        # Status update keywords
        if any(has_phrase(p) for p in ["on my way", "im here", "i'm here"]) or has_word("omw"):
            return AIInterpretation(
                intent="status_update", confidence=70, status_update="en_route",
                suggested_response="Noted, thank you."
            )
        if has_word("arrived") or (has_word("here") and len(words) <= 3):
            return AIInterpretation(
                intent="status_update", confidence=70, status_update="arrived",
                suggested_response="Noted, thank you."
            )
        if any(has_word(w) for w in ["done", "finished", "completed"]):
            return AIInterpretation(
                intent="status_update", confidence=70, status_update="completed",
                suggested_response="Noted, thank you."
            )

        # Acknowledgment keywords (no response needed)
        ack_words = ["cool", "thanks", "great", "perfect", "alright"]
        if any(has_word(w) for w in ack_words) or has_phrase("thank you") or has_phrase("sounds good") or has_phrase("got it"):
            return AIInterpretation(
                intent="acknowledgment", confidence=70,
                suggested_response=""
            )

        # Default: unclear (but only if we truly can't determine)
        if has_pending:
            return AIInterpretation(
                intent="unclear", confidence=0, needs_clarification=True,
                suggested_response="Can you confirm — can you take the job? Reply yes or no."
            )
        return AIInterpretation(
            intent="acknowledgment", confidence=30,
            suggested_response=""
        )

    def _get_fallback_response(self, intent: str) -> str:
        """Get fallback response when AI generation fails."""
        fallbacks = {
            "accept_job": "Confirmed, you're booked. We'll send details before the job.",
            "reject_job": "Understood. Job will be reassigned.",
            "cancel_job": "Understood, job cancelled. It will be reassigned.",
            "need_time": "No problem, let us know when you decide.",
            "partial_accept": "Noted. Assignments updated.",
            "question": "Checking on that. Will follow up shortly.",
            "status_update": "Noted, thank you.",
            "unclear": "Your message was unclear. Can you take the job? Please reply yes or no.",
            "acknowledgment": ""
        }
        return fallbacks.get(intent, "Message received.")
