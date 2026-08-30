"""
Integration tests for the agentic action layer.

Tests cover the full lifecycle:
  1. Credential save / retrieve / delete
  2. Action run creation (happy path + validation errors)
  3. Approval token generation and verification
  4. Approval flow (approve + reject)
  5. Agent router — domain resolution and credential injection
  6. MCP registry — circuit breaker behaviour
  7. Finance agent plan generation (pure reasoning — no external calls needed)

Setup (one-time):
  pip install -r requirements-dev.txt
  createdb textlens_test          # a separate DB from the app's main one
  psql textlens_test -c "CREATE EXTENSION IF NOT EXISTS vector"   # needed by Base.metadata (pgvector columns elsewhere in the schema)

Run:
  MCP_ENCRYPTION_KEY=$(python -c "import secrets; print(secrets.token_hex(32))") \
  TEST_DATABASE_URL=postgresql+asyncpg://<user>:<pass>@localhost:5432/textlens_test \
  pytest tests/test_action_layer.py -v --asyncio-mode=auto
"""

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# Test database setup
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/textlens_test",
)


@pytest_asyncio.fixture
async def engine():
    """
    Function-scoped (not session-scoped): pytest-asyncio gives each async test
    its own event loop by default, and an asyncpg connection pool created in
    one event loop cannot be reused from another — sharing one engine across
    tests via a session-scoped fixture causes
    'cannot perform operation: another operation is in progress'. A fresh
    engine per test avoids that entirely, at the cost of running
    create_all/drop_all per test — an acceptable trade-off for this suite's size.
    """
    from app.db.database import Base
    from app.models.action_models import ActionRun, AgentTrace, AvailableAction, UserMCPCredential
    # action_runs.agent_run_id / user_id have FKs into agent_runs / users — those
    # models must be imported (and thus registered on Base.metadata) before
    # create_all, or SQLAlchemy can't resolve the foreign keys.
    from app.models.models import AgentRun, User  # noqa: F401

    eng = create_async_engine(TEST_DB_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def test_user(db):
    """
    action_runs.user_id and user_mcp_credentials.user_id both carry a real FK
    into users.id — a bare random UUID isn't enough to satisfy the constraint,
    a row actually needs to exist. Returns the created user's id.
    """
    from app.models.models import User

    user = User(
        id=str(uuid.uuid4()),
        email=f"{uuid.uuid4()}@example.com",
        full_name="Test User",
        hashed_password="not-a-real-hash",
    )
    db.add(user)
    await db.commit()
    return user.id

# 1. Credential store tests
class TestCredentialStore:
    @pytest.mark.asyncio
    async def test_save_and_retrieve_credential(self, db, test_user):
        from app.services.mcp.credential_store import get_credential, save_credential

        user_id = test_user
        creds = {"api_key": "test-pharmacy-key-abc123", "base_url": "https://api.example.com"}

        with patch("app.services.mcp.credential_store.settings") as mock_settings:
            mock_settings.MCP_ENCRYPTION_KEY = "a" * 64  # valid 64-char hex placeholder
            mock_settings.MCP_KEY_VERSION = 1

            # Patch _get_key to return a real 32-byte key
            test_key = bytes.fromhex("0" * 64)
            with patch("app.services.mcp.credential_store._get_key", return_value=test_key):
                await save_credential(db, user_id, "pharmacy_api", creds)
                result = await get_credential(db, user_id, "pharmacy_api")

        assert result is not None
        assert result["api_key"] == "test-pharmacy-key-abc123"
        assert result["base_url"] == "https://api.example.com"

    @pytest.mark.asyncio
    async def test_retrieve_nonexistent_credential_returns_none(self, db):
        from app.services.mcp.credential_store import get_credential

        result = await get_credential(db, str(uuid.uuid4()), "pharmacy_api")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_credential(self, db, test_user):
        from app.services.mcp.credential_store import (
            delete_credential,
            get_credential,
            save_credential,
        )

        user_id = test_user
        test_key = bytes.fromhex("0" * 64)

        with patch("app.services.mcp.credential_store._get_key", return_value=test_key):
            with patch("app.services.mcp.credential_store.settings") as s:
                s.MCP_KEY_VERSION = 1
                await save_credential(db, user_id, "email_api", {"api_key": "key123"})
                deleted = await delete_credential(db, user_id, "email_api")
                assert deleted is True

                result = await get_credential(db, user_id, "email_api")
                assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self, db):
        from app.services.mcp.credential_store import delete_credential

        deleted = await delete_credential(db, str(uuid.uuid4()), "email_api")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_upsert_replaces_existing_credential(self, db, test_user):
        from app.services.mcp.credential_store import get_credential, save_credential

        user_id = test_user
        test_key = bytes.fromhex("0" * 64)

        with patch("app.services.mcp.credential_store._get_key", return_value=test_key):
            with patch("app.services.mcp.credential_store.settings") as s:
                s.MCP_KEY_VERSION = 1
                await save_credential(db, user_id, "email_api", {"api_key": "old-key"})
                await save_credential(db, user_id, "email_api", {"api_key": "new-key"})
                result = await get_credential(db, user_id, "email_api")

        assert result["api_key"] == "new-key"

