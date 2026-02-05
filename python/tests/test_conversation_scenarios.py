"""
Comprehensive test suite for WhatsApp cleaner conversation handling.

Tests all paths through _process_cleaner_message with a real in-memory
SQLite database and mocked AI / messaging services.

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
  - Multiple pending offers -> multi-job confirmation prompt
  - Unclear message with pending offers -> clarification
  - Reject with no pending and no active jobs -> no action
  - Keyword fallback when OpenAI API fails
  - Conversation context tracking (inbound + outbound recorded)
  - Full multi-turn conversation flows
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


async def _make_pending_job(db, cleaner_id=1, property_id=1, batch_position=1):
    """Create a job in OFFERED status with a pending offer."""
    job = Job(
        property_id=property_id, job_type="turnover",
        status=JobStatus.OFFERED.value, urgency="normal",
        scheduled_date=datetime.now(timezone.utc) + timedelta(days=2),
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
    async def test_accept_two_offers_asks_confirmation(self, db, seed, mock_ai, mock_messaging):
        """With 2 pending offers, 'Yes' should ask to confirm which ones."""
        cleaner = seed["cleaner_a"]
        await _make_pending_job(db, cleaner_id=1, batch_position=1)
        await _make_pending_job(db, cleaner_id=1, batch_position=2)

        result = await process(
            db, cleaner, "Yes to all", mock_ai, mock_messaging,
            AIInterpretation(
                intent="accept_job", confidence=90,
                suggested_response="Confirmed for both.",
            ),
        )

        assert result["action_taken"] == "awaiting_multi_job_confirmation"
        assert result["details"]["pending_count"] == 2


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
        assert 1 in result["details"]["accepted"]
        assert 2 in result["details"]["rejected"]


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
        """'Yes' with NO pending -> should NOT be accept_job."""
        result = self._fallback("Yes", [])
        assert result.intent != "accept_job"

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
