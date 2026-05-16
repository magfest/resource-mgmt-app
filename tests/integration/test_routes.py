"""
Integration tests for routes using Flask test client.
"""


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
