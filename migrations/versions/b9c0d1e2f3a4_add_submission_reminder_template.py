"""Add submission_reminder email template.

Adds a BUDGET-only reminder template sent by the manually-triggered
`flask send-submission-reminders <event_code>` CLI command to every
department that has not yet started a PRIMARY BUDGET submission.

Deadline date and time zone are hard-coded as literal text in both
subject and body. This is an emergency-stopgap template — if the
deadline shifts, edit the row via the admin template editor.

NOTE: Revision id deliberately picks 'b9c0d1e2f3a4' to stay clear of
the unmerged feature/email-system branch's reserved id 'a7b8c9d0e1f2'
and the already-merged 'a8b9c0d1e2f3'. Per feedback_alembic_revision_collisions.

Revision ID: b9c0d1e2f3a4
Revises: 33f688ff7587
Create Date: 2026-05-21

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b9c0d1e2f3a4'
down_revision = '33f688ff7587'
branch_labels = None
depends_on = None


TEMPLATE_KEY = 'submission_reminder'

SUBJECT = '[MAGFest Budget] Reminder: {{ event_cycle.name }} budget due Sunday May 24'

BODY_TEXT = '''Your department hasn't submitted its {{ event_cycle.name }} budget yet.

Department: {{ department.name }} ({{ department.code }})
Submission deadline: Sunday, May 24, 2026 at 11:59 PM ET

The budget request must be submitted before the deadline so it can be reviewed and approved in time for the event.

Open your department's budget portfolio to start or finish your submission:
{{ base_url }}/{{ event_cycle.code }}/{{ department.code }}/budget

If your department's budget has already been submitted and you're still seeing this reminder, please reply so we can investigate.
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
            'name': 'Budget Submission Reminder',
            'description': (
                'Sent by the manually-triggered `flask send-submission-reminders` '
                'CLI to every department that has not yet started a PRIMARY BUDGET '
                'submission for the named event. Deadline date is hard-coded in '
                'the body; edit this template row to change the wording.'
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
