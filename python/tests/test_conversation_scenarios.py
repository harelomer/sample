"""
Comprehensive test suite for WhatsApp cleaner conversation handling.

Tests all paths through _process_cleaner_message with a real in-memory
SQLite database.

Two testing approaches:
  1. Mocked-AI tests: pre-set AI interpretation, verify handler logic
  2. Real-message integration tests: force keyword fallback (OpenAI fails),
     send actual message strings, verify the FULL pipeline end-to-end

Scenarios covered:
  - Accept a pending job (single offer)
  - Accept with no pending offers -> "no open job offers"
  - Reject a pending offer -> job cascaded to next cleaner
  - Cancel a confirmed/active job -> offer marked cancelled, job reassigned
  - Same cleaner NOT reassigned after cancellation
  - Need time ("Let me check")
  - Acknowledgment ("Cool", "Thanks") -> no response sent
  - Status updates ("On my way", "Done") -> job status changed
  - Question ("What time?") -> response sent
  - Partial accept (accept job 1, reject job 2)
  - Multiple pending offers -> accept all directly
  - Unclear message with pending offers -> clarification
  - Reject with no pending and no active jobs -> no action
  - Keyword fallback when OpenAI API fails
  - Conversation context tracking (inbound + outbound recorded)
  - Full multi-turn conversation flows
  - Real messages through keyword fallback: "Yes", "No", "Actually I cant",
    "On my way", "Done", "Cool", "Let me check", and multi-turn flows
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.models.base import Base
from app.models.property import Property
from app.models.cleaner import Cleaner
from app.models.job import Job, JobOffer, JobStatus, JobStatusHistory
from app.models.message import (
    Message, ConversationContext,
    MessageDirection, MessageChannel, SenderType,
)
from app.schemas.message import AIInterpretation
from app.api.webhooks import _process_cleaner_message


# --------------------------------------------------------------------------- #
#  Fixtures                                                                     #
# --------------------------------------------------------------------------- #

@pytest_asyncio.fixture
async def engine():
    """In-memory SQLite engine with all tables created."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine):
    """Async session bound to the in-memory engine."""
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
def mock_messaging():
    """Mock messaging service that records all sent messages."""
    svc = AsyncMock()
    svc.send_to_cleaner = AsyncMock(return_value={"success": True, "message_id": "msg_test"})
    svc.send_job_offer = AsyncMock(return_value={"success": True, "message_id": "offer_test"})
    svc.send_whatsapp_message = AsyncMock(return_value={"success": True})
    return svc


@pytest_asyncio.fixture
def mock_ai():
    """Mock AI service with default stubs for all public methods."""
    svc = AsyncMock()
    svc.interpret_cleaner_message = AsyncMock()  # Overridden per-test
    svc.generate_job_offer_message = AsyncMock(
        return_value="Cleaning job at Oakland House on Monday 10:00. $80. Can you take it?"
    )
    svc.generate_conversational_message = AsyncMock(
        return_value="Please confirm which jobs you want."
    )
    return svc


@pytest_asyncio.fixture
async def seed(db):
    """Insert base rows: property, two cleaners, conversation context."""
    prop = Property(
        id=1, name="Oakland House", address="123 Main St", city="Oakland",
        standard_cleaning_rate=80.0, estimated_cleaning_duration_minutes=120,
    )
    db.add(prop)

    cleaner_a = Cleaner(
        id=1, name="Alice Smith", phone="+15551234567",
        whatsapp_chat_id="15551234567@c.us",
        is_active=True, is_available=True,
        total_jobs_completed=10, total_jobs_offered=12,
        jobs_accepted=8, jobs_rejected=2,
        average_rating=4.8,
    )
    db.add(cleaner_a)

    cleaner_b = Cleaner(
        id=2, name="Bob Jones", phone="+15559876543",
        whatsapp_chat_id="15559876543@c.us",
        is_active=True, is_available=True,
        total_jobs_completed=5, total_jobs_offered=6,
        jobs_accepted=4, jobs_rejected=1,
        average_rating=4.5,
    )
    db.add(cleaner_b)

    ctx = ConversationContext(
        id=1,
        external_chat_id="15551234567@c.us",
        channel="whatsapp",
        participant_type="cleaner",
        cleaner_id=1,
        conversation_state="idle",
        recent_messages=[],
    )
    db.add(ctx)

    await db.flush()
    return {
        "property": prop,
        "cleaner_a": cleaner_a,
        "cleaner_b": cleaner_b,
        "context": ctx,
    }


# --------------------------------------------------------------------------- #
#  Helpers                                                                      #
# --------------------------------------------------------------------------- #

CHAT_ID = "15551234567@c.us"


async def _make_pending_job(db, cleaner_id=1, property_id=1, batch_position=1, scheduled_date=None):
    """Create a job in OFFERED status with a pending offer."""
    job = Job(
        property_id=property_id, job_type="turnover",
        status=JobStatus.OFFERED.value, urgency="normal",
        scheduled_date=scheduled_date or (datetime.now(timezone.utc) + timedelta(days=2)),
        scheduled_time="10:00", payment_amount=80.0,
        max_assignment_attempts=5, assignment_attempts=1,
    )
    db.add(job)
    await db.flush()

    offer = JobOffer(
        job_id=job.id, cleaner_id=cleaner_id,
        offered_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        offered_amount=80.0, status="pending",
        batch_position=batch_position,
    )
    db.add(offer)
    await db.flush()
    return job, offer


async def _make_confirmed_job(db, cleaner_id=1, property_id=1):
    """Create a confirmed job with an accepted offer."""
    job = Job(
        property_id=property_id, job_type="turnover",
        status=JobStatus.CONFIRMED.value, urgency="normal",
        scheduled_date=datetime.now(timezone.utc) + timedelta(days=1),
        scheduled_time="10:00", payment_amount=80.0,
        assigned_cleaner_id=cleaner_id,
        max_assignment_attempts=5, assignment_attempts=1,
    )
    db.add(job)
    await db.flush()

    offer = JobOffer(
        job_id=job.id, cleaner_id=cleaner_id,
        offered_at=datetime.now(timezone.utc) - timedelta(hours=2),
        offered_amount=80.0, status="accepted",
        responded_at=datetime.now(timezone.utc) - timedelta(hours=1),
        batch_position=1,
    )
    db.add(offer)
    await db.flush()
    return job, offer


def _inbound_message(db, cleaner, text):
    """Create and add an inbound Message to the session."""
    msg = Message(
        channel=MessageChannel.WHATSAPP.value,
        direction=MessageDirection.INBOUND.value,
        sender_type=SenderType.CLEANER.value,
        cleaner_id=cleaner.id,
        external_chat_id=CHAT_ID,
        external_sender_id=cleaner.phone.lstrip("+"),
        content=text,
        content_type="text",
        sent_at=datetime.now(timezone.utc),
    )
    db.add(msg)
    return msg


async def process(db, cleaner, text, mock_ai, mock_messaging, interpretation):
    """
    Run _process_cleaner_message with the given AI interpretation.

    Patches dependency look-ups so the handler receives mocked services.
    """
    mock_ai.interpret_cleaner_message = AsyncMock(return_value=interpretation)
    msg = _inbound_message(db, cleaner, text)
    await db.flush()

    with patch("app.api.webhooks.get_ai_service", return_value=mock_ai), \
         patch("app.api.webhooks.get_messaging_service", return_value=mock_messaging), \
         patch("app.dependencies.get_ai_service", return_value=mock_ai), \
         patch("app.dependencies.get_messaging_service", return_value=mock_messaging):
        result = await _process_cleaner_message(
            db=db, message=msg, cleaner=cleaner,
            message_text=text, chat_id=CHAT_ID,
        )
    # Flush so db.refresh() in tests sees the latest state
    await db.flush()
    return result


# --------------------------------------------------------------------------- #
#  1. Accept Job                                                                #
# --------------------------------------------------------------------------- #

class TestAcceptJob:

    @pytest.mark.asyncio
    async def test_accept_single_offer(self, db, seed, mock_ai, mock_messaging):
        """'Yes' with one pending offer -> offer accepted, job confirmed."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        result = await process(
            db, cleaner, "Yes", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=95,
                suggested_response="Confirmed, you're booked for Oakland House.",
            ),
        )

        assert result["action_taken"] == "accept_job"
        assert result["details"]["accepted_offers"] == 1

        await db.refresh(offer)
        assert offer.status == "accepted"

        await db.refresh(job)
        assert job.status == JobStatus.CONFIRMED.value
        assert job.assigned_cleaner_id == cleaner.id

        # Response sent
        mock_messaging.send_to_cleaner.assert_called_once()

    @pytest.mark.asyncio
    async def test_accept_no_pending_offers(self, db, seed, mock_ai, mock_messaging):
        """'Yes' with NO pending offers -> 'no open job offers'."""
        cleaner = seed["cleaner_a"]

        result = await process(
            db, cleaner, "Yes I can do it", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=80,
                suggested_response="Confirmed.",
            ),
        )

        assert result["action_taken"] == "no_action"
        assert result["details"]["reason"] == "no_pending_offers"

        sent_text = mock_messaging.send_to_cleaner.call_args[0][1]
        assert "no open job offers" in sent_text.lower()


# --------------------------------------------------------------------------- #
#  2. Reject Job (pending offer)                                                #
# --------------------------------------------------------------------------- #

class TestRejectPendingOffer:

    @pytest.mark.asyncio
    async def test_reject_offer_cascades(self, db, seed, mock_ai, mock_messaging):
        """'No' with pending offer -> rejected, cascade triggered."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        result = await process(
            db, cleaner, "No I cant do it", mock_ai, mock_messaging,
            AIInterpretation(
                intent="reject_job", confidence=90,
                suggested_response="Understood. Job will be reassigned.",
            ),
        )

        assert result["action_taken"] == "reject_job"
        assert result["details"]["rejected_offers"] == 1

        await db.refresh(offer)
        assert offer.status == "rejected"

        # Job should be reset for cascade (PENDING, OFFERED, or ESCALATED)
        await db.refresh(job)
        assert job.status in (
            JobStatus.PENDING.value,
            JobStatus.OFFERED.value,
            JobStatus.ESCALATED.value,
        )


