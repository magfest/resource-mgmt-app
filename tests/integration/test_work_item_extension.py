"""
Integration tests for the budget request extension flag.

Tests cover the grant and revoke routes for marking a WorkItem as having an approved extension.
"""
from app import db
from app.models import (
    WorkItem,
    WorkItemAuditEvent,
    AUDIT_EVENT_EXTENSION_GRANTED,
    AUDIT_EVENT_EXTENSION_REVOKED,
)


def _login(client, user_id):
    """Set the session to simulate a logged-in user."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


# ============================================================
# Grant route tests
# ============================================================

class TestGrantExtension:

    def test_admin_can_grant_extension(self, app, client, seed_draft_work_item):
        """A budget admin can grant an extension; flag + stamps + audit event are set."""
        data = seed_draft_work_item
        work_item = data["work_item"]
        public_id = work_item.public_id

        _login(client, "test:admin")

        response = client.post(
            f"/tst2026/testdept/budget/item/{public_id}/extension/grant",
            follow_redirects=False,
        )

        assert response.status_code == 302  # redirect to detail

        # Re-fetch and assert state
        with app.app_context():
            wi = WorkItem.query.filter_by(public_id=public_id).one()
            assert wi.extension_granted is True
            assert wi.extension_granted_at is not None
            assert wi.extension_granted_by_user_id == "test:admin"

            audit = WorkItemAuditEvent.query.filter_by(
                work_item_id=wi.id,
                event_type=AUDIT_EVENT_EXTENSION_GRANTED,
            ).all()
            assert len(audit) == 1
            assert audit[0].created_by_user_id == "test:admin"

    def test_grant_is_idempotent(self, app, client, seed_draft_work_item):
        """Granting an already-granted extension is a no-op (no second audit event, no stamp change)."""
        data = seed_draft_work_item
        work_item = data["work_item"]
        public_id = work_item.public_id

        _login(client, "test:admin")

        # First grant
        client.post(f"/tst2026/testdept/budget/item/{public_id}/extension/grant")

        with app.app_context():
            wi = WorkItem.query.filter_by(public_id=public_id).one()
            first_stamp = wi.extension_granted_at

        # Second grant (should be no-op)
        response = client.post(
            f"/tst2026/testdept/budget/item/{public_id}/extension/grant",
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            wi = WorkItem.query.filter_by(public_id=public_id).one()
            assert wi.extension_granted is True
            assert wi.extension_granted_at == first_stamp  # unchanged

            audit = WorkItemAuditEvent.query.filter_by(
                work_item_id=wi.id,
                event_type=AUDIT_EVENT_EXTENSION_GRANTED,
            ).all()
            assert len(audit) == 1  # still just one

    def test_non_admin_cannot_grant(self, app, client, seed_draft_work_item):
        """A non-admin user gets 403 and no state changes."""
        data = seed_draft_work_item
        work_item = data["work_item"]
        public_id = work_item.public_id

        # Seed a non-admin user who has dept membership but no admin role
        from app.models import User, DepartmentMembership, DepartmentMembershipWorkTypeAccess
        with app.app_context():
            user = User(
                id="test:member", email="member@test.local",
                auth_subject="test:member", display_name="Test Member", is_active=True,
            )
            db.session.add(user)
            db.session.flush()
            membership = DepartmentMembership(
                user_id=user.id, department_id=data["department"].id,
                event_cycle_id=data["cycle"].id, is_department_head=False,
            )
            db.session.add(membership)
            db.session.flush()
            db.session.add(DepartmentMembershipWorkTypeAccess(
                department_membership_id=membership.id,
                work_type_id=data["work_type"].id, can_view=True, can_edit=True,
            ))
            db.session.commit()

        _login(client, "test:member")

        response = client.post(
            f"/tst2026/testdept/budget/item/{public_id}/extension/grant",
        )
        assert response.status_code == 403

        with app.app_context():
            wi = WorkItem.query.filter_by(public_id=public_id).one()
            assert wi.extension_granted is False
            assert wi.extension_granted_at is None
            audit = WorkItemAuditEvent.query.filter_by(
                work_item_id=wi.id,
                event_type=AUDIT_EVENT_EXTENSION_GRANTED,
            ).count()
            assert audit == 0


# ============================================================
# Revoke route tests
# ============================================================

class TestRevokeExtension:

    def test_admin_can_revoke_extension(self, app, client, seed_draft_work_item):
        """Revoking a granted extension clears the flag + stamps and writes an audit event."""
        data = seed_draft_work_item
        work_item = data["work_item"]
        public_id = work_item.public_id

        _login(client, "test:admin")

        # First grant, then revoke
        client.post(f"/tst2026/testdept/budget/item/{public_id}/extension/grant")
        response = client.post(
            f"/tst2026/testdept/budget/item/{public_id}/extension/revoke",
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            wi = WorkItem.query.filter_by(public_id=public_id).one()
            assert wi.extension_granted is False
            assert wi.extension_granted_at is None
            assert wi.extension_granted_by_user_id is None

            audit = WorkItemAuditEvent.query.filter_by(
                work_item_id=wi.id,
                event_type=AUDIT_EVENT_EXTENSION_REVOKED,
            ).all()
            assert len(audit) == 1

    def test_revoke_is_idempotent(self, app, client, seed_draft_work_item):
        """Revoking a non-granted extension is a no-op."""
        data = seed_draft_work_item
        work_item = data["work_item"]
        public_id = work_item.public_id

        _login(client, "test:admin")

        response = client.post(
            f"/tst2026/testdept/budget/item/{public_id}/extension/revoke",
            follow_redirects=False,
        )
        assert response.status_code == 302

        with app.app_context():
            wi = WorkItem.query.filter_by(public_id=public_id).one()
            assert wi.extension_granted is False
            audit = WorkItemAuditEvent.query.filter_by(
                work_item_id=wi.id,
                event_type=AUDIT_EVENT_EXTENSION_REVOKED,
            ).count()
            assert audit == 0
