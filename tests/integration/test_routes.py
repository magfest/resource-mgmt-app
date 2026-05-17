"""
Integration tests for routes using Flask test client.
"""
from app import db


class TestAuthRoutes:
    """Tests for authentication-related routes."""

    def test_home_redirects_when_unauthenticated(self, client):
        """Home page should redirect unauthenticated users to login."""
        response = client.get("/", follow_redirects=False)

        # Should redirect to login page
        assert response.status_code == 302
        assert "/login" in response.location

    def test_login_page_loads(self, client):
        """Login page should load successfully."""
        # Note: /login is the login page, /auth/login initiates OAuth
        response = client.get("/login")

        assert response.status_code == 200
        # Check that the response contains expected login page content
        assert b"Sign" in response.data or b"Login" in response.data or b"sign" in response.data


def _login(client, user_id):
    """Set the session to simulate a logged-in user."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_comment_post_with_relative_return_to_redirects_to_edit_notes_tab(
    app, client, seed_draft_work_item
):
    """
    Posting a note from the edit page (with return_to=request.path, i.e. a
    leading-slash relative path) must redirect back to the edit page with
    tab=notes preserved. Regression test for the request.url -> request.path
    fix in app/templates/budget/work_item_edit.html.

    Note: this test asserts route behavior. It passes both before AND after
    the template fix, because the route already handles relative paths
    correctly. The bug was that the template was never *sending* a relative
    path. This test is a regression guard against future drift on either
    side.
    """
    item = seed_draft_work_item["work_item"]
    event = seed_draft_work_item["cycle"].code
    dept = seed_draft_work_item["department"].code
    edit_path = f"/{event}/{dept}/budget/item/{item.public_id}/edit"

    _login(client, "test:admin")

    response = client.post(
        f"/{event}/{dept}/budget/item/{item.public_id}/comment",
        data={"comment": "Test note", "return_to": edit_path},
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["Location"]
    assert "/edit" in location, f"Expected redirect to edit page, got {location}"
    assert "tab=notes" in location, f"Expected tab=notes in redirect, got {location}"


# ============================================================
# Note (Comment) Edit/Delete + Income Audit Tests
# ============================================================

def test_author_can_edit_own_comment_on_draft_writes_audit(
    app, client, seed_draft_work_item
):
    from app.models import WorkItemComment, WorkItemAuditEvent
    item = seed_draft_work_item["work_item"]
    event = seed_draft_work_item["cycle"].code
    dept = seed_draft_work_item["department"].code

    with app.app_context():
        comment = WorkItemComment(
            work_item_id=item.id, visibility="PUBLIC",
            body="Original text", created_by_user_id="test:admin",
        )
        db.session.add(comment)
        db.session.commit()
        comment_id = comment.id

    _login(client, "test:admin")

    response = client.post(
        f"/{event}/{dept}/budget/item/{item.public_id}/comment/{comment_id}/edit",
        data={"comment": "Edited text"}, follow_redirects=False,
    )
    assert response.status_code == 302
    assert "tab=notes" in response.headers["Location"]

    with app.app_context():
        assert WorkItemComment.query.get(comment_id).body == "Edited text"
        audits = WorkItemAuditEvent.query.filter_by(
            work_item_id=item.id, event_type="COMMENT_EDIT"
        ).all()
        assert len(audits) == 1
        assert audits[0].old_value == "Original text"
        assert audits[0].new_value == "Edited text"
        assert audits[0].snapshot["comment_id"] == comment_id


def test_non_author_cannot_edit_comment(app, client, seed_draft_work_item):
    from app.models import WorkItemComment
    item = seed_draft_work_item["work_item"]
    event = seed_draft_work_item["cycle"].code
    dept = seed_draft_work_item["department"].code

    with app.app_context():
        comment = WorkItemComment(
            work_item_id=item.id, visibility="PUBLIC",
            body="Original text", created_by_user_id="test:someone-else",
        )
        db.session.add(comment)
        db.session.commit()
        comment_id = comment.id

    _login(client, "test:admin")

    response = client.post(
        f"/{event}/{dept}/budget/item/{item.public_id}/comment/{comment_id}/edit",
        data={"comment": "Hijacked"}, follow_redirects=False,
    )
    assert response.status_code == 403
    with app.app_context():
        assert WorkItemComment.query.get(comment_id).body == "Original text"


def test_no_op_edit_writes_no_audit(app, client, seed_draft_work_item):
    from app.models import WorkItemComment, WorkItemAuditEvent
    item = seed_draft_work_item["work_item"]
    event = seed_draft_work_item["cycle"].code
    dept = seed_draft_work_item["department"].code

    with app.app_context():
        comment = WorkItemComment(
            work_item_id=item.id, visibility="PUBLIC",
            body="Same text", created_by_user_id="test:admin",
        )
        db.session.add(comment)
        db.session.commit()
        comment_id = comment.id

    _login(client, "test:admin")

    client.post(
        f"/{event}/{dept}/budget/item/{item.public_id}/comment/{comment_id}/edit",
        data={"comment": "Same text"},
    )
    with app.app_context():
        assert WorkItemAuditEvent.query.filter_by(
            work_item_id=item.id, event_type="COMMENT_EDIT"
        ).count() == 0


def test_author_can_delete_own_comment_writes_audit_with_body(
    app, client, seed_draft_work_item
):
    from app.models import WorkItemComment, WorkItemAuditEvent
    item = seed_draft_work_item["work_item"]
    event = seed_draft_work_item["cycle"].code
    dept = seed_draft_work_item["department"].code

    with app.app_context():
        comment = WorkItemComment(
            work_item_id=item.id, visibility="PUBLIC",
            body="Sensitive original text", created_by_user_id="test:admin",
        )
        db.session.add(comment)
        db.session.commit()
        comment_id = comment.id

    _login(client, "test:admin")

    response = client.post(
        f"/{event}/{dept}/budget/item/{item.public_id}/comment/{comment_id}/delete",
    )
    assert response.status_code == 302
    with app.app_context():
        assert WorkItemComment.query.get(comment_id) is None
        audits = WorkItemAuditEvent.query.filter_by(
            work_item_id=item.id, event_type="COMMENT_DELETE"
        ).all()
        assert len(audits) == 1
        assert audits[0].old_value == "Sensitive original text"
        assert audits[0].snapshot["comment_id"] == comment_id


def test_non_author_cannot_delete_comment(app, client, seed_draft_work_item):
    from app.models import WorkItemComment
    item = seed_draft_work_item["work_item"]
    event = seed_draft_work_item["cycle"].code
    dept = seed_draft_work_item["department"].code

    with app.app_context():
        comment = WorkItemComment(
            work_item_id=item.id, visibility="PUBLIC",
            body="Original text", created_by_user_id="test:someone-else",
        )
        db.session.add(comment)
        db.session.commit()
        comment_id = comment.id

    _login(client, "test:admin")

    response = client.post(
        f"/{event}/{dept}/budget/item/{item.public_id}/comment/{comment_id}/delete",
        follow_redirects=False,
    )
    assert response.status_code == 403
    with app.app_context():
        assert WorkItemComment.query.get(comment_id) is not None
        assert WorkItemComment.query.get(comment_id).body == "Original text"


def test_income_estimate_change_writes_audit(
    app, client, seed_draft_work_item
):
    from app.models import WorkItemAuditEvent
    item = seed_draft_work_item["work_item"]
    event = seed_draft_work_item["cycle"].code
    dept = seed_draft_work_item["department"].code

    _login(client, "test:admin")

    client.post(
        f"/{event}/{dept}/budget/item/{item.public_id}/income",
        data={"income_estimate": "100.00", "income_notes": ""},
    )
    with app.app_context():
        audits = WorkItemAuditEvent.query.filter_by(
            work_item_id=item.id, event_type="INCOME_ESTIMATE_CHANGE"
        ).all()
        assert len(audits) == 1
        assert audits[0].old_value is None
        assert audits[0].new_value == "10000"


def test_income_notes_change_writes_audit(
    app, client, seed_draft_work_item
):
    from app.models import WorkItemAuditEvent
    item = seed_draft_work_item["work_item"]
    event = seed_draft_work_item["cycle"].code
    dept = seed_draft_work_item["department"].code

    _login(client, "test:admin")

    client.post(
        f"/{event}/{dept}/budget/item/{item.public_id}/income",
        data={"income_estimate": "", "income_notes": "Merch table revenue"},
    )
    with app.app_context():
        audits = WorkItemAuditEvent.query.filter_by(
            work_item_id=item.id, event_type="INCOME_NOTES_CHANGE"
        ).all()
        assert len(audits) == 1
        assert audits[0].new_value == "Merch table revenue"


def test_income_save_no_change_writes_no_audit(
    app, client, seed_draft_work_item
):
    from app.models import WorkItem, WorkItemAuditEvent
    item = seed_draft_work_item["work_item"]
    event = seed_draft_work_item["cycle"].code
    dept = seed_draft_work_item["department"].code

    with app.app_context():
        wi = WorkItem.query.get(item.id)
        wi.income_estimate_cents = 5000
        wi.income_notes = "Existing notes"
        db.session.commit()

    _login(client, "test:admin")

    client.post(
        f"/{event}/{dept}/budget/item/{item.public_id}/income",
        data={"income_estimate": "50.00", "income_notes": "Existing notes"},
    )
    with app.app_context():
        assert WorkItemAuditEvent.query.filter_by(
            work_item_id=item.id, event_type="INCOME_ESTIMATE_CHANGE",
        ).count() == 0
        assert WorkItemAuditEvent.query.filter_by(
            work_item_id=item.id, event_type="INCOME_NOTES_CHANGE",
        ).count() == 0


def test_adding_comment_writes_audit(app, client, seed_draft_work_item):
    from app.models import WorkItemComment, WorkItemAuditEvent
    item = seed_draft_work_item["work_item"]
    event = seed_draft_work_item["cycle"].code
    dept = seed_draft_work_item["department"].code
    edit_path = f"/{event}/{dept}/budget/item/{item.public_id}/edit"

    _login(client, "test:admin")
    response = client.post(
        f"/{event}/{dept}/budget/item/{item.public_id}/comment",
        data={"comment": "Fresh note", "return_to": edit_path},
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        comment = WorkItemComment.query.filter_by(work_item_id=item.id).first()
        assert comment is not None
        audits = WorkItemAuditEvent.query.filter_by(
            work_item_id=item.id, event_type="COMMENT_ADDED"
        ).all()
        assert len(audits) == 1
        assert audits[0].new_value == "Fresh note"
        assert audits[0].snapshot["comment_id"] == comment.id
        assert audits[0].snapshot["visibility"] == "PUBLIC"


def test_editing_comment_bumps_updated_at(app, client, seed_draft_work_item):
    from app.models import WorkItemComment
    item = seed_draft_work_item["work_item"]
    event = seed_draft_work_item["cycle"].code
    dept = seed_draft_work_item["department"].code

    with app.app_context():
        comment = WorkItemComment(
            work_item_id=item.id, visibility="PUBLIC",
            body="v1", created_by_user_id="test:admin",
        )
        db.session.add(comment)
        db.session.commit()
        comment_id = comment.id
        original_updated_at = comment.updated_at

    import time; time.sleep(1.1)  # ensure timestamp difference > 1s tolerance

    _login(client, "test:admin")
    client.post(
        f"/{event}/{dept}/budget/item/{item.public_id}/comment/{comment_id}/edit",
        data={"comment": "v2"},
    )
    with app.app_context():
        updated = WorkItemComment.query.get(comment_id)
        assert updated.updated_at > original_updated_at


def test_new_comment_updated_at_equals_created_at(app, client, seed_draft_work_item):
    """Brand-new comment should NOT appear as edited (updated_at ~= created_at)."""
    from app.models import WorkItemComment
    item = seed_draft_work_item["work_item"]
    event = seed_draft_work_item["cycle"].code
    dept = seed_draft_work_item["department"].code

    _login(client, "test:admin")
    client.post(
        f"/{event}/{dept}/budget/item/{item.public_id}/comment",
        data={"comment": "Brand new", "return_to": f"/{event}/{dept}/budget/item/{item.public_id}/edit"},
    )
    with app.app_context():
        comment = WorkItemComment.query.filter_by(work_item_id=item.id).first()
        delta_seconds = abs((comment.updated_at - comment.created_at).total_seconds())
        assert delta_seconds < 1, f"New comment should not appear edited, but delta={delta_seconds}s"