# --------------------------------------------------------------------------- #
#  3. Cancel Confirmed Job                                                      #
# --------------------------------------------------------------------------- #

class TestCancelConfirmedJob:

    @pytest.mark.asyncio
    async def test_cancel_active_job(self, db, seed, mock_ai, mock_messaging):
        """Cleaner cancels an accepted job -> offer=cancelled, job cascaded."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_confirmed_job(db)

        result = await process(
            db, cleaner, "My schedule changed I cant come clean",
            mock_ai, mock_messaging,
            AIInterpretation(
                intent="reject_job", confidence=85,
                suggested_response="Understood, job cancelled. It will be reassigned.",
            ),
        )

        assert result["action_taken"] == "job_cancelled"

        await db.refresh(offer)
        assert offer.status == "cancelled"

        await db.refresh(job)
        assert job.status in (
            JobStatus.PENDING.value,
            JobStatus.OFFERED.value,
            JobStatus.ESCALATED.value,
        )

    @pytest.mark.asyncio
    async def test_cancel_excludes_same_cleaner(self, db, seed, mock_ai, mock_messaging):
        """After cancellation the same cleaner must NOT get the job again."""
        cleaner_a = seed["cleaner_a"]
        job, offer = await _make_confirmed_job(db, cleaner_id=cleaner_a.id)

        await process(
            db, cleaner_a, "Actually I cant make it", mock_ai, mock_messaging,
            AIInterpretation(
                intent="reject_job", confidence=85,
                suggested_response="Understood, job cancelled.",
            ),
        )

        await db.refresh(offer)
        assert offer.status == "cancelled"

        # Any new pending offers for this job must not be for cleaner A
        rows = (await db.execute(
            select(JobOffer).where(
                JobOffer.job_id == job.id,
                JobOffer.status == "pending",
            )
        )).scalars().all()

        for o in rows:
            assert o.cleaner_id != cleaner_a.id, (
                "Same cleaner should not be reassigned after cancellation"
            )


# --------------------------------------------------------------------------- #
#  4. Acknowledgment (no response)                                              #
# --------------------------------------------------------------------------- #

class TestAcknowledgment:

    @pytest.mark.asyncio
    async def test_cool_no_response(self, db, seed, mock_ai, mock_messaging):
        """'Cool' -> acknowledgment, no message sent."""
        result = await process(
            db, seed["cleaner_a"], "Cool", mock_ai, mock_messaging,
            AIInterpretation(intent="acknowledgment", confidence=90, suggested_response=""),
        )
        assert result["action_taken"] == "acknowledgment"
        mock_messaging.send_to_cleaner.assert_not_called()

    @pytest.mark.asyncio
    async def test_thanks_no_response(self, db, seed, mock_ai, mock_messaging):
        """'Thanks!!' -> acknowledgment, no message sent."""
        result = await process(
            db, seed["cleaner_a"], "Thanks!!", mock_ai, mock_messaging,
            AIInterpretation(intent="acknowledgment", confidence=90, suggested_response=""),
        )
        assert result["action_taken"] == "acknowledgment"
        mock_messaging.send_to_cleaner.assert_not_called()

    @pytest.mark.asyncio
    async def test_ok_thank_you_no_response(self, db, seed, mock_ai, mock_messaging):
        """'Its ok thank you' with no pending offers -> acknowledgment."""
        result = await process(
            db, seed["cleaner_a"], "Its ok thank you", mock_ai, mock_messaging,
            AIInterpretation(intent="acknowledgment", confidence=85, suggested_response=""),
        )
        assert result["action_taken"] == "acknowledgment"
        mock_messaging.send_to_cleaner.assert_not_called()


# --------------------------------------------------------------------------- #
#  5. Need Time                                                                 #
# --------------------------------------------------------------------------- #

class TestNeedTime:

    @pytest.mark.asyncio
    async def test_let_me_check(self, db, seed, mock_ai, mock_messaging):
        """'Let me check' -> need_time, brief response."""
        await _make_pending_job(db)

        result = await process(
            db, seed["cleaner_a"], "Let me check my schedule",
            mock_ai, mock_messaging,
            AIInterpretation(
                intent="need_time", confidence=85,
                suggested_response="No problem, let us know when you decide.",
            ),
        )

        assert result["action_taken"] == "need_time"
        mock_messaging.send_to_cleaner.assert_called_once()

    @pytest.mark.asyncio
    async def test_i_dont_know_yet(self, db, seed, mock_ai, mock_messaging):
        """'I dont know yet' -> need_time."""
        await _make_pending_job(db)

        result = await process(
            db, seed["cleaner_a"], "I dont know yet",
            mock_ai, mock_messaging,
            AIInterpretation(
                intent="need_time", confidence=80,
                suggested_response="No rush. Reply when you know.",
            ),
        )

        assert result["action_taken"] == "need_time"
        mock_messaging.send_to_cleaner.assert_called_once()


# --------------------------------------------------------------------------- #
#  6. Status Updates                                                            #
# --------------------------------------------------------------------------- #

class TestStatusUpdate:

    @pytest.mark.asyncio
    async def test_on_my_way(self, db, seed, mock_ai, mock_messaging):
        """'On my way' -> EN_ROUTE."""
        cleaner = seed["cleaner_a"]
        job, _ = await _make_confirmed_job(db)

        result = await process(
            db, cleaner, "On my way", mock_ai, mock_messaging,
            AIInterpretation(
                intent="status_update", confidence=95,
                status_update="en_route",
                suggested_response="Noted, thank you.",
            ),
        )

        assert result["action_taken"] == "status_update"
        assert result["details"]["new_status"] == "en_route"
        await db.refresh(job)
        assert job.status == JobStatus.EN_ROUTE.value

    @pytest.mark.asyncio
    async def test_done(self, db, seed, mock_ai, mock_messaging):
        """'All done' -> COMPLETED."""
        cleaner = seed["cleaner_a"]
        job, _ = await _make_confirmed_job(db)
        job.status = JobStatus.IN_PROGRESS.value
        job.started_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.flush()

        result = await process(
            db, cleaner, "All done here", mock_ai, mock_messaging,
            AIInterpretation(
                intent="status_update", confidence=95,
                status_update="completed",
                suggested_response="Noted, thank you.",
            ),
        )

        assert result["details"]["new_status"] == "completed"
        await db.refresh(job)
        assert job.status == JobStatus.COMPLETED.value
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_arrived(self, db, seed, mock_ai, mock_messaging):
        """'Im here' -> IN_PROGRESS."""
        cleaner = seed["cleaner_a"]
        job, _ = await _make_confirmed_job(db)

        result = await process(
            db, cleaner, "Im here", mock_ai, mock_messaging,
            AIInterpretation(
                intent="status_update", confidence=90,
                status_update="arrived",
                suggested_response="Noted, thank you.",
            ),
        )

        assert result["details"]["new_status"] == "in_progress"
        await db.refresh(job)
        assert job.status == JobStatus.IN_PROGRESS.value


# --------------------------------------------------------------------------- #
#  7. Question                                                                  #
# --------------------------------------------------------------------------- #

class TestQuestion:

    @pytest.mark.asyncio
    async def test_what_time(self, db, seed, mock_ai, mock_messaging):
        """'What time is the job?' -> question, response sent."""
        await _make_pending_job(db)

        result = await process(
            db, seed["cleaner_a"], "What time is the job?",
            mock_ai, mock_messaging,
            AIInterpretation(
                intent="question", confidence=85,
                question_type="time",
                suggested_response="The job is scheduled for 10:00 AM.",
            ),
        )

        assert result["action_taken"] == "question"
        assert result["details"]["question_type"] == "time"
        mock_messaging.send_to_cleaner.assert_called_once()


# --------------------------------------------------------------------------- #
#  8. Multiple Offers                                                           #
# --------------------------------------------------------------------------- #

class TestMultipleOffers:

    @pytest.mark.asyncio
    async def test_accept_two_offers_accepts_all(self, db, seed, mock_ai, mock_messaging):
        """With 2 pending offers, 'Yes' accepts all — AI understood the intent."""
        cleaner = seed["cleaner_a"]
        job1, offer1 = await _make_pending_job(db, cleaner_id=1, batch_position=1)
        job2, offer2 = await _make_pending_job(db, cleaner_id=1, batch_position=2)

        result = await process(
            db, cleaner, "Yes to all", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=90,
                suggested_response="Confirmed for both.",
            ),
        )

        assert result["action_taken"] == "accept_job"
        assert result["details"]["accepted_offers"] == 2

        await db.refresh(offer1)
        await db.refresh(offer2)
        assert offer1.status == "accepted"
        assert offer2.status == "accepted"


# --------------------------------------------------------------------------- #
#  9. Partial Accept                                                            #
# --------------------------------------------------------------------------- #

class TestPartialAccept:

    @pytest.mark.asyncio
    async def test_accept_first_reject_second(self, db, seed, mock_ai, mock_messaging):
        """Cleaner accepts job #1 and rejects job #2."""
        cleaner = seed["cleaner_a"]
        job1, offer1 = await _make_pending_job(db, batch_position=1)
        job2, offer2 = await _make_pending_job(db, batch_position=2)

        result = await process(
            db, cleaner,
            "I can do the first one but not the second",
            mock_ai, mock_messaging,
            AIInterpretation(
                intent="partial_accept", confidence=85,
                accepted_job_ids=[1], rejected_job_ids=[2],
                suggested_response="Noted. Booked for the first. Second will be reassigned.",
            ),
        )

        assert result["action_taken"] == "partial_accept"
        assert result["details"]["accepted_count"] >= 1


