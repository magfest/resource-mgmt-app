"""
Regression test for the submission-reminder 500 (May 2026).

Reminder emails sent to recipients of departments that hadn't started a
budget yet triggered a 500 when a recipient clicked the link without an
active session: portfolio_landing -> get_portfolio_context auto-created
a WorkPortfolio row with created_by_user_id=None, violating the NOT NULL
constraint. The fix gates the auto-create on user_ctx.user_id and
aborts 401 (which redirects to login) when the user is anonymous.
"""
from app import db
from app.models import Department


def test_anonymous_visit_to_uncreated_portfolio_redirects_to_login(
    app, client, seed_workflow_data,
):
    cycle = seed_workflow_data["cycle"]

    # seed_workflow_data already creates a portfolio for its department, which
    # would skip the auto-create branch. Use a fresh department with no
    # portfolio so the route hits the (formerly crashing) insert path.
    fresh_dept = Department(
        code="NOPORTFOLIO", name="No Portfolio Yet", is_active=True,
    )
    db.session.add(fresh_dept)
    db.session.commit()

    # No session set: simulates a reminder-email recipient with no active
    # login (DEV_LOGIN_ENABLED is False in the test fixture).
    response = client.get(f"/{cycle.code}/{fresh_dept.code}/budget")

    assert response.status_code == 302, (
        f"Expected redirect to login (302), got {response.status_code}. "
        f"Anonymous portfolio access should never reach the WorkPortfolio "
        f"insert (which would 500 on the NOT NULL constraint)."
    )
    assert "/login" in response.headers.get("Location", ""), (
        f"Expected Location to point at /login, got "
        f"{response.headers.get('Location')!r}"
    )