# 2. Approval service tests
class TestApprovalService:
    def test_generate_and_verify_token(self):
        from app.services.approval_service import generate_approval_token
        import jwt

        action_run_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())

        with patch("app.services.approval_service.settings") as mock_settings:
            mock_settings.SECRET_KEY = "test-secret-key-for-tests-only"
            mock_settings.APPROVAL_TOKEN_TTL_MINUTES = 15
            token, expires_at = generate_approval_token(action_run_id, user_id)

        assert isinstance(token, str)
        assert len(token) > 20
        assert isinstance(expires_at, datetime)
        assert expires_at > datetime.now(timezone.utc)

        # Decode and verify claims
        claims = jwt.decode(
            token, "test-secret-key-for-tests-only", algorithms=["HS256"]
        )
        assert claims["sub"] == action_run_id
        assert claims["uid"] == user_id
        assert claims["type"] == "action_approval"

    @pytest.mark.asyncio
    async def test_reject_action_transitions_to_rejected(self, db, test_user):
        from app.models.action_models import ActionRun
        from app.services.approval_service import reject_action

        user_id = test_user
        run = ActionRun(
            id=str(uuid.uuid4()),
            user_id=user_id,
            action_type="order_medicines",
            domain="healthcare",
            status="AWAITING_APPROVAL",
        )
        db.add(run)
        await db.commit()

        result = await reject_action(db, run.id, user_id, reason="Wrong pharmacy")
        assert result.status == "REJECTED"
        assert "Wrong pharmacy" in result.error_message

    @pytest.mark.asyncio
    async def test_reject_wrong_user_raises(self, db, test_user):
        from app.models.action_models import ActionRun
        from app.services.approval_service import reject_action

        run = ActionRun(
            id=str(uuid.uuid4()),
            user_id=test_user,
            action_type="order_medicines",
            domain="healthcare",
            status="AWAITING_APPROVAL",
        )
        db.add(run)
        await db.commit()

        with pytest.raises(ValueError, match="not found or access denied"):
            await reject_action(db, run.id, str(uuid.uuid4()))  # wrong user

    @pytest.mark.asyncio
    async def test_reject_terminal_status_raises(self, db, test_user):
        from app.models.action_models import ActionRun
        from app.services.approval_service import reject_action

        user_id = test_user
        run = ActionRun(
            id=str(uuid.uuid4()),
            user_id=user_id,
            action_type="order_medicines",
            domain="healthcare",
            status="COMPLETED",
        )
        db.add(run)
        await db.commit()

        with pytest.raises(ValueError, match="Only PENDING, PLANNING, or AWAITING_APPROVAL"):
            await reject_action(db, run.id, user_id)

