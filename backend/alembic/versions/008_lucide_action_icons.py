"""
008 — Replace emoji action icons with lucide-react icon names

The `available_actions.icon` column originally stored an emoji glyph
(e.g. '📅'), rendered directly in the frontend. The rest of the app renders
icons via named lucide-react imports and never uses emoji — this brought the
new agentic UI back in line with that standard. The column now stores a
lucide-react icon component name (e.g. 'Calendar'), resolved client-side via
frontend/src/lib/actionIcons.jsx.
"""
from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

_ICON_MAP = {
    # Healthcare
    ("healthcare", "book_appointment"): "Calendar",
    ("healthcare", "order_medicines"): "Pill",
    ("healthcare", "create_medication_schedule"): "ClipboardList",
    ("healthcare", "explain_prescription"): "MessageCircle",
    ("healthcare", "medical_assistant"): "Bot",
    # Career / HR
    ("hr", "find_jobs"): "Search",
    ("hr", "apply_to_job"): "Send",
    ("hr", "optimize_resume"): "Sparkles",
    ("hr", "generate_interview_prep"): "Target",
    # Finance
    ("finance", "create_expense_entry"): "Wallet",
    ("finance", "validate_invoice"): "CheckCircle2",
    ("finance", "generate_financial_report"): "BarChart3",
    ("finance", "send_payment_reminder"): "Mail",
    # Legal
    ("legal", "summarize_document"): "FileText",
    ("legal", "extract_key_clauses"): "Scale",
    ("legal", "track_obligations"): "CalendarClock",
    ("legal", "document_qa"): "MessageCircle",
    # Government
    ("government", "summarize_filing"): "ClipboardList",
    ("government", "extract_obligations"): "CheckSquare",
    ("government", "flag_risks"): "AlertTriangle",
    ("government", "document_qa"): "MessageCircle",
    # Education
    ("education", "summarize_document"): "FileText",
    ("education", "generate_study_material"): "BookOpen",
    ("education", "document_qa"): "MessageCircle",
    ("education", "generate_quiz"): "ClipboardCheck",
    ("education", "create_learning_plan"): "Target",
}

_OLD_ICON_MAP = {
    ("healthcare", "book_appointment"): "📅",
    ("healthcare", "order_medicines"): "💊",
    ("healthcare", "create_medication_schedule"): "📋",
    ("healthcare", "explain_prescription"): "💬",
    ("healthcare", "medical_assistant"): "🤖",
    ("hr", "find_jobs"): "🔍",
    ("hr", "apply_to_job"): "📤",
    ("hr", "optimize_resume"): "✨",
    ("hr", "generate_interview_prep"): "🎯",
    ("finance", "create_expense_entry"): "💰",
    ("finance", "validate_invoice"): "✅",
    ("finance", "generate_financial_report"): "📊",
    ("finance", "send_payment_reminder"): "📧",
    ("legal", "summarize_document"): "📝",
    ("legal", "extract_key_clauses"): "⚖️",
    ("legal", "track_obligations"): "📅",
    ("legal", "document_qa"): "💬",
    ("government", "summarize_filing"): "📋",
    ("government", "extract_obligations"): "✅",
    ("government", "flag_risks"): "⚠️",
    ("government", "document_qa"): "💬",
    ("education", "summarize_document"): "📝",
    ("education", "generate_study_material"): "📚",
    ("education", "document_qa"): "💬",
    ("education", "generate_quiz"): "📋",
    ("education", "create_learning_plan"): "🎯",
}


_UPDATE_SQL = sa.text(
    "UPDATE available_actions SET icon = :icon "
    "WHERE domain = :domain AND action_type = :action_type"
)


def upgrade() -> None:
    conn = op.get_bind()
    for (domain, action_type), icon_name in _ICON_MAP.items():
        conn.execute(_UPDATE_SQL, {"icon": icon_name, "domain": domain, "action_type": action_type})


def downgrade() -> None:
    conn = op.get_bind()
    for (domain, action_type), emoji in _OLD_ICON_MAP.items():
        conn.execute(_UPDATE_SQL, {"icon": emoji, "domain": domain, "action_type": action_type})
