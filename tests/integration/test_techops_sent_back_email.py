"""The techops_sent_back email row and the notify_sent_back enqueue."""
import json
from datetime import datetime

from app import db
from app.models import EmailOutbox, EmailTemplate, TechOpsRequestDetail
from app.services.notifications import (
    notify_sent_back,
    notify_submission_confirmation,
)
from migrations.versions.sb7q2m4x9k1d_techops_sent_back_email import (
    seed_sent_back_template,
)
from migrations.versions.ws8841d5f27c_techops_supply_email_templates import (
    seed_work_type_templates,
)
from tests.integration.techops_review_helpers import grant_department_access
from tests.integration.test_techops_review_affordances import techops_review  # noqa: F401


def _seed():
    seed_sent_back_template(db.session.connection())
    db.session.commit()


def _add_contact(item, email):
    db.session.add(TechOpsRequestDetail(
        work_item_id=item.id, primary_contact_name="Contact",
        primary_contact_email=email,
    ))
    db.session.commit()


def test_migration_seeds_sent_back_once(app):
    _seed()
    _seed()
    rows = EmailTemplate.query.filter_by(template_key="techops_sent_back").all()
    assert len(rows) == 1
    assert rows[0].is_active is True
    assert rows[0].subject.startswith("[MAGFest TechOps]")
    assert "{{ reason }}" in rows[0].body_text


def test_migration_keeps_edited_wording(app):
    now = datetime.utcnow()
    db.session.add(EmailTemplate(
        template_key="techops_sent_back", name="Edited", subject="Edited",
        body_text="Edited", is_active=True, version=1,
        created_at=now, updated_at=now,
    ))
    db.session.commit()
    _seed()
    assert EmailTemplate.query.filter_by(
        template_key="techops_sent_back").one().subject == "Edited"


def test_notify_sent_back_queues_members_and_contact(techops_review):
    _seed()
    item = techops_review["item"]
    grant_department_access("test:author", item)
    _add_contact(item, "Contact@Example.org")

    assert notify_sent_back(item, "Wrong rooms", datetime.utcnow()) == 2
    db.session.commit()

    rows = EmailOutbox.query.filter_by(template_key="techops_sent_back").all()
    assert sorted(r.recipient_email for r in rows) == [
        "Contact@Example.org", "author@test.local"]
    assert all(json.loads(r.context_json)["reason"] == "Wrong rooms" for r in rows)


def test_submission_confirmation_recipients_unchanged(techops_review):
    seed_work_type_templates(db.session.connection())
    db.session.commit()
    item = techops_review["item"]
    item.submitted_at = datetime.utcnow()
    grant_department_access("test:author", item)
    _add_contact(item, "AUTHOR@test.local")

    assert notify_submission_confirmation(item) == 1
    db.session.commit()
    rows = EmailOutbox.query.filter_by(
        template_key="techops_submission_confirmation").all()
    assert [r.recipient_email for r in rows] == ["author@test.local"]


def test_resubmit_within_the_hour_emails_reviewers_again(techops_review):
    from app.models import ApprovalGroup, UserRole, ROLE_APPROVER
    from app.routes import UserContext
    from app.routes.work.helpers.lifecycle import (
        RETURN_ACTION_PULL_BACK, reset_submitted_to_draft, submit_work_item,
    )
    from app.services.notifications import notify_work_item_submitted
    seed_work_type_templates(db.session.connection())
    db.session.commit()
    item = techops_review["item"]
    ctx = UserContext(user_id="test:author", user=None, roles=(),
                      is_super_admin=False, approval_group_ids=set())
    for _ in range(2):
        reset_submitted_to_draft(item, ctx, action=RETURN_ACTION_PULL_BACK)
        submit_work_item(item, ctx)
        notify_work_item_submitted(item)
        db.session.commit()
    rows = EmailOutbox.query.filter_by(template_key="techops_submitted").all()
    per_recipient = {}
    for r in rows:
        per_recipient[r.recipient_email] = per_recipient.get(r.recipient_email, 0) + 1
    assert per_recipient and set(per_recipient.values()) == {2}
