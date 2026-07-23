"""Task 2: shared checkout helper + work-type admins can take the checkout lock.

A budget WORKTYPE_ADMIN (non-super, no approval-group membership) must be
able to check out a SUBMITTED work item — needed so a later task can
require the lock for admin-final decisions without locking budget admins
out of the admin tab. `user_holds_checkout` is a small shared predicate
used by that later task.
"""
from app import db
from app.models import UserRole, ROLE_WORKTYPE_ADMIN, WORK_ITEM_STATUS_SUBMITTED
from app.routes import UserContext
from app.routes.work.helpers.checkout import can_checkout, user_holds_checkout


def test_worktype_admin_can_checkout(app, seed_draft_work_item):
    """A non-super budget WORKTYPE_ADMIN with no approval-group membership
    can check out a SUBMITTED item of that work type."""
    d = seed_draft_work_item
    d["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    db.session.add(UserRole(
        user_id=d["reviewer"].id, role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=d["work_type"].id,
    ))
    db.session.commit()

    uc = UserContext(
        user_id=d["reviewer"].id, user=d["reviewer"], roles=(),
        is_super_admin=False, approval_group_ids=set(),
    )
    can, _reason = can_checkout(d["work_item"], uc)
    assert can is True


def test_non_admin_non_approver_still_rejected(app, seed_draft_work_item):
    """Sanity check the gate still rejects a plain user with no role at all
    (no super admin, no approval-group membership, no WORKTYPE_ADMIN row)."""
    d = seed_draft_work_item
    d["work_item"].status = WORK_ITEM_STATUS_SUBMITTED
    db.session.commit()

    uc = UserContext(
        user_id=d["reviewer"].id, user=d["reviewer"], roles=(),
        is_super_admin=False, approval_group_ids=set(),
    )
    can, reason = can_checkout(d["work_item"], uc)
    assert can is False
    assert reason == "Only reviewers can checkout work items."


def test_user_holds_checkout(app, seed_draft_work_item):
    wi = seed_draft_work_item["work_item"]
    wi.checked_out_by_user_id = "test:admin"

    uc = UserContext(
        user_id="test:admin", user=None, roles=(),
        is_super_admin=True, approval_group_ids=set(),
    )
    assert user_holds_checkout(wi, uc) is True

    uc2 = UserContext(
        user_id="other", user=None, roles=(),
        is_super_admin=False, approval_group_ids=set(),
    )
    assert user_holds_checkout(wi, uc2) is False
