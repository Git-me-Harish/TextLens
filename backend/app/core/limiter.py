"""
Shared slowapi Limiter instance.

Split out from main.py so route modules (e.g. chat.py, which needs a
tighter per-route limit on its LLM-calling endpoint) can import it
without a circular import back through main.py, which itself imports
every route module.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.RATE_LIMIT_PER_MINUTE}/minute"],
)
