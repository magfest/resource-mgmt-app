"""enqueue_email writes rows and never commits."""
from app import db
from app.models import EmailOutbox, EmailTemplate
from app.models.constants import ENQUEUE_OUTCOME_CREATED, ENQUEUE_OUTCOME_DUPLICATE
from app.services.email_enqueue import enqueue_email


def _template(key):
    """Seed the one template this test needs.

    These tests take `app` alone, not seed_workflow_data, so no template
    exists. enqueue_email returns BLOCKED_INACTIVE and writes nothing without
    one, which would make every assertion below pass on an empty queue.
    """
    db.session.add(EmailTemplate(
        template_key=key, name=key, subject="S", body_text="B",
        is_active=True,
    ))
    db.session.flush()


def test_creates_row(app):
    with app.app_context():
        _template("submitted")
        outcome = enqueue_email("submitted", "a@example.org", dedup_key="k1")
        db.session.commit()
        assert outcome == ENQUEUE_OUTCOME_CREATED
        assert db.session.query(EmailOutbox).count() == 1


def test_duplicate_dedup_key_does_not_abort_the_transaction(app):
    """A caught IntegrityError aborts the surrounding transaction on Postgres,
    which would let a duplicate email roll back the approval that caused it."""
    with app.app_context():
        _template("finalized")
        enqueue_email("finalized", "b@example.org", dedup_key="k2")
        db.session.commit()
        outcome = enqueue_email("finalized", "b@example.org", dedup_key="k2")
        db.session.commit()
        assert outcome == ENQUEUE_OUTCOME_DUPLICATE
        assert db.session.query(EmailOutbox).count() == 1


def test_does_not_commit(app):
    with app.app_context():
        _template("submitted")
        enqueue_email("submitted", "c@example.org", dedup_key="k3")
        db.session.rollback()
        assert db.session.query(EmailOutbox).count() == 0
