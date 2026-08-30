"""
010 — Add the missing 'Match Resume Against Jobs' career action

idea.md's Career & Recruitment domain lists five actions: Find Relevant Jobs,
Match Resume Against Jobs, Apply to Jobs, Optimize Resume, Generate Interview
Preparation Plan. Only four were ever seeded — "match resume against a job"
existed only as an internal tool inside find_jobs, never as its own
selectable action. Adds it as a standalone, no-credential (pure reasoning)
action — see career_agent.py's ACTION_PROMPTS["match_resume"].
"""
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO available_actions
            (domain, action_type, label, description, requires_credentials, icon, sort_order)
        VALUES
        ('hr', 'match_resume',
         'Match Resume Against a Job',
         'Score how well this resume fits a specific job description you provide, with a skills gap analysis.',
         '[]', 'Percent', 2)
    """)
    # find_jobs / apply_to_job / optimize_resume / generate_interview_prep were seeded at
    # sort_order 1/2/3/4 in migration 005 — bump apply_to_job onward to make room at position 2.
    op.execute("""
        UPDATE available_actions SET sort_order = sort_order + 1
        WHERE domain = 'hr' AND action_type IN ('apply_to_job', 'optimize_resume', 'generate_interview_prep')
    """)


def downgrade() -> None:
    op.execute("DELETE FROM available_actions WHERE domain = 'hr' AND action_type = 'match_resume'")
    op.execute("""
        UPDATE available_actions SET sort_order = sort_order - 1
        WHERE domain = 'hr' AND action_type IN ('apply_to_job', 'optimize_resume', 'generate_interview_prep')
    """)