# --------------------------------------------------------------------------- #
# 10. Unclear / Clarification                                                   #
# --------------------------------------------------------------------------- #

class TestUnclear:

    @pytest.mark.asyncio
    async def test_unclear_with_pending(self, db, seed, mock_ai, mock_messaging):
        """Ambiguous message + pending offers -> clarification sent."""
        await _make_pending_job(db)

        result = await process(
            db, seed["cleaner_a"], "hmm idk maybe tomorrow",
            mock_ai, mock_messaging,
            AIInterpretation(
                intent="unclear", confidence=20,
                needs_clarification=True,
                clarification_question="Can you take the job? Reply yes or no.",
                suggested_response="Can you take the job? Reply yes or no.",
            ),
        )

        mock_messaging.send_to_cleaner.assert_called_once()


# --------------------------------------------------------------------------- #
# 11. Reject with NO pending and NO active jobs                                 #
# --------------------------------------------------------------------------- #

class TestRejectNoJobs:

    @pytest.mark.asyncio
    async def test_reject_nothing(self, db, seed, mock_ai, mock_messaging):
        """'Cancel' with nothing pending or active -> no_action."""
        result = await process(
            db, seed["cleaner_a"], "I need to cancel", mock_ai, mock_messaging,
            AIInterpretation(
                intent="reject_job", confidence=70,
                suggested_response="There are no active jobs to cancel.",
            ),
        )

        assert result["action_taken"] == "no_action"
        assert result["details"]["reason"] == "no_active_jobs"


# --------------------------------------------------------------------------- #
# 12. Keyword Fallback (unit tests on AIService._keyword_fallback)              #
# --------------------------------------------------------------------------- #

class TestKeywordFallback:
    """Directly test the rule-based fallback for when OpenAI fails."""

    def _fallback(self, message, pending_jobs=None):
        from app.services.ai_service import AIService
        svc = AIService.__new__(AIService)
        return svc._keyword_fallback(message, pending_jobs or [])

    # -- acceptance --

    def test_yes_with_pending(self):
        result = self._fallback("Yes", [{"job_id": 1}])
        assert result.intent == "accept_job"

    def test_sure_with_pending(self):
        result = self._fallback("Sure", [{"job_id": 1}])
        assert result.intent == "accept_job"

    def test_i_can_do_it_with_pending(self):
        result = self._fallback("I can do it", [{"job_id": 1}])
        assert result.intent == "accept_job"

    def test_ok_with_pending(self):
        result = self._fallback("ok", [{"job_id": 1}])
        assert result.intent == "accept_job"

    def test_yes_without_pending(self):
        """'Yes' with NO pending -> still accept_job (handler decides what to do)."""
        result = self._fallback("Yes", [])
        assert result.intent == "accept_job"

    # -- rejection --

    def test_no_with_pending(self):
        result = self._fallback("No", [{"job_id": 1}])
        assert result.intent == "reject_job"

    def test_cant_come(self):
        result = self._fallback("I cant come", [{"job_id": 1}])
        assert result.intent == "reject_job"

    def test_schedule_changed(self):
        result = self._fallback("My schedule changed", [])
        assert result.intent == "reject_job"

    # -- need_time --

    def test_let_me_check(self):
        result = self._fallback("Let me check", [{"job_id": 1}])
        assert result.intent == "need_time"

    def test_dont_know_yet(self):
        result = self._fallback("I dont know yet", [{"job_id": 1}])
        assert result.intent == "need_time"

    def test_maybe(self):
        result = self._fallback("maybe", [{"job_id": 1}])
        assert result.intent == "need_time"

    # -- status_update --

    def test_on_my_way(self):
        result = self._fallback("On my way", [])
        assert result.intent == "status_update"
        assert result.status_update == "en_route"

    def test_done(self):
        result = self._fallback("done", [])
        assert result.intent == "status_update"
        assert result.status_update == "completed"

    def test_arrived(self):
        result = self._fallback("I arrived", [])
        assert result.intent == "status_update"
        assert result.status_update == "arrived"

    # -- acknowledgment --

    def test_cool(self):
        result = self._fallback("Cool", [])
        assert result.intent == "acknowledgment"
        assert result.suggested_response == ""

    def test_thanks(self):
        result = self._fallback("Thanks", [])
        assert result.intent == "acknowledgment"

    def test_sounds_good(self):
        result = self._fallback("Sounds good", [])
        assert result.intent == "acknowledgment"

    # -- unclear default --

    def test_random_with_pending(self):
        result = self._fallback("purple elephants", [{"job_id": 1}])
        assert result.intent == "unclear"

    def test_random_without_pending(self):
        """Random text with no pending -> acknowledgment (silent)."""
        result = self._fallback("purple elephants", [])
        assert result.intent == "acknowledgment"


# --------------------------------------------------------------------------- #
# 13. Conversation Context Tracking                                             #
# --------------------------------------------------------------------------- #

class TestConversationContext:

    @pytest.mark.asyncio
    async def test_inbound_recorded(self, db, seed, mock_ai, mock_messaging):
        """Inbound message appears in context.recent_messages."""
        context = seed["context"]

        await process(
            db, seed["cleaner_a"], "Got it", mock_ai, mock_messaging,
            AIInterpretation(intent="acknowledgment", confidence=90, suggested_response=""),
        )

        await db.refresh(context)
        inbound = [m for m in context.recent_messages if m.get("direction") == "inbound"]
        assert len(inbound) >= 1
        assert inbound[-1]["content"] == "Got it"

    @pytest.mark.asyncio
    async def test_outbound_recorded(self, db, seed, mock_ai, mock_messaging):
        """When a response is sent, it appears in context.recent_messages."""
        context = seed["context"]
        await _make_pending_job(db)

        await process(
            db, seed["cleaner_a"], "Yes", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=95,
                suggested_response="Confirmed, you're booked.",
            ),
        )

        await db.refresh(context)
        outbound = [m for m in context.recent_messages if m.get("direction") == "outbound"]
        assert len(outbound) >= 1
        assert context.last_outbound_message is not None

    @pytest.mark.asyncio
    async def test_ack_no_outbound_recorded(self, db, seed, mock_ai, mock_messaging):
        """Acknowledgment sends nothing -> no outbound in context."""
        context = seed["context"]

        await process(
            db, seed["cleaner_a"], "Cool", mock_ai, mock_messaging,
            AIInterpretation(intent="acknowledgment", confidence=90, suggested_response=""),
        )

        await db.refresh(context)
        outbound = [m for m in context.recent_messages if m.get("direction") == "outbound"]
        assert len(outbound) == 0


# --------------------------------------------------------------------------- #
# 14. Full Conversation Flows                                                   #
# --------------------------------------------------------------------------- #