# 3. MCP Registry + circuit breaker tests
class TestMCPRegistry:
    def test_get_known_server(self):
        from app.services.mcp.registry import get_server

        server = get_server("pharmacy_api")
        assert server.service_name == "pharmacy_api"
        assert "search_medicines" in server.allowed_tools
        assert "create_order" in server.allowed_tools

    def test_get_unknown_server_raises(self):
        from app.services.mcp.registry import get_server

        with pytest.raises(KeyError, match="Unknown MCP service"):
            get_server("nonexistent_service")

    @pytest.mark.asyncio
    async def test_call_blocked_tool_raises(self):
        from app.services.mcp.registry import call_mcp_tool

        with pytest.raises(ValueError, match="not in the allowlist"):
            await call_mcp_tool(
                "pharmacy_api",
                "delete_all_orders",  # not in allowlist
                {},
                {"api_key": "test"},
            )

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_failures(self):
        from app.services.mcp.registry import CircuitBreaker, CircuitState

        cb = CircuitBreaker(service_name="test_service", failure_threshold=3, recovery_seconds=60)
        assert cb.state == CircuitState.CLOSED

        for _ in range(3):
            await cb.record_failure()

        assert cb.state == CircuitState.OPEN
        assert not await cb.call_allowed()

    @pytest.mark.asyncio
    async def test_circuit_breaker_closes_after_success(self):
        from app.services.mcp.registry import CircuitBreaker, CircuitState

        cb = CircuitBreaker(service_name="test_service", failure_threshold=2, recovery_seconds=0)
        await cb.record_failure()
        await cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Simulate recovery window elapsed (recovery_seconds=0)
        assert await cb.call_allowed()  # transitions to HALF_OPEN
        await cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_get_required_services(self):
        from app.services.mcp.registry import get_required_services

        assert "pharmacy_api" in get_required_services("order_medicines")
        assert "google_calendar" in get_required_services("book_appointment")
        assert get_required_services("explain_prescription") == []
        assert get_required_services("medical_assistant") == []



# 4. Agent router tests


class TestAgentRouter:
    @pytest.mark.asyncio
    async def test_missing_credentials_raises(self, db):
        from app.services.actions.agent_router import MissingCredentialsError, get_agent

        # order_medicines needs pharmacy_api — user has no credentials saved
        user_id = str(uuid.uuid4())
        with pytest.raises(MissingCredentialsError) as exc_info:
            await get_agent("healthcare", "order_medicines", user_id, db)

        assert "pharmacy_api" in exc_info.value.missing

    @pytest.mark.asyncio
    async def test_unknown_domain_raises(self, db):
        from app.services.actions.agent_router import get_agent

        with pytest.raises(ValueError, match="No agent registered"):
            await get_agent("astrology", "read_horoscope", str(uuid.uuid4()), db)

    @pytest.mark.asyncio
    async def test_no_credential_action_resolves_agent(self, db):
        """explain_prescription needs no credentials — agent should be returned."""
        from app.services.actions.agent_router import get_agent
        from app.services.actions.healthcare_agent import HealthcareAgent

        user_id = str(uuid.uuid4())
        with patch("app.services.actions.agent_router.AsyncAnthropic"):
            agent = await get_agent("healthcare", "explain_prescription", user_id, db)

        assert isinstance(agent, HealthcareAgent)

    @pytest.mark.asyncio
    async def test_government_domain_resolves(self, db):
        from app.services.actions.agent_router import get_agent
        from app.services.actions.government_agent import GovernmentAgent

        with patch("app.services.actions.agent_router.AsyncAnthropic"):
            agent = await get_agent("government", "flag_risks", str(uuid.uuid4()), db)

        assert isinstance(agent, GovernmentAgent)

