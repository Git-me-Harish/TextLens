"""Persist user_instructions on agent_runs

Revision ID: 023
Revises: 022
Create Date: 2026-09-02 00:00:00

The Instructions box on the Pipelines page genuinely shapes the model's
output — it's appended to the prompt as "Additional instructions from user:
{text}" — but the text itself was thrown away the moment the request was
sent. Once a run finished there was no way to see what instructions produced
it: not in the Results step, not in Pipeline History, nowhere. A user
revisiting a run a week later had no way to explain why the summary reads
the way it does.

BatchJob already has this column and returns it via BatchJobOut — this
brings the single-document run path to the same standard.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("user_instructions", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "user_instructions")