class TestFullFlows:

    @pytest.mark.asyncio
    async def test_needtime_then_accept_then_ack(self, db, seed, mock_ai, mock_messaging):
        """
        Turn 1: 'Let me check' -> need_time  (offer stays pending)
        Turn 2: 'I can do it'  -> accept_job  (offer accepted)
        Turn 3: 'Thanks'       -> ack          (no response)
        """
        cleaner = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        # Turn 1
        r1 = await process(
            db, cleaner, "Let me check my schedule", mock_ai, mock_messaging,
            AIInterpretation(
                intent="need_time", confidence=85,
                suggested_response="No problem, reply when ready.",
            ),
        )
        assert r1["action_taken"] == "need_time"
        await db.refresh(offer)
        assert offer.status == "pending"
        mock_messaging.reset_mock()

        # Turn 2
        r2 = await process(
            db, cleaner, "I checked and I can do it", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=95,
                suggested_response="Confirmed, you're booked.",
            ),
        )
        assert r2["action_taken"] == "accept_job"
        await db.refresh(offer)
        assert offer.status == "accepted"
        await db.refresh(job)
        assert job.status == JobStatus.CONFIRMED.value
        mock_messaging.reset_mock()

        # Turn 3
        r3 = await process(
            db, cleaner, "Thanks!", mock_ai, mock_messaging,
            AIInterpretation(intent="acknowledgment", confidence=90, suggested_response=""),
        )
        assert r3["action_taken"] == "acknowledgment"
        mock_messaging.send_to_cleaner.assert_not_called()

    @pytest.mark.asyncio
    async def test_accept_then_cancel_reassigns_to_other(self, db, seed, mock_ai, mock_messaging):
        """
        Turn 1: Accept -> job confirmed by cleaner A
        Turn 2: Cancel -> offer marked cancelled, job cascades to cleaner B
        """
        cleaner_a = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        # Turn 1: accept
        r1 = await process(
            db, cleaner_a, "Yes", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=95,
                suggested_response="Confirmed.",
            ),
        )
        assert r1["action_taken"] == "accept_job"
        await db.refresh(offer)
        assert offer.status == "accepted"
        await db.refresh(job)
        assert job.status == JobStatus.CONFIRMED.value
        mock_messaging.reset_mock()

        # Turn 2: cancel
        r2 = await process(
            db, cleaner_a, "Wait actually I cant", mock_ai, mock_messaging,
            AIInterpretation(
                intent="reject_job", confidence=85,
                suggested_response="Understood, job cancelled.",
            ),
        )
        assert r2["action_taken"] == "job_cancelled"

        await db.refresh(offer)
        assert offer.status == "cancelled"

        # Verify new offers exclude cleaner A
        new_offers = (await db.execute(
            select(JobOffer).where(
                JobOffer.job_id == job.id,
                JobOffer.status == "pending",
            )
        )).scalars().all()
        for o in new_offers:
            assert o.cleaner_id != cleaner_a.id

    @pytest.mark.asyncio
    async def test_status_flow_confirmed_to_completed(self, db, seed, mock_ai, mock_messaging):
        """
        Turn 1: 'On my way'  -> EN_ROUTE
        Turn 2: 'Im here'    -> IN_PROGRESS
        Turn 3: 'All done'   -> COMPLETED
        """
        cleaner = seed["cleaner_a"]
        job, _ = await _make_confirmed_job(db)

        # EN_ROUTE
        await process(
            db, cleaner, "On my way", mock_ai, mock_messaging,
            AIInterpretation(
                intent="status_update", confidence=95,
                status_update="en_route",
                suggested_response="Noted.",
            ),
        )
        await db.refresh(job)
        assert job.status == JobStatus.EN_ROUTE.value
        mock_messaging.reset_mock()

        # IN_PROGRESS (arrived)
        await process(
            db, cleaner, "Im here", mock_ai, mock_messaging,
            AIInterpretation(
                intent="status_update", confidence=90,
                status_update="arrived",
                suggested_response="Noted.",
            ),
        )
        await db.refresh(job)
        assert job.status == JobStatus.IN_PROGRESS.value
        mock_messaging.reset_mock()

        # COMPLETED
        await process(
            db, cleaner, "All done", mock_ai, mock_messaging,
            AIInterpretation(
                intent="status_update", confidence=95,
                status_update="completed",
                suggested_response="Noted, thank you.",
            ),
        )
        await db.refresh(job)
        assert job.status == JobStatus.COMPLETED.value
        assert job.completed_at is not None


# --------------------------------------------------------------------------- #
# 15. Real-Message Integration Tests (keyword fallback, no mocked AI intent)   #
# --------------------------------------------------------------------------- #

async def process_real(db, cleaner, text, mock_messaging):
    """
    Run _process_cleaner_message with REAL message strings.

    Forces OpenAI to fail so the keyword fallback classifies the message.
    No pre-set AI intent — the message text drives the entire pipeline.
    """
    from app.services.ai_service import AIService

    real_ai = AIService.__new__(AIService)
    real_ai.settings = MagicMock()
    real_ai.client = AsyncMock()
    real_ai.client.chat.completions.create = AsyncMock(
        side_effect=Exception("OpenAI unavailable — testing keyword fallback")
    )
    real_ai.model = "gpt-4"
    real_ai.max_tokens = 256
    # Wire up the methods that aren't overridden
    real_ai.generate_conversational_message = AsyncMock(
        return_value="Please confirm which jobs you want."
    )

    msg = _inbound_message(db, cleaner, text)
    await db.flush()

    with patch("app.api.webhooks.get_ai_service", return_value=real_ai), \
         patch("app.api.webhooks.get_messaging_service", return_value=mock_messaging), \
         patch("app.dependencies.get_ai_service", return_value=real_ai), \
         patch("app.dependencies.get_messaging_service", return_value=mock_messaging):
        result = await _process_cleaner_message(
            db=db, message=msg, cleaner=cleaner,
            message_text=text, chat_id=CHAT_ID,
        )
    await db.flush()
    return result