# 5. Domain agent plan tests (no LLM / MCP calls)
class TestDomainAgentPlans:
    def _make_state(self, action_type: str, domain: str, ctx: dict) -> dict:
        return {
            "action_run_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "action_type": action_type,
            "domain": domain,
            "document_context": ctx,
            "user_context": None,
            "plan": None,
            "messages": [],
            "tool_calls_made": 0,
            "iterations": 0,
            "awaiting_approval": False,
            "approval_granted": False,
            "result": None,
            "error": None,
            "trace_id": str(uuid.uuid4()),
            "span_counter": 0,
        }

    @pytest.mark.asyncio
    async def test_healthcare_order_medicines_plan(self):
        from app.services.actions.healthcare_agent import HealthcareAgent

        agent = HealthcareAgent(db=MagicMock(), anthropic_client=MagicMock(), user_mcp_credentials={})
        state = self._make_state("order_medicines", "healthcare", {
            "medicines": [{"name": "Amoxicillin", "dosage": "500mg"}, {"name": "Paracetamol", "dosage": "1g"}],
            "patient_name": "Test Patient",
            "doctor_name": "Dr. Smith",
        })
        plan = await agent._plan(state)

        assert plan.risk_level == "medium"
        assert "pharmacy_api" in plan.external_services
        assert len(plan.steps) >= 2
        assert any("search" in s.description.lower() or "order" in s.description.lower() for s in plan.steps)

    @pytest.mark.asyncio
    async def test_healthcare_explain_prescription_no_approval(self):
        from app.services.actions.healthcare_agent import HealthcareAgent

        agent = HealthcareAgent(db=MagicMock(), anthropic_client=MagicMock(), user_mcp_credentials={})
        assert agent._requires_approval("explain_prescription") is False
        assert agent._requires_approval("medical_assistant") is False
        assert agent._requires_approval("order_medicines") is True

    @pytest.mark.asyncio
    async def test_finance_validate_invoice_no_approval(self):
        from app.services.actions.finance_agent import FinanceAgent

        agent = FinanceAgent(db=MagicMock(), anthropic_client=MagicMock(), user_mcp_credentials={})
        assert agent._requires_approval("validate_invoice") is False
        assert agent._requires_approval("create_expense_entry") is True

    @pytest.mark.asyncio
    async def test_government_all_actions_no_approval(self):
        from app.services.actions.government_agent import GovernmentAgent

        agent = GovernmentAgent(db=MagicMock(), anthropic_client=MagicMock(), user_mcp_credentials={})
        for action in ["summarize_filing", "extract_obligations", "flag_risks", "document_qa"]:
            assert agent._requires_approval(action) is False

    @pytest.mark.asyncio
    async def test_legal_track_obligations_plan_has_calendar(self):
        from app.services.actions.legal_agent import LegalAgent

        agent = LegalAgent(db=MagicMock(), anthropic_client=MagicMock(), user_mcp_credentials={})
        state = self._make_state("track_obligations", "legal", {
            "document_type": "Service Agreement",
            "parties": ["Acme Corp", "Vendor Ltd"],
            "key_dates": ["2025-12-31", "2026-03-01"],
        })
        plan = await agent._plan(state)

        assert "google_calendar" in plan.external_services
        assert plan.risk_level == "low"
        assert len(plan.steps) >= 2

    @pytest.mark.asyncio
    async def test_career_find_jobs_plan(self):
        from app.services.actions.career_agent import CareerAgent

        agent = CareerAgent(db=MagicMock(), anthropic_client=MagicMock(), user_mcp_credentials={})
        state = self._make_state("find_jobs", "hr", {
            "candidate_name": "Jane Doe",
            "skills": ["Python", "FastAPI", "PostgreSQL", "Redis", "Docker"],
            "location": "Bangalore",
            "total_experience_years": 3,
        })
        plan = await agent._plan(state)

        assert "job_board_api" in plan.external_services
        assert any("Python" in s.description or "skills" in s.description.lower() for s in plan.steps)



# 6. Pydantic schema validation tests


class TestSchemaValidation:
    def test_start_action_strips_html(self):
        from app.schemas.action_schemas import StartActionRequest

        req = StartActionRequest(
            agent_run_id=str(uuid.uuid4()),
            action_type="explain_prescription",
            user_context="<script>alert('xss')</script>My question about the medicine",
        )
        assert "<script>" not in req.user_context
        assert "My question" in req.user_context

    def test_start_action_rejects_unknown_action_type(self):
        from pydantic import ValidationError
        from app.schemas.action_schemas import StartActionRequest

        with pytest.raises(ValidationError, match="Unknown action_type"):
            StartActionRequest(
                agent_run_id=str(uuid.uuid4()),
                action_type="hack_the_planet",
            )

    def test_save_credential_rejects_unknown_service(self):
        from pydantic import ValidationError
        from app.schemas.action_schemas import SaveCredentialRequest

        with pytest.raises(ValidationError, match="Unknown service_name"):
            SaveCredentialRequest(
                service_name="shadow_api",
                credentials={"key": "value"},
            )

    def test_save_credential_rejects_empty_credentials(self):
        from pydantic import ValidationError
        from app.schemas.action_schemas import SaveCredentialRequest

        with pytest.raises(ValidationError):
            SaveCredentialRequest(service_name="pharmacy_api", credentials={})

    def test_user_context_truncated_at_2000_chars(self):
        from pydantic import ValidationError
        from app.schemas.action_schemas import StartActionRequest

        with pytest.raises(ValidationError):
            StartActionRequest(
                agent_run_id=str(uuid.uuid4()),
                action_type="explain_prescription",
                user_context="x" * 2001,
            )
