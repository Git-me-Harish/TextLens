"""
Agent Router

Single entry point that maps a (domain, action_type) pair to the correct
domain agent, instantiates it with the user's decrypted MCP credentials,
and returns it ready to run.

Design rules:
  - All credential lookups happen HERE before the agent is instantiated.
    The agent never touches the DB for credentials — they are injected.
  - If required credentials are missing for a write action, fail fast with
    a clear ActionableError before any LLM call is made.
  - Only the services actually needed for the action_type are fetched,
    minimising credential surface.
"""

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.actions.base_agent import BaseAgent
from app.services.actions.career_agent import CareerAgent
from app.services.actions.education_agent import EducationAgent
from app.services.actions.finance_agent import FinanceAgent
from app.services.actions.government_agent import GovernmentAgent
from app.services.actions.healthcare_agent import HealthcareAgent
from app.services.actions.legal_agent import LegalAgent
from app.services.mcp.credential_store import get_credential
from app.services.mcp.registry import get_required_services, get_server

import structlog

logger = structlog.get_logger(__name__)

# domain → agent class
_DOMAIN_AGENT_MAP: dict[str, type[BaseAgent]] = {
    "healthcare": HealthcareAgent,
    "hr":         CareerAgent,
    "finance":    FinanceAgent,
    "legal":      LegalAgent,
    "government": GovernmentAgent,
    "education":  EducationAgent,
}


class MissingCredentialsError(Exception):
    """Raised when required MCP credentials are not connected for an action."""
    def __init__(self, action_type: str, missing: list[str]):
        self.action_type = action_type
        self.missing = missing
        super().__init__(
            f"Action '{action_type}' requires the following connected services "
            f"that are not yet configured: {', '.join(missing)}. "
            f"Please connect them under Settings → Integrations."
        )


async def get_agent(
    domain: str,
    action_type: str,
    user_id: str,
    db: AsyncSession,
) -> BaseAgent:
    """
    Build and return the correct domain agent for the given action.

    Steps:
      1. Resolve domain → agent class.
      2. Determine which MCP services are needed for this action.
      3. Fetch and decrypt only those credentials from the DB.
      4. Fail fast if any required service has no credential saved.
      5. Instantiate and return the agent with injected credentials.
    """
    agent_class = _DOMAIN_AGENT_MAP.get(domain)
    if not agent_class:
        raise ValueError(
            f"No agent registered for domain '{domain}'. "
            f"Supported domains: {sorted(_DOMAIN_AGENT_MAP.keys())}"
        )

    required_services = get_required_services(action_type)
    user_mcp_credentials: dict[str, dict] = {}
    missing_services: list[str] = []

    for service_name in required_services:
        # Services with no per-user credential (e.g. email_api, which sends
        # through the platform's own Resend account) never block on a missing
        # connection — there's nothing for the user to connect.
        if get_server(service_name).credential_key is None:
            continue
        creds = await get_credential(db, user_id, service_name)
        if creds is None:
            missing_services.append(service_name)
        else:
            user_mcp_credentials[service_name] = creds

    if missing_services:
        logger.warning(
            "agent_router.missing_credentials",
            user_id=user_id,
            action_type=action_type,
            missing=missing_services,
        )
        raise MissingCredentialsError(action_type, missing_services)

    anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    logger.info(
        "agent_router.agent_resolved",
        domain=domain,
        action_type=action_type,
        agent_class=agent_class.__name__,
        services_loaded=list(user_mcp_credentials.keys()),
    )

    return agent_class(
        db=db,
        anthropic_client=anthropic_client,
        user_mcp_credentials=user_mcp_credentials,
    )


def get_supported_domains() -> list[str]:
    return sorted(_DOMAIN_AGENT_MAP.keys())


def get_agent_class(domain: str) -> type[BaseAgent] | None:
    return _DOMAIN_AGENT_MAP.get(domain)