class TestRealMessages:
    """
    Integration tests using real message strings through keyword fallback.

    These do NOT mock the AI intent — OpenAI is forced to fail so the
    keyword_fallback rule engine classifies the message, then the handler
    acts on it. This tests the full pipeline end-to-end with actual
    WhatsApp-style messages.
    """

    # -- Accept --

    @pytest.mark.asyncio
    async def test_yes_accepts_pending(self, db, seed, mock_messaging):
        """'Yes' with a pending offer -> accepted, job confirmed."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        result = await process_real(db, cleaner, "Yes", mock_messaging)

        assert result["action_taken"] == "accept_job"
        await db.refresh(offer)
        assert offer.status == "accepted"
        await db.refresh(job)
        assert job.status == JobStatus.CONFIRMED.value

    @pytest.mark.asyncio
    async def test_sure_accepts_pending(self, db, seed, mock_messaging):
        """'Sure' with a pending offer -> accepted."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        result = await process_real(db, cleaner, "Sure", mock_messaging)

        assert result["action_taken"] == "accept_job"
        await db.refresh(offer)
        assert offer.status == "accepted"

    @pytest.mark.asyncio
    async def test_ok_accepts_pending(self, db, seed, mock_messaging):
        """'ok' with a pending offer -> accepted."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        result = await process_real(db, cleaner, "ok", mock_messaging)

        assert result["action_taken"] == "accept_job"
        await db.refresh(offer)
        assert offer.status == "accepted"

    # -- Reject pending --

    @pytest.mark.asyncio
    async def test_no_rejects_pending(self, db, seed, mock_messaging):
        """'No' with a pending offer -> rejected, cascade triggered."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        result = await process_real(db, cleaner, "No", mock_messaging)

        assert result["action_taken"] == "reject_job"
        await db.refresh(offer)
        assert offer.status == "rejected"

    @pytest.mark.asyncio
    async def test_cant_come_rejects_pending(self, db, seed, mock_messaging):
        """'I cant come' with a pending offer -> rejected."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        result = await process_real(db, cleaner, "I cant come", mock_messaging)

        assert result["action_taken"] == "reject_job"
        await db.refresh(offer)
        assert offer.status == "rejected"

    # -- Cancel confirmed job --

    @pytest.mark.asyncio
    async def test_actually_i_cant_cancels_confirmed(self, db, seed, mock_messaging):
        """
        'Actually I cant sorry' with a confirmed job -> job cancelled.

        This is the exact scenario from the bug: cleaner accepts, then
        sends a cancel message. The keyword fallback catches 'cant' as
        reject_job, and the handler cancels the active job.
        """
        cleaner = seed["cleaner_a"]
        job, offer = await _make_confirmed_job(db)

        result = await process_real(db, cleaner, "Actually I cant sorry", mock_messaging)

        assert result["action_taken"] == "job_cancelled"
        await db.refresh(offer)
        assert offer.status == "cancelled"
        # Response MUST be sent (not silently swallowed)
        mock_messaging.send_to_cleaner.assert_called_once()

    @pytest.mark.asyncio
    async def test_my_schedule_changed_cancels_confirmed(self, db, seed, mock_messaging):
        """'My schedule changed' with confirmed job -> cancelled."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_confirmed_job(db)

        result = await process_real(db, cleaner, "My schedule changed", mock_messaging)

        assert result["action_taken"] == "job_cancelled"
        await db.refresh(offer)
        assert offer.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_with_no_jobs_at_all(self, db, seed, mock_messaging):
        """'I need to cancel' with no pending or active jobs -> no_action."""
        cleaner = seed["cleaner_a"]

        result = await process_real(db, cleaner, "I need to cancel", mock_messaging)

        assert result["action_taken"] == "no_action"
        assert result["details"]["reason"] == "no_active_jobs"

    # -- Need time --

    @pytest.mark.asyncio
    async def test_let_me_check(self, db, seed, mock_messaging):
        """'Let me check my schedule' -> need_time, offer stays pending."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        result = await process_real(db, cleaner, "Let me check my schedule", mock_messaging)

        assert result["action_taken"] == "need_time"
        await db.refresh(offer)
        assert offer.status == "pending"  # unchanged
        mock_messaging.send_to_cleaner.assert_called_once()

    @pytest.mark.asyncio
    async def test_maybe(self, db, seed, mock_messaging):
        """'maybe' -> need_time."""
        cleaner = seed["cleaner_a"]
        await _make_pending_job(db)

        result = await process_real(db, cleaner, "maybe", mock_messaging)

        assert result["action_taken"] == "need_time"

    # -- Status updates --

    @pytest.mark.asyncio
    async def test_on_my_way_en_route(self, db, seed, mock_messaging):
        """'On my way' with confirmed job -> EN_ROUTE."""
        cleaner = seed["cleaner_a"]
        job, _ = await _make_confirmed_job(db)

        result = await process_real(db, cleaner, "On my way", mock_messaging)

        assert result["action_taken"] == "status_update"
        assert result["details"]["new_status"] == "en_route"
        await db.refresh(job)
        assert job.status == JobStatus.EN_ROUTE.value

    @pytest.mark.asyncio
    async def test_done_completes(self, db, seed, mock_messaging):
        """'Done' with in-progress job -> COMPLETED."""
        cleaner = seed["cleaner_a"]
        job, _ = await _make_confirmed_job(db)
        job.status = JobStatus.IN_PROGRESS.value
        job.started_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.flush()

        result = await process_real(db, cleaner, "Done", mock_messaging)

        assert result["details"]["new_status"] == "completed"
        await db.refresh(job)
        assert job.status == JobStatus.COMPLETED.value

    # -- Acknowledgment --

    @pytest.mark.asyncio
    async def test_cool_no_response(self, db, seed, mock_messaging):
        """'Cool' with no jobs -> acknowledgment, no message sent."""
        cleaner = seed["cleaner_a"]

        result = await process_real(db, cleaner, "Cool", mock_messaging)

        assert result["action_taken"] == "acknowledgment"
        mock_messaging.send_to_cleaner.assert_not_called()

    @pytest.mark.asyncio
    async def test_thanks_no_response(self, db, seed, mock_messaging):
        """'Thanks' -> acknowledgment, no message sent."""
        cleaner = seed["cleaner_a"]

        result = await process_real(db, cleaner, "Thanks", mock_messaging)

        assert result["action_taken"] == "acknowledgment"
        mock_messaging.send_to_cleaner.assert_not_called()

    # -- Full multi-turn flows --

    @pytest.mark.asyncio
    async def test_full_flow_accept_then_cancel(self, db, seed, mock_messaging):
        """
        Full pipeline with real messages, no mocked intents:
          Turn 1: 'Yes'                  -> accept_job (offer accepted, job confirmed)
          Turn 2: 'Actually I cant sorry' -> reject_job (job cancelled, reassigned)
        """
        cleaner = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        # Turn 1: accept
        r1 = await process_real(db, cleaner, "Yes", mock_messaging)
        assert r1["action_taken"] == "accept_job"
        await db.refresh(offer)
        assert offer.status == "accepted"
        await db.refresh(job)
        assert job.status == JobStatus.CONFIRMED.value
        mock_messaging.reset_mock()

        # Turn 2: cancel
        r2 = await process_real(db, cleaner, "Actually I cant sorry", mock_messaging)
        assert r2["action_taken"] == "job_cancelled"
        await db.refresh(offer)
        assert offer.status == "cancelled"
        mock_messaging.send_to_cleaner.assert_called_once()

    @pytest.mark.asyncio
    async def test_full_flow_status_updates(self, db, seed, mock_messaging):
        """
        Full pipeline with real messages:
          Turn 1: 'On my way' -> EN_ROUTE
          Turn 2: 'Done'      -> COMPLETED
        """
        cleaner = seed["cleaner_a"]
        job, _ = await _make_confirmed_job(db)

        # Turn 1: en route
        r1 = await process_real(db, cleaner, "On my way", mock_messaging)
        assert r1["details"]["new_status"] == "en_route"
        await db.refresh(job)
        assert job.status == JobStatus.EN_ROUTE.value
        mock_messaging.reset_mock()

        # Simulate in-progress (arrived)
        job.status = JobStatus.IN_PROGRESS.value
        job.started_at = datetime.now(timezone.utc)
        await db.flush()

        # Turn 2: done
        r2 = await process_real(db, cleaner, "Done", mock_messaging)
        assert r2["details"]["new_status"] == "completed"
        await db.refresh(job)
        assert job.status == JobStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_full_flow_needtime_then_accept(self, db, seed, mock_messaging):
        """
        Full pipeline:
          Turn 1: 'Let me check' -> need_time (offer stays pending)
          Turn 2: 'Yes'          -> accept_job (offer accepted)
          Turn 3: 'Thanks'       -> acknowledgment (silent)
        """
        cleaner = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        # Turn 1
        r1 = await process_real(db, cleaner, "Let me check", mock_messaging)
        assert r1["action_taken"] == "need_time"
        await db.refresh(offer)
        assert offer.status == "pending"
        mock_messaging.reset_mock()

        # Turn 2
        r2 = await process_real(db, cleaner, "Yes", mock_messaging)
        assert r2["action_taken"] == "accept_job"
        await db.refresh(offer)
        assert offer.status == "accepted"
        mock_messaging.reset_mock()

        # Turn 3
        r3 = await process_real(db, cleaner, "Thanks", mock_messaging)
        assert r3["action_taken"] == "acknowledgment"
        mock_messaging.send_to_cleaner.assert_not_called()


# --------------------------------------------------------------------------- #
# 16. Reclaim after rejection (take-back when job still unassigned)            #
# --------------------------------------------------------------------------- #

async def _make_rejected_job(db, cleaner_id=1, property_id=1, job_status=JobStatus.PENDING.value):
    """Create a job with a rejected offer from the given cleaner."""
    job = Job(
        property_id=property_id, job_type="turnover",
        status=job_status, urgency="normal",
        scheduled_date=datetime.now(timezone.utc) + timedelta(days=2),
        scheduled_time="10:00", payment_amount=80.0,
        max_assignment_attempts=5, assignment_attempts=2,
    )
    db.add(job)
    await db.flush()

    offer = JobOffer(
        job_id=job.id, cleaner_id=cleaner_id,
        offered_at=datetime.now(timezone.utc) - timedelta(hours=1),
        offered_amount=80.0, status="rejected",
        responded_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        batch_position=1,
    )
    db.add(offer)
    await db.flush()
    return job, offer


class TestReclaimAfterRejection:
    """
    Tests for the take-back scenario: cleaner rejects, then changes mind.

    Rules:
    - Allow reclaim ONLY if the job is unassigned (PENDING or ESCALATED)
    - Block reclaim if job is OFFERED to another cleaner or CONFIRMED
    """

    @pytest.mark.asyncio
    async def test_reclaim_pending_job(self, db, seed, mock_ai, mock_messaging):
        """Cleaner rejected, job still PENDING (no one else got it) -> reclaim works."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_rejected_job(db, cleaner_id=cleaner.id)

        result = await process(
            db, cleaner, "Wait actually yes I can do it", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=85,
                suggested_response="Confirmed, you're booked.",
            ),
        )

        assert result["action_taken"] == "accept_job"
        assert result["details"]["reclaimed"] is True

        await db.refresh(offer)
        assert offer.status == "accepted"

        await db.refresh(job)
        assert job.status == JobStatus.CONFIRMED.value
        assert job.assigned_cleaner_id == cleaner.id

    @pytest.mark.asyncio
    async def test_reclaim_escalated_job(self, db, seed, mock_ai, mock_messaging):
        """Job ESCALATED (all cleaners exhausted) -> reclaim works."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_rejected_job(
            db, cleaner_id=cleaner.id, job_status=JobStatus.ESCALATED.value,
        )

        result = await process(
            db, cleaner, "Yes I changed my mind", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=85,
                suggested_response="Confirmed, you're booked.",
            ),
        )

        assert result["action_taken"] == "accept_job"
        assert result["details"]["reclaimed"] is True

        await db.refresh(offer)
        assert offer.status == "accepted"

        await db.refresh(job)
        assert job.status == JobStatus.CONFIRMED.value

    @pytest.mark.asyncio
    async def test_no_reclaim_when_offered_to_another(self, db, seed, mock_ai, mock_messaging):
        """Job OFFERED to another cleaner -> reclaim blocked."""
        cleaner_a = seed["cleaner_a"]
        cleaner_b = seed["cleaner_b"]

        # Cleaner A rejected, job cascaded to cleaner B (OFFERED with pending offer)
        job = Job(
            property_id=1, job_type="turnover",
            status=JobStatus.OFFERED.value, urgency="normal",
            scheduled_date=datetime.now(timezone.utc) + timedelta(days=2),
            scheduled_time="10:00", payment_amount=80.0,
            max_assignment_attempts=5, assignment_attempts=2,
        )
        db.add(job)
        await db.flush()

        # A's rejected offer
        offer_a = JobOffer(
            job_id=job.id, cleaner_id=cleaner_a.id,
            offered_at=datetime.now(timezone.utc) - timedelta(hours=1),
            offered_amount=80.0, status="rejected",
            responded_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            batch_position=1,
        )
        db.add(offer_a)

        # B's pending offer (job is OFFERED to B)
        offer_b = JobOffer(
            job_id=job.id, cleaner_id=cleaner_b.id,
            offered_at=datetime.now(timezone.utc),
            offered_amount=80.0, status="pending",
            batch_position=2,
        )
        db.add(offer_b)
        await db.flush()

        # A tries to accept -> should NOT reclaim (job is OFFERED, not PENDING)
        result = await process(
            db, cleaner_a, "Wait actually yes", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=85,
                suggested_response="Confirmed.",
            ),
        )

        assert result["action_taken"] == "no_action"
        assert result["details"]["reason"] == "no_pending_offers"

        # Original rejection unchanged
        await db.refresh(offer_a)
        assert offer_a.status == "rejected"

    @pytest.mark.asyncio
    async def test_no_reclaim_when_confirmed_by_another(self, db, seed, mock_ai, mock_messaging):
        """Job CONFIRMED by another cleaner -> reclaim blocked."""
        cleaner_a = seed["cleaner_a"]
        cleaner_b = seed["cleaner_b"]

        job = Job(
            property_id=1, job_type="turnover",
            status=JobStatus.CONFIRMED.value, urgency="normal",
            scheduled_date=datetime.now(timezone.utc) + timedelta(days=2),
            scheduled_time="10:00", payment_amount=80.0,
            assigned_cleaner_id=cleaner_b.id,
            max_assignment_attempts=5, assignment_attempts=2,
        )
        db.add(job)
        await db.flush()

        # A's rejected offer
        offer_a = JobOffer(
            job_id=job.id, cleaner_id=cleaner_a.id,
            offered_at=datetime.now(timezone.utc) - timedelta(hours=1),
            offered_amount=80.0, status="rejected",
            responded_at=datetime.now(timezone.utc) - timedelta(minutes=30),
            batch_position=1,
        )
        db.add(offer_a)
        await db.flush()

        # A tries to accept -> should NOT reclaim (job confirmed by B)
        result = await process(
            db, cleaner_a, "I changed my mind", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=85,
                suggested_response="Confirmed.",
            ),
        )

        assert result["action_taken"] == "no_action"
        assert result["details"]["reason"] == "no_pending_offers"

    # -- Real-message integration test --

    @pytest.mark.asyncio
    async def test_real_msg_reject_then_reclaim(self, db, seed, mock_messaging):
        """
        Full pipeline with real messages — the user's exact scenario:
          Turn 1: 'No' -> reject_job (offer rejected, job stays PENDING)
          Turn 2: 'Yes' -> accept_job via reclaim (offer re-accepted)
        """
        cleaner = seed["cleaner_a"]
        job, offer = await _make_pending_job(db)

        # Turn 1: reject
        r1 = await process_real(db, cleaner, "No", mock_messaging)
        assert r1["action_taken"] == "reject_job"
        await db.refresh(offer)
        assert offer.status == "rejected"
        await db.refresh(job)
        assert job.status in (JobStatus.PENDING.value, JobStatus.OFFERED.value, JobStatus.ESCALATED.value)
        mock_messaging.reset_mock()

        # If cascade offered to someone else, force job back to PENDING
        # to simulate the "no other cleaners available" scenario
        job.status = JobStatus.PENDING.value
        await db.flush()

        # Turn 2: change mind
        r2 = await process_real(db, cleaner, "Yes", mock_messaging)
        assert r2["action_taken"] == "accept_job"
        assert r2["details"].get("reclaimed") is True

        await db.refresh(offer)
        assert offer.status == "accepted"
        await db.refresh(job)
        assert job.status == JobStatus.CONFIRMED.value
        assert job.assigned_cleaner_id == cleaner.id


# --------------------------------------------------------------------------- #
# 17. Multiple Active Jobs Ambiguity                                           #
# --------------------------------------------------------------------------- #

class TestMultipleActiveJobs:
    """
    Tests for when a cleaner has 2+ confirmed/active jobs and sends an
    ambiguous message like 'I can't' or 'Done'.

    The handler must NOT crash (old bug: scalar_one_or_none with 2 rows).
    Instead it asks the cleaner which job they mean.
    """

    @pytest.mark.asyncio
    async def test_cancel_with_two_active_jobs_asks_which(self, db, seed, mock_ai, mock_messaging):
        """'I can't' with 2 confirmed jobs -> asks which one to cancel."""
        cleaner = seed["cleaner_a"]
        job1, _ = await _make_confirmed_job(db, cleaner_id=cleaner.id, property_id=1)
        job2, _ = await _make_confirmed_job(db, cleaner_id=cleaner.id, property_id=1)

        result = await process(
            db, cleaner, "I cant do it", mock_ai, mock_messaging,
            AIInterpretation(
                intent="reject_job", confidence=85,
                suggested_response="Understood, job cancelled.",
            ),
        )

        assert result["action_taken"] == "needs_clarification"
        assert result["details"]["reason"] == "multiple_active_jobs"
        assert result["details"]["active_job_count"] == 2
        mock_messaging.send_to_cleaner.assert_called_once()
        sent_text = mock_messaging.send_to_cleaner.call_args[0][1]
        assert "2 active jobs" in sent_text
        assert "which one" in sent_text.lower()

        # Neither job cancelled
        await db.refresh(job1)
        await db.refresh(job2)
        assert job1.status == JobStatus.CONFIRMED.value
        assert job2.status == JobStatus.CONFIRMED.value

    @pytest.mark.asyncio
    async def test_status_update_with_two_active_jobs_asks_which(self, db, seed, mock_ai, mock_messaging):
        """'Done' with 2 active jobs -> asks which one is done."""
        cleaner = seed["cleaner_a"]
        job1, _ = await _make_confirmed_job(db, cleaner_id=cleaner.id, property_id=1)
        job2, _ = await _make_confirmed_job(db, cleaner_id=cleaner.id, property_id=1)

        result = await process(
            db, cleaner, "All done", mock_ai, mock_messaging,
            AIInterpretation(
                intent="status_update", confidence=95,
                status_update="completed",
                suggested_response="Noted, thank you.",
            ),
        )

        assert result["action_taken"] == "needs_clarification"
        assert result["details"]["reason"] == "multiple_active_jobs"
        assert result["details"]["active_job_count"] == 2
        mock_messaging.send_to_cleaner.assert_called_once()
        sent_text = mock_messaging.send_to_cleaner.call_args[0][1]
        assert "2 active jobs" in sent_text

        # Neither job updated
        await db.refresh(job1)
        await db.refresh(job2)
        assert job1.status == JobStatus.CONFIRMED.value
        assert job2.status == JobStatus.CONFIRMED.value

    @pytest.mark.asyncio
    async def test_cancel_single_active_still_works(self, db, seed, mock_ai, mock_messaging):
        """'I can't' with 1 confirmed job -> cancels normally (no regression)."""
        cleaner = seed["cleaner_a"]
        job, offer = await _make_confirmed_job(db, cleaner_id=cleaner.id, property_id=1)

        result = await process(
            db, cleaner, "I cant do it", mock_ai, mock_messaging,
            AIInterpretation(
                intent="reject_job", confidence=85,
                suggested_response="Understood, job cancelled.",
            ),
        )

        assert result["action_taken"] == "job_cancelled"
        assert result["details"]["job_id"] == job.id

        await db.refresh(offer)
        assert offer.status == "cancelled"

    @pytest.mark.asyncio
    async def test_status_update_single_active_still_works(self, db, seed, mock_ai, mock_messaging):
        """'On my way' with 1 confirmed job -> updates normally."""
        cleaner = seed["cleaner_a"]
        job, _ = await _make_confirmed_job(db, cleaner_id=cleaner.id, property_id=1)

        result = await process(
            db, cleaner, "On my way", mock_ai, mock_messaging,
            AIInterpretation(
                intent="status_update", confidence=95,
                status_update="en_route",
                suggested_response="Noted.",
            ),
        )

        assert result["action_taken"] == "status_update"
        assert result["details"]["new_status"] == "en_route"
        await db.refresh(job)
        assert job.status == JobStatus.EN_ROUTE.value

    # -- Real-message integration tests --

    @pytest.mark.asyncio
    async def test_real_msg_cancel_two_active_asks_which(self, db, seed, mock_messaging):
        """Real message: 'Actually I cant' with 2 jobs -> asks which one."""
        cleaner = seed["cleaner_a"]
        job1, _ = await _make_confirmed_job(db, cleaner_id=cleaner.id, property_id=1)
        job2, _ = await _make_confirmed_job(db, cleaner_id=cleaner.id, property_id=1)

        result = await process_real(db, cleaner, "Actually I cant sorry", mock_messaging)

        assert result["action_taken"] == "needs_clarification"
        assert result["details"]["reason"] == "multiple_active_jobs"

    @pytest.mark.asyncio
    async def test_real_msg_done_two_active_asks_which(self, db, seed, mock_messaging):
        """Real message: 'Done' with 2 active jobs -> asks which one."""
        cleaner = seed["cleaner_a"]
        job1, _ = await _make_confirmed_job(db, cleaner_id=cleaner.id, property_id=1)
        job2, _ = await _make_confirmed_job(db, cleaner_id=cleaner.id, property_id=1)

        result = await process_real(db, cleaner, "Done", mock_messaging)

        assert result["action_taken"] == "needs_clarification"
        assert result["details"]["reason"] == "multiple_active_jobs"

    @pytest.mark.asyncio
    async def test_real_msg_yes_two_pending_accepts_both(self, db, seed, mock_messaging):
        """Real message: 'Yes' with 2 pending offers -> accepts both directly."""
        cleaner = seed["cleaner_a"]
        job1, offer1 = await _make_pending_job(db, cleaner_id=1, batch_position=1)
        job2, offer2 = await _make_pending_job(db, cleaner_id=1, batch_position=2)

        result = await process_real(db, cleaner, "Yes", mock_messaging)

        assert result["action_taken"] == "accept_job"
        assert result["details"]["accepted_offers"] == 2

        await db.refresh(offer1)
        await db.refresh(offer2)
        assert offer1.status == "accepted"
        assert offer2.status == "accepted"


