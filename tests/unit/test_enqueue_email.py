"""enqueue_email writes rows and never commits."""
from app import db
from app.models import EmailOutbox
from app.models.constants import ENQUEUE_OUTCOME_CREATED, ENQUEUE_OUTCOME_DUPLICATE
from app.services.email_enqueue import enqueue_email


def test_creates_row(app):
    with app.app_context():
        outcome = enqueue_email("submitted", "a@example.org", dedup_key="k1")
        db.session.commit()
        assert outcome == ENQUEUE_OUTCOME_CREATED
        assert db.session.query(EmailOutbox).count() == 1


def test_duplicate_dedup_key_does_not_abort_the_transaction(app):
    """A caught IntegrityError aborts the surrounding transaction on Postgres,
    which would let a duplicate email roll back the approval that caused it."""
    with app.app_context():
        enqueue_email("finalized", "b@example.org", dedup_key="k2")
        db.session.commit()
        outcome = enqueue_email("finalized", "b@example.org", dedup_key="k2")
        db.session.commit()
        assert outcome == ENQUEUE_OUTCOME_DUPLICATE
        assert db.session.query(EmailOutbox).count() == 1


def test_does_not_commit(app):
    with app.app_context():
        enqueue_email("submitted", "c@example.org", dedup_key="k3")
        db.session.rollback()
        assert db.session.query(EmailOutbox).count() == 0
