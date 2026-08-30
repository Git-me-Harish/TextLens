"""
007 — Drop unused langgraph_thread_id column

action_runs.langgraph_thread_id was added in migration 005 for a LangGraph
StateGraph checkpoint mechanism that was never actually implemented — the
agent execution loop in base_agent.py is a hand-rolled async ReAct loop, not
a LangGraph graph, and nothing in the codebase ever reads or writes this
column. Dropping it rather than leaving it as misleading dead schema.
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_action_runs_langgraph_thread_id", table_name="action_runs")
    op.drop_column("action_runs", "langgraph_thread_id")


def downgrade() -> None:
    op.add_column(
        "action_runs",
        sa.Column("langgraph_thread_id", sa.String(256), nullable=True),
    )
    op.create_index(
        "ix_action_runs_langgraph_thread_id", "action_runs", ["langgraph_thread_id"]
    )
