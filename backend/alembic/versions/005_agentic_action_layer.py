"""
004 — Agentic Action Layer

New tables:
  - available_actions       catalog of domain-specific actions
  - action_runs             lifecycle record for each user-initiated action
  - agent_traces            immutable per-step audit trail
  - user_mcp_credentials    encrypted external service credentials

RLS is enforced via application-set session variable (app.current_tenant_id).
The migration adds RLS policies to action_runs and user_mcp_credentials.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, BYTEA

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── available_actions (catalog table — no RLS needed, admin-managed) ─
    op.create_table(
        "available_actions",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("domain", sa.String(50), nullable=False, index=True),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("requires_credentials", JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb"),
                  comment="Array of service_name strings user must have connected"),
        sa.Column("icon", sa.String(50), nullable=True),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("domain", "action_type", name="uq_available_actions_domain_type"),
    )

    # ── action_runs ───────────────────────────────────────────────────────
    op.create_table(
        "action_runs",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("agent_run_id", UUID(as_uuid=False),
                  sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("domain", sa.String(50), nullable=False),
        # State machine: PENDING → PLANNING → AWAITING_APPROVAL → EXECUTING → COMPLETED
        #                                   ↘ REJECTED
        #                PLANNING → FAILED
        #                EXECUTING → FAILED
        #                Any → CANCELLED
        sa.Column("status", sa.String(30), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("plan", JSONB, nullable=True,
                  comment="Structured plan produced by the planner node before execution"),
        sa.Column("approval_required", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("approval_token", sa.String(512), nullable=True,
                  comment="Short-lived signed JWT scoped to this action_run_id"),
        sa.Column("approval_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("approved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("action_result", JSONB, nullable=True,
                  comment="Final structured result from the agent execution"),
        sa.Column("user_context", sa.Text, nullable=True,
                  comment="Optional free-text context provided by the user at action start"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("langgraph_thread_id", sa.String(256), nullable=True, index=True,
                  comment="LangGraph checkpoint thread ID for resumption after HITL"),
        sa.Column("total_llm_calls", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("total_tool_calls", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("total_tokens_used", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_action_runs_user_status",
                    "action_runs", ["user_id", "status"])
    op.create_index("ix_action_runs_domain_type",
                    "action_runs", ["domain", "action_type"])

    # RLS on action_runs
    op.execute("ALTER TABLE action_runs ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY action_runs_tenant_isolation ON action_runs
        USING (user_id = current_setting('app.current_tenant_id', true)::uuid)
    """)

    # ── agent_traces (append-only audit log) ─────────────────────────────
    op.create_table(
        "agent_traces",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("action_run_id", UUID(as_uuid=False),
                  sa.ForeignKey("action_runs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("span_id", sa.String(128), nullable=False),
        sa.Column("parent_span_id", sa.String(128), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False,
                  comment="llm_call|tool_call|memory_read|hitl_gate|handoff|error"),
        sa.Column("tool_name", sa.String(128), nullable=True),
        # Previews are truncated at application layer — never store raw PII
        sa.Column("input_preview", sa.Text, nullable=True),
        sa.Column("output_preview", sa.Text, nullable=True),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("success", sa.Boolean, nullable=True),
        sa.Column("error_msg", sa.Text, nullable=True),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    # No UPDATE/DELETE on agent_traces — append-only enforced by revoke below
    op.execute("REVOKE UPDATE, DELETE ON agent_traces FROM PUBLIC")

    # ── user_mcp_credentials (encrypted external credentials) ─────────────
    op.create_table(
        "user_mcp_credentials",
        sa.Column("id", UUID(as_uuid=False), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("service_name", sa.String(100), nullable=False,
                  comment="e.g. google_calendar, pharmacy_api, job_board_api"),
        # AES-256-GCM encrypted JSON blob containing the service credentials
        sa.Column("encrypted_credentials", BYTEA, nullable=False),
        sa.Column("iv", BYTEA, nullable=False,
                  comment="AES-GCM initialization vector — unique per row"),
        sa.Column("key_version", sa.Integer, nullable=False, server_default=sa.text("1"),
                  comment="Enables key rotation without re-encrypting all rows at once"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "service_name", name="uq_user_mcp_credentials_user_service"),
    )
    op.execute("ALTER TABLE user_mcp_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY user_mcp_credentials_tenant_isolation ON user_mcp_credentials
        USING (user_id = current_setting('app.current_tenant_id', true)::uuid)
    """)

    # ── Seed available_actions catalog ────────────────────────────────────
    op.execute("""
        INSERT INTO available_actions
            (domain, action_type, label, description, requires_credentials, icon, sort_order)
        VALUES
        -- Healthcare
        ('healthcare', 'book_appointment',
         'Book Medical Appointment',
         'Schedule a follow-up or specialist appointment using details from the document.',
         '["google_calendar"]', '📅', 1),
        ('healthcare', 'order_medicines',
         'Order Prescribed Medicines',
         'Send the prescription to a connected pharmacy and track the order.',
         '["pharmacy_api"]', '💊', 2),
        ('healthcare', 'create_medication_schedule',
         'Create Medication Schedule',
         'Generate a structured daily medication reminder schedule from the prescription.',
         '[]', '📋', 3),
        ('healthcare', 'explain_prescription',
         'Explain Prescription',
         'Get a plain-language explanation of the prescription, dosages, and instructions.',
         '[]', '💬', 4),
        ('healthcare', 'medical_assistant',
         'Personal Medical Assistant',
         'Ask any question about this medical document and get expert AI guidance.',
         '[]', '🤖', 5),

        -- Career / HR
        ('hr', 'find_jobs',
         'Find Relevant Jobs',
         'Search for job opportunities matching the skills and experience in this document.',
         '["job_board_api"]', '🔍', 1),
        ('hr', 'apply_to_job',
         'Apply to a Job',
         'Prepare and submit a job application tailored to the resume or profile.',
         '["job_board_api", "email_api"]', '📤', 2),
        ('hr', 'optimize_resume',
         'Optimize Resume',
         'Analyse the resume against a job description and suggest targeted improvements.',
         '[]', '✨', 3),
        ('hr', 'generate_interview_prep',
         'Generate Interview Prep',
         'Create a personalized interview preparation plan based on the resume and target role.',
         '[]', '🎯', 4),

        -- Finance
        ('finance', 'create_expense_entry',
         'Create Expense Entry',
         'Push the extracted invoice or receipt data into a connected accounting system.',
         '["accounting_api"]', '💰', 1),
        ('finance', 'validate_invoice',
         'Validate Invoice',
         'Cross-check the invoice against known supplier rates and flag anomalies.',
         '[]', '✅', 2),
        ('finance', 'generate_financial_report',
         'Generate Financial Report',
         'Summarise the extracted financial data into a formatted report.',
         '[]', '📊', 3),
        ('finance', 'send_payment_reminder',
         'Send Payment Reminder',
         'Draft and send a payment reminder email for an overdue invoice.',
         '["email_api"]', '📧', 4),

        -- Legal
        ('legal', 'summarize_document',
         'Summarize Legal Document',
         'Produce an executive summary of the key terms, parties, and obligations.',
         '[]', '📝', 1),
        ('legal', 'extract_key_clauses',
         'Extract Key Clauses',
         'Pull out the most legally significant clauses with risk ratings.',
         '[]', '⚖️', 2),
        ('legal', 'track_obligations',
         'Track Obligations & Deadlines',
         'Create calendar entries for all key dates and deadlines found in the document.',
         '["google_calendar"]', '📅', 3),
        ('legal', 'document_qa',
         'Ask Questions About This Document',
         'Chat with the legal document — get answers about any clause or term.',
         '[]', '💬', 4),

        -- Government
        ('government', 'summarize_filing',
         'Summarize Filing',
         'Produce a plain-language summary of the government form or filing.',
         '[]', '📋', 1),
        ('government', 'extract_obligations',
         'Extract Obligations',
         'List all compliance obligations, deadlines, and required actions.',
         '[]', '✅', 2),
        ('government', 'flag_risks',
         'Flag Compliance Risks',
         'Identify potential compliance gaps or missing information in the filing.',
         '[]', '⚠️', 3),
        ('government', 'document_qa',
         'Ask Questions About This Document',
         'Chat with the government document for guided compliance assistance.',
         '[]', '💬', 4)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_traces CASCADE")
    op.execute("DROP TABLE IF EXISTS action_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS user_mcp_credentials CASCADE")
    op.execute("DROP TABLE IF EXISTS available_actions CASCADE")