# --------------------------------------------------------------------------- #
# 18. Partial Accept — "only" patterns                                         #
# --------------------------------------------------------------------------- #

class TestPartialAcceptKeywordFallback:
    """Keyword fallback classifies intent; handler does job matching."""

    def _fallback(self, message, pending_jobs=None):
        from app.services.ai_service import AIService
        svc = AIService.__new__(AIService)
        return svc._keyword_fallback(message, pending_jobs or [])

    def test_only_the_last_one(self):
        """'Only the last one' with 2 jobs -> partial_accept intent."""
        jobs = [
            {"job_id": 1, "batch_position": 1, "property_name": "9th", "date": "Feb 12"},
            {"job_id": 2, "batch_position": 2, "property_name": "9th", "date": "Feb 25"},
        ]
        result = self._fallback("Only the last one", jobs)
        assert result.intent == "partial_accept"

    def test_only_the_first_one(self):
        """'Only the first one' with 2 jobs -> partial_accept intent."""
        jobs = [
            {"job_id": 1, "batch_position": 1, "property_name": "9th", "date": "Feb 12"},
            {"job_id": 2, "batch_position": 2, "property_name": "9th", "date": "Feb 25"},
        ]
        result = self._fallback("Only the first one", jobs)
        assert result.intent == "partial_accept"

    def test_only_feb_25(self):
        """'Only feb 25' with 2 jobs -> partial_accept intent."""
        jobs = [
            {"job_id": 1, "batch_position": 1, "property_name": "9th", "date": "Feb 12"},
            {"job_id": 2, "batch_position": 2, "property_name": "9th", "date": "Feb 25"},
        ]
        result = self._fallback("Only feb 25", jobs)
        assert result.intent == "partial_accept"

    def test_only_the_26(self):
        """'Only the 26' with 2 jobs -> partial_accept intent."""
        jobs = [
            {"job_id": 1, "batch_position": 1, "property_name": "9th", "date": "Wednesday Feb 12"},
            {"job_id": 2, "batch_position": 2, "property_name": "9th", "date": "Thursday Feb 26"},
        ]
        result = self._fallback("Only the 26", jobs)
        assert result.intent == "partial_accept"

    def test_only_the_30_still_partial(self):
        """'Only the 30' -> still partial_accept (handler resolves or asks)."""
        jobs = [
            {"job_id": 1, "batch_position": 1, "property_name": "9th", "date": "Feb 12"},
            {"job_id": 2, "batch_position": 2, "property_name": "9th", "date": "Feb 25"},
        ]
        result = self._fallback("Only the 30", jobs)
        assert result.intent == "partial_accept"

    def test_number_reply(self):
        """'2' with 2 pending -> partial_accept intent."""
        jobs = [
            {"job_id": 1, "batch_position": 1, "property_name": "9th", "date": "Feb 12"},
            {"job_id": 2, "batch_position": 2, "property_name": "9th", "date": "Feb 25"},
        ]
        result = self._fallback("2", jobs)
        assert result.intent == "partial_accept"

    def test_number_1(self):
        """'1' with 2 pending -> partial_accept intent."""
        jobs = [
            {"job_id": 1, "batch_position": 1, "property_name": "9th", "date": "Feb 12"},
            {"job_id": 2, "batch_position": 2, "property_name": "9th", "date": "Feb 25"},
        ]
        result = self._fallback("1", jobs)
        assert result.intent == "partial_accept"

    def test_all_accepts_all(self):
        """'all' with pending -> accept_job (accept all)."""
        jobs = [
            {"job_id": 1, "batch_position": 1, "property_name": "9th", "date": "Feb 12"},
            {"job_id": 2, "batch_position": 2, "property_name": "9th", "date": "Feb 25"},
        ]
        result = self._fallback("all", jobs)
        assert result.intent == "accept_job"

    def test_only_with_single_job_not_partial(self):
        """'Only this one yes' with 1 job -> not partial, falls through to accept."""
        jobs = [{"job_id": 1, "batch_position": 1, "property_name": "9th", "date": "Feb 12"}]
        result = self._fallback("Only this one yes", jobs)
        # With only 1 job, "only" guard doesn't trigger. "yes" -> accept_job
        assert result.intent == "accept_job"


