"""Add submission_confirmation email template.

Adds a BUDGET-only confirmation email sent to all members of the
submitting department when a budget request transitions out of DRAFT.
The existing 'submitted' template goes to budget admins (so they can
dispatch); this new template tells the department itself that the
request landed, with a line count and total requested dollars for the
paper trail. Wording uses an explicit "requested" framing so the
amounts are not mistaken for an approval.

NOTE: Revision id deliberately skips 'z6a7b8c9d0e1' because the
unmerged 'feature/email-system' branch already uses that id for an
unrelated migration (scheduled_notifications). Picking a distinct id
keeps the two branches mergeable in either order without rebasing
the revision chain.

Revision ID: a8b9c0d1e2f3
Revises: y5z6a7b8c9d0
Create Date: 2026-05-20

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8b9c0d1e2f3'
down_revision = 'y5z6a7b8c9d0'
branch_labels = None
depends_on = None


TEMPLATE_KEY = 'submission_confirmation'

SUBJECT = '[MAGFest Budget] Submission received - {{ work_item.public_id }}'

BODY_TEXT = '''Your budget request was submitted and is waiting for a budget admin to dispatch it for review.

Request: {{ work_item.public_id }}
Department: {{ work_item.portfolio.department.name }}
Event: {{ work_item.portfolio.event_cycle.name }}
{% if work_item.reason %}Reason: {{ work_item.reason }}
{% endif %}
Submitted: {{ line_count }} line{{ 's' if line_count != 1 else '' }} totaling ${{ '%.2f'|format(total_requested_dollars) }} requested.

These amounts are what your department requested — they have not been approved yet. A budget admin will dispatch the request to reviewers, and you'll get another email if any lines need your attention.

View the request:
{{ base_url }}/work/{{ work_item.portfolio.event_cycle.code }}/{{ work_item.portfolio.department.code }}/budget/item/{{ work_item.public_id }}
'''


def upgrade():
    email_templates = sa.table(
        'email_templates',
        sa.column('template_key', sa.String),
        sa.column('name', sa.String),
        sa.column('description', sa.Text),
        sa.column('subject', sa.String),
        sa.column('body_text', sa.Text),
        sa.column('is_active', sa.Boolean),
        sa.column('version', sa.Integer),
    )

    op.bulk_insert(email_templates, [
        {
            'template_key': TEMPLATE_KEY,
            'name': 'Budget Submission Confirmation',
            'description': (
                'Sent to all members of the submitting department when a BUDGET '
                'request leaves DRAFT. Includes line count and total requested '
                'dollars. Does not fire for non-BUDGET worktypes.'
            ),
            'subject': SUBJECT,
            'body_text': BODY_TEXT,
            'is_active': True,
            'version': 1,
        },
    ])


def downgrade():
    op.execute(
        sa.text("DELETE FROM email_templates WHERE template_key = :key").bindparams(
            key=TEMPLATE_KEY
        )
    )
