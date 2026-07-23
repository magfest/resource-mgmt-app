from app import db
from app.models import (
    WorkLineReview,
    REVIEW_STAGE_APPROVAL_GROUP, REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STATUS_PENDING, REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED,
    REVIEW_STATUS_NEEDS_INFO, REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
)
from app.routes.work.helpers.review_state import (
    get_line_review_state,
    AWAITING_REVIEWER_GROUP, AWAITING_ADMIN, AWAITING_REQUESTER, AWAITING_DONE,
)


def _ag(line, status, gid):
    r = WorkLineReview(work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
                       approval_group_id=gid, status=status,
                       created_by_user_id="test:admin")
    db.session.add(r); db.session.flush(); return r


def _admin(line, status):
    r = WorkLineReview(work_line_id=line.id, stage=REVIEW_STAGE_ADMIN_FINAL,
                       approval_group_id=None, status=status,
                       created_by_user_id="test:admin")
    db.session.add(r); db.session.flush(); return r


def test_no_reviews_awaits_reviewer_group(app, seed_draft_work_item):
    line = seed_draft_work_item["line"]
    st = get_line_review_state(line)
    assert st.awaiting == AWAITING_REVIEWER_GROUP
    assert st.kickback_review is None


def test_ag_recommended_awaits_admin(app, seed_draft_work_item):
    d = seed_draft_work_item
    _ag(d["line"], REVIEW_STATUS_APPROVED_NEEDS_REVIEW, d["approval_group"].id)
    db.session.commit()
    st = get_line_review_state(d["line"])
    assert st.awaiting == AWAITING_ADMIN


def test_admin_approved_is_done(app, seed_draft_work_item):
    d = seed_draft_work_item
    _ag(d["line"], REVIEW_STATUS_APPROVED, d["approval_group"].id)
    _admin(d["line"], REVIEW_STATUS_APPROVED)
    db.session.commit()
    assert get_line_review_state(d["line"]).awaiting == AWAITING_DONE


def test_admin_needs_info_awaits_requester_kickback_is_admin(app, seed_draft_work_item):
    d = seed_draft_work_item
    _ag(d["line"], REVIEW_STATUS_APPROVED_NEEDS_REVIEW, d["approval_group"].id)
    admin = _admin(d["line"], REVIEW_STATUS_NEEDS_INFO)
    db.session.commit()
    st = get_line_review_state(d["line"])
    assert st.awaiting == AWAITING_REQUESTER
    assert st.kickback_review is not None and st.kickback_review.id == admin.id


def test_ag_needs_info_kickback_is_ag(app, seed_draft_work_item):
    d = seed_draft_work_item
    ag = _ag(d["line"], REVIEW_STATUS_NEEDS_INFO, d["approval_group"].id)
    db.session.commit()
    st = get_line_review_state(d["line"])
    assert st.awaiting == AWAITING_REQUESTER
    assert st.kickback_review.id == ag.id


def test_reopened_admin_awaits_admin(app, seed_draft_work_item):
    d = seed_draft_work_item
    _ag(d["line"], REVIEW_STATUS_APPROVED_NEEDS_REVIEW, d["approval_group"].id)
    _admin(d["line"], REVIEW_STATUS_PENDING)
    db.session.commit()
    assert get_line_review_state(d["line"]).awaiting == AWAITING_ADMIN


def test_no_admin_stage_ag_terminal_is_done(app, seed_draft_work_item):
    d = seed_draft_work_item
    d["work_type_config"].has_admin_final = False
    _ag(d["line"], REVIEW_STATUS_APPROVED, d["approval_group"].id)
    db.session.commit()
    st = get_line_review_state(d["line"])
    assert st.has_admin_stage is False
    assert st.awaiting == AWAITING_DONE