# --------------------------------------------------------------------------- #
# 19. Handler partial-signal guard                                              #
# --------------------------------------------------------------------------- #

class TestPartialSignalGuard:
    """
    When accept_job + multiple pending, handler checks conversation history
    for 'only' signals to avoid accepting all when cleaner wanted partial.
    """

    @pytest.mark.asyncio
    async def test_yes_after_only_matches_from_history(self, db, seed, mock_ai, mock_messaging):
        """'Yes' after 'only the last one' -> handler matches from the 'only' message."""
        cleaner = seed["cleaner_a"]
        context = seed["context"]
        job1, offer1 = await _make_pending_job(db, cleaner_id=1, batch_position=1)
        job2, offer2 = await _make_pending_job(db, cleaner_id=1, batch_position=2)

        # Simulate prior message: cleaner said "only the last one"
        context.recent_messages = [
            {"direction": "outbound", "content": "2 jobs available: 9th on Feb 12, 9th on Feb 25."},
            {"direction": "inbound", "content": "Only the last one"},
            {"direction": "outbound", "content": "Can you confirm?"},
        ]
        await db.flush()

        result = await process(
            db, cleaner, "Yes", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=85,
                suggested_response="Confirmed.",
            ),
        )

        # Handler found "Only the last one" in history, matched it
        assert result["action_taken"] == "partial_accept"
        assert result["details"]["accepted_count"] == 1
        assert result["details"]["rejected_count"] == 1

    @pytest.mark.asyncio
    async def test_yes_without_only_accepts_all(self, db, seed, mock_ai, mock_messaging):
        """'Yes' with no prior 'only' signal -> accepts all normally."""
        cleaner = seed["cleaner_a"]
        context = seed["context"]
        job1, offer1 = await _make_pending_job(db, cleaner_id=1, batch_position=1)
        job2, offer2 = await _make_pending_job(db, cleaner_id=1, batch_position=2)

        # No "only" in conversation history
        context.recent_messages = [
            {"direction": "outbound", "content": "2 jobs available."},
        ]
        await db.flush()

        result = await process(
            db, cleaner, "Yes to all", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=90,
                suggested_response="Confirmed for both.",
            ),
        )

        assert result["action_taken"] == "accept_job"
        assert result["details"]["accepted_offers"] == 2
        await db.refresh(offer1)
        await db.refresh(offer2)
        assert offer1.status == "accepted"
        assert offer2.status == "accepted"

    # -- Real-message integration tests --

    @pytest.mark.asyncio
    async def test_real_msg_only_last_one_partial(self, db, seed, mock_messaging):
        """Real message: 'Only the last one' with 2 pending -> partial_accept."""
        cleaner = seed["cleaner_a"]
        job1, offer1 = await _make_pending_job(db, cleaner_id=1, batch_position=1)
        job2, offer2 = await _make_pending_job(db, cleaner_id=1, batch_position=2)

        result = await process_real(db, cleaner, "Only the last one", mock_messaging)

        assert result["action_taken"] == "partial_accept"
        assert result["details"]["accepted_count"] == 1
        assert result["details"]["rejected_count"] == 1

        # "last one" = last in list (DESC: offer2 first, offer1 last) = offer1
        await db.refresh(offer1)
        assert offer1.status == "accepted"
        await db.refresh(offer2)
        assert offer2.status == "rejected"

    @pytest.mark.asyncio
    async def test_real_msg_number_reply_picks_job(self, db, seed, mock_messaging):
        """Real message: '2' with 2 pending -> partial_accept for 2nd listed job.

        pending_offers ordered by offered_at DESC, so offer2 is first
        in the list and offer1 is second. '2' picks the 2nd item = offer1.
        """
        cleaner = seed["cleaner_a"]
        job1, offer1 = await _make_pending_job(db, cleaner_id=1, batch_position=1)
        job2, offer2 = await _make_pending_job(db, cleaner_id=1, batch_position=2)

        result = await process_real(db, cleaner, "2", mock_messaging)

        assert result["action_taken"] == "partial_accept"
        assert result["details"]["accepted_count"] == 1

        # '2' picks the 2nd item in the DESC-ordered list = offer1
        await db.refresh(offer1)
        assert offer1.status == "accepted"
        await db.refresh(offer2)
        assert offer2.status == "rejected"

    @pytest.mark.asyncio
    async def test_real_msg_only_feb_no_match_asks_which(self, db, seed, mock_messaging):
        """Real message: 'Only feb 25' when no job has day 25 -> asks which."""
        cleaner = seed["cleaner_a"]
        # Default scheduled_date is ~2 days from now, NOT on day 25
        job1, offer1 = await _make_pending_job(db, cleaner_id=1, batch_position=1)
        job2, offer2 = await _make_pending_job(db, cleaner_id=1, batch_position=2)

        result = await process_real(db, cleaner, "Only feb 25", mock_messaging)

        # Date 25 doesn't match any job -> asks for clarification
        mock_messaging.send_to_cleaner.assert_called_once()
        sent_text = mock_messaging.send_to_cleaner.call_args[0][1]
        assert "1)" in sent_text or "2)" in sent_text

        # Neither offer should be accepted yet
        await db.refresh(offer1)
        await db.refresh(offer2)
        assert offer1.status == "pending"
        assert offer2.status == "pending"

    @pytest.mark.asyncio
    async def test_real_msg_only_the_26_matches_date(self, db, seed, mock_messaging):
        """Real message: 'Only the 26' when job on Feb 26 exists -> partial_accept."""
        cleaner = seed["cleaner_a"]
        feb12 = datetime(2026, 2, 12, 10, 0, tzinfo=timezone.utc)
        feb26 = datetime(2026, 2, 26, 10, 0, tzinfo=timezone.utc)
        job1, offer1 = await _make_pending_job(db, cleaner_id=1, batch_position=1, scheduled_date=feb12)
        job2, offer2 = await _make_pending_job(db, cleaner_id=1, batch_position=2, scheduled_date=feb26)

        result = await process_real(db, cleaner, "Only the 26", mock_messaging)

        assert result["action_taken"] == "partial_accept"
        mock_messaging.send_to_cleaner.assert_called_once()

        await db.refresh(offer2)
        assert offer2.status == "accepted"
        await db.refresh(offer1)
        assert offer1.status == "rejected"

    @pytest.mark.asyncio
    async def test_real_msg_only_the_12_matches_date(self, db, seed, mock_messaging):
        """Real message: 'Only the 12' when job on Feb 12 exists -> partial_accept."""
        cleaner = seed["cleaner_a"]
        feb12 = datetime(2026, 2, 12, 10, 0, tzinfo=timezone.utc)
        feb26 = datetime(2026, 2, 26, 10, 0, tzinfo=timezone.utc)
        job1, offer1 = await _make_pending_job(db, cleaner_id=1, batch_position=1, scheduled_date=feb12)
        job2, offer2 = await _make_pending_job(db, cleaner_id=1, batch_position=2, scheduled_date=feb26)

        result = await process_real(db, cleaner, "Only the 12", mock_messaging)

        assert result["action_taken"] == "partial_accept"
        mock_messaging.send_to_cleaner.assert_called_once()

        await db.refresh(offer1)
        assert offer1.status == "accepted"
        await db.refresh(offer2)
        assert offer2.status == "rejected"


