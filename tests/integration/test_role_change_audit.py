"""Role changes write a security audit row, and ALERT events reach Slack."""
import io
import json

from flask import url_for

from app import db
from app.models import User, UserRole, WorkType, SecurityAuditLog, ROLE_SUPER_ADMIN
from app.security_audit import (
    log_security_event,
    EVENT_USER_MODIFY,
    CATEGORY_ADMIN,
    SEVERITY_ALERT,
    SEVERITY_INFO,
)


def _login(client, uid):
    with client.session_transaction() as s:
        s["active_user_id"] = uid


def _audit_rows(event_type=EVENT_USER_MODIFY):
    return (
        db.session.query(SecurityAuditLog)
        .filter_by(event_type=event_type)
        .all()
    )


def _post_bulk_roles(client, csv_rows):
    """POST an in-memory CSV to the bulk user-roles upload route.

    Each row is (user_id, role, approval_group); leave approval_group ""
    for roles that do not need one.
    """
    header = "user_id,role,approval_group"
    body = header + "\n" + "\n".join(",".join(r) for r in csv_rows)
    return client.post(
        "/admin/config/data-upload/user-roles",
        data={"file": (io.BytesIO(body.encode()), "roles.csv")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_alert_event_posts_to_slack(app, monkeypatch):
    """An ALERT event attempts one Slack post carrying the event type."""
    calls = []

    def fake_send(text, template_key, work_item_id=None, blocks=None):
        calls.append({"text": text, "template_key": template_key})
        return True

    monkeypatch.setattr("app.security_audit.send_slack_message", fake_send)

    with app.app_context():
        log_security_event(
            EVENT_USER_MODIFY,
            category=CATEGORY_ADMIN,
            severity=SEVERITY_ALERT,
            user_id="test:admin",
            details={"target_email": "someone@magfest.org"},
        )
        db.session.commit()

    assert len(calls) == 1
    assert EVENT_USER_MODIFY in calls[0]["template_key"]


def test_info_event_does_not_post_to_slack(app, monkeypatch):
    """Routine events stay out of Slack."""
    calls = []

    def fake_send(text, template_key, work_item_id=None, blocks=None):
        calls.append(template_key)
        return True

    monkeypatch.setattr("app.security_audit.send_slack_message", fake_send)

    with app.app_context():
        log_security_event(
            EVENT_USER_MODIFY,
            category=CATEGORY_ADMIN,
            severity=SEVERITY_INFO,
            user_id="test:admin",
        )
        db.session.commit()

    assert calls == []


def test_slack_failure_does_not_lose_the_audit_row(app, monkeypatch):
    """Slack is a convenience. It must never break the control."""
    def exploding_send(text, template_key, work_item_id=None, blocks=None):
        raise RuntimeError("slack is down")

    monkeypatch.setattr("app.security_audit.send_slack_message", exploding_send)

    with app.app_context():
        log_security_event(
            EVENT_USER_MODIFY,
            category=CATEGORY_ADMIN,
            severity=SEVERITY_ALERT,
            user_id="test:admin",
            details={"target_email": "someone@magfest.org"},
        )
        db.session.commit()

        from app.models import SecurityAuditLog
        rows = db.session.query(SecurityAuditLog).filter_by(
            event_type=EVENT_USER_MODIFY
        ).all()
        assert len(rows) == 1
        assert rows[0].severity == SEVERITY_ALERT


def test_granting_a_worktype_role_writes_an_entry(app, client, seed_workflow_data):
    """A work-type admin grant is recorded, naming the role by code."""
    target = User(id="u:target", email="target@magfest.org", display_name="Target")
    db.session.add(target)
    wt = db.session.query(WorkType).filter_by(code="BUDGET").one()
    db.session.commit()
    wt_id = wt.id

    with app.test_request_context():
        url = url_for("admin_config.users.update_user", user_id="u:target")

    _login(client, "test:admin")
    client.post(url, data={
        "email": "target@magfest.org",
        "display_name": "Target",
        "is_active": "1",
        f"role_worktype_admin_{wt_id}": "1",
    }, follow_redirects=True)

    rows = _audit_rows()
    assert len(rows) == 1
    details = json.loads(rows[0].details)
    assert "WORKTYPE_ADMIN:BUDGET" in details["granted"]
    assert details["revoked"] == []
    assert rows[0].severity == SEVERITY_INFO
    assert rows[0].user_id == "test:admin"
    assert details["target_user_id"] == "u:target"
    assert details["target_email"] == "target@magfest.org"


def test_granting_an_approver_role_names_the_group(app, client, seed_workflow_data):
    """The approval-group branch of _role_codes renders the group's code,
    not its numeric ID."""
    target = User(id="u:target5", email="t5@magfest.org", display_name="T5")
    db.session.add(target)
    ag = seed_workflow_data["approval_group"]
    db.session.commit()
    ag_id = ag.id

    with app.test_request_context():
        url = url_for("admin_config.users.update_user", user_id="u:target5")

    _login(client, "test:admin")
    client.post(url, data={
        "email": "t5@magfest.org",
        "display_name": "T5",
        "is_active": "1",
        f"role_approver_{ag_id}": "1",
    }, follow_redirects=True)

    rows = _audit_rows()
    assert len(rows) == 1
    details = json.loads(rows[0].details)
    assert f"APPROVER:{ag.code}" in details["granted"]


def test_revoking_a_role_names_it(app, client, seed_workflow_data):
    """Removing a role records it under revoked, not granted."""
    target = User(id="u:target2", email="t2@magfest.org", display_name="T2")
    db.session.add(target)
    wt = db.session.query(WorkType).filter_by(code="BUDGET").one()
    db.session.flush()
    db.session.add(UserRole(user_id="u:target2", role_code="WORKTYPE_ADMIN", work_type_id=wt.id))
    db.session.commit()

    with app.test_request_context():
        url = url_for("admin_config.users.update_user", user_id="u:target2")

    _login(client, "test:admin")
    client.post(url, data={
        "email": "t2@magfest.org",
        "display_name": "T2",
        "is_active": "1",
    }, follow_redirects=True)

    rows = _audit_rows()
    assert len(rows) == 1
    details = json.loads(rows[0].details)
    assert "WORKTYPE_ADMIN:BUDGET" in details["revoked"]
    assert details["granted"] == []


def test_super_admin_grant_is_alert(app, client, seed_workflow_data, monkeypatch):
    """The role that hands over the whole system raises severity.

    Task 1 wires SEVERITY_ALERT to a Slack post. This is the first ALERT
    event a real code path produces, so the Slack call is monkeypatched
    rather than relying on SLACK_ENABLED alone.
    """
    monkeypatch.setattr(
        "app.security_audit.send_slack_message",
        lambda text, template_key, work_item_id=None, blocks=None: True,
    )

    db.session.add(User(id="u:target3", email="t3@magfest.org", display_name="T3"))
    db.session.commit()

    with app.test_request_context():
        url = url_for("admin_config.users.update_user", user_id="u:target3")

    _login(client, "test:admin")
    client.post(url, data={
        "email": "t3@magfest.org",
        "display_name": "T3",
        "is_active": "1",
        "role_super_admin": "1",
    }, follow_redirects=True)

    rows = _audit_rows()
    assert len(rows) == 1
    assert rows[0].severity == SEVERITY_ALERT
    assert ROLE_SUPER_ADMIN in json.loads(rows[0].details)["granted"]


def test_super_admin_revoke_is_alert(app, client, seed_workflow_data, monkeypatch):
    """Removing SUPER_ADMIN raises severity the same way granting it does.

    The design spec's section 10 names this exact risk: severity applied to
    the grant list only. A check of `granted` alone would still pass the
    grant test above, so this exercises the revoke branch directly.
    """
    monkeypatch.setattr(
        "app.security_audit.send_slack_message",
        lambda text, template_key, work_item_id=None, blocks=None: True,
    )

    target = User(id="u:target6", email="t6@magfest.org", display_name="T6")
    db.session.add(target)
    db.session.flush()
    db.session.add(UserRole(user_id="u:target6", role_code=ROLE_SUPER_ADMIN))
    db.session.commit()

    with app.test_request_context():
        url = url_for("admin_config.users.update_user", user_id="u:target6")

    _login(client, "test:admin")
    client.post(url, data={
        "email": "t6@magfest.org",
        "display_name": "T6",
        "is_active": "1",
    }, follow_redirects=True)

    rows = _audit_rows()
    assert len(rows) == 1
    assert rows[0].severity == SEVERITY_ALERT
    details = json.loads(rows[0].details)
    assert ROLE_SUPER_ADMIN in details["revoked"]
    assert details["granted"] == []


def test_edit_that_changes_no_role_writes_nothing(app, client, seed_workflow_data):
    """A display-name edit must not produce role-change noise."""
    db.session.add(User(id="u:target4", email="t4@magfest.org", display_name="Old"))
    db.session.commit()

    with app.test_request_context():
        url = url_for("admin_config.users.update_user", user_id="u:target4")

    _login(client, "test:admin")
    client.post(url, data={
        "email": "t4@magfest.org",
        "display_name": "New",
        "is_active": "1",
    }, follow_redirects=True)

    assert _audit_rows() == []


def test_bulk_role_upload_writes_an_entry(app, client, seed_workflow_data):
    """The bulk path records what it created, with no revokes."""
    with app.app_context():
        db.session.add(User(id="u:bulk", email="bulk@magfest.org", display_name="Bulk"))
        db.session.commit()

    _login(client, "test:admin")
    _post_bulk_roles(client, csv_rows=[("u:bulk", "SUPER_ADMIN", "")])

    with app.app_context():
        rows = _audit_rows()
        assert len(rows) == 1
        details = json.loads(rows[0].details)
        assert "SUPER_ADMIN" in details["granted"]
        assert details["revoked"] == []
        assert rows[0].severity == SEVERITY_ALERT


def test_bulk_approver_grant_names_the_group(app, client, seed_workflow_data):
    """An APPROVER grant renders with its group code, not the bare role.

    UserRole.approval_group has load_on_pending=False. A read on a pending,
    unflushed row returns None without querying rather than waiting for
    autoflush, so this exercises the branch a SUPER_ADMIN-only row cannot
    reach.
    """
    ag = seed_workflow_data["approval_group"]
    with app.app_context():
        db.session.add(User(id="u:bulk-approver", email="bulk-approver@magfest.org", display_name="BulkApprover"))
        db.session.commit()
        ag_code = ag.code

    _login(client, "test:admin")
    _post_bulk_roles(client, csv_rows=[("u:bulk-approver", "APPROVER", ag_code)])

    with app.app_context():
        rows = _audit_rows()
        assert len(rows) == 1
        details = json.loads(rows[0].details)
        assert details["granted"] == [f"APPROVER:{ag_code}"]
        assert rows[0].severity == SEVERITY_INFO


def test_bulk_role_upload_writes_one_entry_per_user(app, client, seed_workflow_data):
    """A multi-user upload logs one entry per user, naming only their own roles."""
    ag = seed_workflow_data["approval_group"]
    with app.app_context():
        db.session.add_all([
            User(id="u:bulk-a", email="bulk-a@magfest.org", display_name="BulkA"),
            User(id="u:bulk-b", email="bulk-b@magfest.org", display_name="BulkB"),
        ])
        db.session.commit()
        ag_code = ag.code

    _login(client, "test:admin")
    _post_bulk_roles(client, csv_rows=[
        ("u:bulk-a", "SUPER_ADMIN", ""),
        ("u:bulk-b", "APPROVER", ag_code),
    ])

    with app.app_context():
        rows = _audit_rows()
        assert len(rows) == 2

        by_user = {json.loads(r.details)["target_user_id"]: r for r in rows}
        assert set(by_user) == {"u:bulk-a", "u:bulk-b"}

        a_details = json.loads(by_user["u:bulk-a"].details)
        assert a_details["granted"] == ["SUPER_ADMIN"]
        assert by_user["u:bulk-a"].severity == SEVERITY_ALERT

        b_details = json.loads(by_user["u:bulk-b"].details)
        assert b_details["granted"] == [f"APPROVER:{ag_code}"]
        assert by_user["u:bulk-b"].severity == SEVERITY_INFO
