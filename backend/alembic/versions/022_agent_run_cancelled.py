"""Add 'cancelled' to the agentstatus enum

Revision ID: 022
Revises: 021
Create Date: 2026-09-02 00:00:00

Pipeline runs could not be cancelled at all: once started, the only options
were to wait for the LLM call to finish and then delete the result. That
burns tokens on work the user has already said they don't want.

Both halves are changed together on purpose. Migration 004 added values to
the Postgres enum without updating the Python class, and the two drifted —
JobType.pdf_merge raised AttributeError and valid job types were rejected
with a 400 until it was found much later. Adding a value here without the
matching AgentStatus member (or vice versa) reproduces exactly that bug.

ADD VALUE IF NOT EXISTS is safe inside a transaction on PostgreSQL 12+ as
long as the new label isn't *used* in the same transaction — it isn't here.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE agentstatus ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    # PostgreSQL cannot drop a single enum label. Reversing this means
    # recreating the type and rewriting every dependent column, which would
    # destroy rows already carrying 'cancelled'. Left as a no-op deliberately
    # rather than implemented as something lossy that looks reversible.
    pass