# --------------------------------------------------------------------------- #
# 20. Handler date-matching safety net for partial_accept                       #
# --------------------------------------------------------------------------- #

class TestPartialAcceptDateMatching:
    """
    When AI returns partial_accept with date numbers (e.g. 26 for Feb 26)
    instead of position numbers, the handler should match by day-of-month.
    """

    @pytest.mark.asyncio
    async def test_ai_returns_date_number_handler_matches(self, db, seed, mock_ai, mock_messaging):
        """AI returns accepted_jobs:[26] for a Feb 26 job -> handler matches by date."""
        cleaner = seed["cleaner_a"]
        feb12 = datetime(2026, 2, 12, 10, 0, tzinfo=timezone.utc)
        feb26 = datetime(2026, 2, 26, 10, 0, tzinfo=timezone.utc)
        job1, offer1 = await _make_pending_job(db, batch_position=1, scheduled_date=feb12)
        job2, offer2 = await _make_pending_job(db, batch_position=2, scheduled_date=feb26)

        # AI correctly identified partial_accept but returned the date (26)
        # instead of the position number (2)
        result = await process(
            db, cleaner, "Only the 26", mock_ai, mock_messaging,
            AIInterpretation(
                intent="partial_accept", confidence=85,
                accepted_job_ids=[26], rejected_job_ids=[12],
                suggested_response="Noted, you'll take the Feb 26 job.",
            ),
        )

        assert result["action_taken"] == "partial_accept"
        # Handler matched by date since positions 26/12 don't exist
        await db.refresh(offer2)
        assert offer2.status == "accepted"
        await db.refresh(offer1)
        assert offer1.status == "rejected"
        # Response sent
        mock_messaging.send_to_cleaner.assert_called_once()

    @pytest.mark.asyncio
    async def test_ai_returns_date_number_accept_only(self, db, seed, mock_ai, mock_messaging):
        """AI returns accepted_jobs:[26], no rejected_jobs -> accept match, reject others."""
        cleaner = seed["cleaner_a"]
        feb12 = datetime(2026, 2, 12, 10, 0, tzinfo=timezone.utc)
        feb26 = datetime(2026, 2, 26, 10, 0, tzinfo=timezone.utc)
        job1, offer1 = await _make_pending_job(db, batch_position=1, scheduled_date=feb12)
        job2, offer2 = await _make_pending_job(db, batch_position=2, scheduled_date=feb26)

        # AI returned accepted_jobs with date number, forgot rejected_jobs
        result = await process(
            db, cleaner, "Only the 26", mock_ai, mock_messaging,
            AIInterpretation(
                intent="partial_accept", confidence=85,
                accepted_job_ids=[26], rejected_job_ids=[],
                suggested_response="Noted, you'll take the Feb 26 job.",
            ),
        )

        assert result["action_taken"] == "partial_accept"
        await db.refresh(offer2)
        assert offer2.status == "accepted"
        # Other offer should be rejected by default
        await db.refresh(offer1)
        assert offer1.status == "rejected"

    @pytest.mark.asyncio
    async def test_ai_returns_correct_positions(self, db, seed, mock_ai, mock_messaging):
        """AI returns correct positions [2]/[1] -> handler matches by position directly."""
        cleaner = seed["cleaner_a"]
        feb12 = datetime(2026, 2, 12, 10, 0, tzinfo=timezone.utc)
        feb26 = datetime(2026, 2, 26, 10, 0, tzinfo=timezone.utc)
        job1, offer1 = await _make_pending_job(db, batch_position=1, scheduled_date=feb12)
        job2, offer2 = await _make_pending_job(db, batch_position=2, scheduled_date=feb26)

        result = await process(
            db, cleaner, "Only the 26", mock_ai, mock_messaging,
            AIInterpretation(
                intent="partial_accept", confidence=90,
                accepted_job_ids=[2], rejected_job_ids=[1],
                suggested_response="Noted, you'll take the Feb 26 job.",
            ),
        )

        assert result["action_taken"] == "partial_accept"
        await db.refresh(offer2)
        assert offer2.status == "accepted"
        await db.refresh(offer1)
        assert offer1.status == "rejected"

    @pytest.mark.asyncio
    async def test_unmatched_positions_sends_clarification(self, db, seed, mock_ai, mock_messaging):
        """AI returns positions that match nothing -> asks for clarification."""
        cleaner = seed["cleaner_a"]
        job1, offer1 = await _make_pending_job(db, batch_position=1)
        job2, offer2 = await _make_pending_job(db, batch_position=2)

        # AI returned nonsense positions that don't match positions OR dates
        result = await process(
            db, cleaner, "Only the special one", mock_ai, mock_messaging,
            AIInterpretation(
                intent="partial_accept", confidence=60,
                accepted_job_ids=[99], rejected_job_ids=[],
                suggested_response="",
            ),
        )

        assert result["action_taken"] == "needs_clarification"
        # Should ask for clarification instead of going silent
        mock_messaging.send_to_cleaner.assert_called_once()
        sent_text = mock_messaging.send_to_cleaner.call_args[0][1]
        assert "which job" in sent_text.lower() or "1)" in sent_text

        # Neither offer touched
        await db.refresh(offer1)
        await db.refresh(offer2)
        assert offer1.status == "pending"
        assert offer2.status == "pending"

    @pytest.mark.asyncio
    async def test_partial_accept_empty_response_still_sends(self, db, seed, mock_ai, mock_messaging):
        """AI returns partial_accept with empty suggested_response -> handler still sends."""
        cleaner = seed["cleaner_a"]
        job1, offer1 = await _make_pending_job(db, batch_position=1)
        job2, offer2 = await _make_pending_job(db, batch_position=2)

        result = await process(
            db, cleaner, "Only the first one", mock_ai, mock_messaging,
            AIInterpretation(
                intent="partial_accept", confidence=85,
                accepted_job_ids=[1], rejected_job_ids=[2],
                suggested_response="",  # AI forgot to set response
            ),
        )

        assert result["action_taken"] == "partial_accept"
        # Handler should still send a fallback response
        mock_messaging.send_to_cleaner.assert_called_once()
        # "first one" = first in the displayed list (DESC order) = offer2
        await db.refresh(offer2)
        assert offer2.status == "accepted"
        await db.refresh(offer1)
        assert offer1.status == "rejected"
