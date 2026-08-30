"""
006 — Education Domain Action Catalog

Seeds the `available_actions` catalog for the 'education' domain, which was
part of the original agentic action layer design (idea.md domain #5:
Education & Knowledge) but was missing from migration 005.

Adds the EducationAgent's five actions: summarize_document,
generate_study_material, document_qa, generate_quiz, create_learning_plan.
All are pure-reasoning actions — no MCP credentials required.
"""
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO available_actions
            (domain, action_type, label, description, requires_credentials, icon, sort_order)
        VALUES
        ('education', 'summarize_document',
         'Summarize Document',
         'Produce a clear summary of the key points, terms, and topic of this document.',
         '[]', '📝', 1),
        ('education', 'generate_study_material',
         'Generate Study Material',
         'Create revision notes, key concept definitions, and flashcards from this document.',
         '[]', '📚', 2),
        ('education', 'document_qa',
         'Ask Questions About This Document',
         'Chat with the document — get tutor-style answers grounded in its content.',
         '[]', '💬', 3),
        ('education', 'generate_quiz',
         'Generate Quiz / Assessment',
         'Create a quiz with explanations to test understanding of this document.',
         '[]', '📋', 4),
        ('education', 'create_learning_plan',
         'Create Learning Plan',
         'Build a milestone-based learning plan to master the material in this document.',
         '[]', '🎯', 5)
    """)


def downgrade() -> None:
    op.execute("DELETE FROM available_actions WHERE domain = 'education'")
