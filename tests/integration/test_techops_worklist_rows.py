"""The worklist's rows, filters, groups and totals for one event."""
from app import db
from app.models import (
    Department, EventCycle, TechOpsLineDetail,
    REVIEW_STATUS_APPROVED, REVIEW_STATUS_NEEDS_INFO, REVIEW_STATUS_REJECTED,
    WORK_ITEM_STATUS_DRAFT, WORK_ITEM_STATUS_FINALIZED, WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes.work.techops.worklist import (
    WorklistFilters, group_rows, parse_filters, worklist_rows, worklist_totals,
)

from tests.integration.techops_review_helpers import (
    add_request, add_routed_line, add_space, set_review_status,
)
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    techops_review,
)

ALL_TEAMS = WorklistFilters()
TEAMS = ("TECHOPS_GEN", "TECHOPS_NET", "TREASURY")


def _cycle():
    return EventCycle.query.filter_by(code="SMF2027").one()


def _numbers(filters=ALL_TEAMS):
    return [r.line.line_number for r in worklist_rows(_cycle(), filters)]


def test_a_submitted_line_appears_as_pending_and_department_wide(techops_review):
    rows = worklist_rows(_cycle(), ALL_TEAMS)

    assert [(r.line.line_number, r.status, r.team.code, r.room_name) for r in rows] \
        == [(1, "pending", "TECHOPS_GEN", "Department-wide")]


def test_drafts_archived_and_no_services_lines_are_left_out(techops_review):
    item = techops_review["item"]
    add_routed_line(item, 2, "TECHOPS_GEN", service_code="NO_SERVICES")
    assert _numbers() == [1]

    item.is_archived = True
    db.session.commit()
    assert _numbers() == []

    item.is_archived = False
    item.status = WORK_ITEM_STATUS_DRAFT
    db.session.commit()
    assert _numbers() == []


def test_finalized_and_reopened_requests_both_appear(techops_review):
    item = techops_review["item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    db.session.commit()
    assert _numbers() == [1]

    item.status = WORK_ITEM_STATUS_SUBMITTED
    db.session.commit()
    assert _numbers() == [1]


def test_status_mapping_and_the_default_hides_rejected(techops_review):
    item = techops_review["item"]
    set_review_status(add_routed_line(item, 2, "TECHOPS_NET"), REVIEW_STATUS_NEEDS_INFO)
    set_review_status(add_routed_line(item, 3, "TECHOPS_NET"), REVIEW_STATUS_APPROVED)
    set_review_status(add_routed_line(item, 4, "TECHOPS_NET"), REVIEW_STATUS_REJECTED)

    default = {r.line.line_number: r.status for r in worklist_rows(_cycle(), ALL_TEAMS)}

    assert default == {1: "pending", 2: "waiting", 3: "approved"}
    assert sorted(_numbers(WorklistFilters(status="all"))) == [1, 2, 3, 4]
    assert sorted(_numbers(WorklistFilters(status="pending"))) == [1, 2]
    assert sorted(_numbers(WorklistFilters(status="approved"))) == [3]


def test_team_service_and_department_filters(techops_review):
    item = techops_review["item"]
    add_routed_line(item, 2, "TECHOPS_NET", service_code="ETHERNET")

    assert _numbers(WorklistFilters(team_codes=("TECHOPS_NET",))) == [2]
    assert _numbers(WorklistFilters(service_code="ETHERNET")) == [2]
    assert _numbers(WorklistFilters(dept_code="NOPE")) == []


def test_parse_filters_defaults_to_own_teams_and_ignores_unknown_values():
    kwargs = dict(own_team_codes=("TECHOPS_GEN",), team_codes=TEAMS,
                  service_codes=("WIFI",), dept_codes=("TESTDEPT",))

    filters, group_by, team_choice = parse_filters({}, **kwargs)
    assert filters == WorklistFilters(team_codes=("TECHOPS_GEN",))
    assert (group_by, team_choice) == ("room", "")

    filters, group_by, team_choice = parse_filters(
        {"team": "all", "status": "bogus", "service": "NOPE",
         "dept": "testdept", "group": "service"}, **kwargs)
    assert filters == WorklistFilters(dept_code="TESTDEPT")
    assert (group_by, team_choice) == ("service", "all")

    filters, _, team_choice = parse_filters({"team": "TECHOPS_NET"}, **kwargs)
    assert filters.team_codes == ("TECHOPS_NET",)
    assert team_choice == "TECHOPS_NET"


def test_a_staffer_in_no_group_defaults_to_every_team():
    filters, _, team_choice = parse_filters(
        {}, own_team_codes=(), team_codes=TEAMS, service_codes=(), dept_codes=())

    assert filters.team_codes == ()
    assert team_choice == ""


def test_one_room_shared_by_two_departments_is_one_group(techops_review):
    item = techops_review["item"]
    holder = Department.query.filter_by(code="TESTDEPT").one()
    room = add_space("Chesapeake A", holder=holder)
    other = add_request("HOTELS", WORK_ITEM_STATUS_SUBMITTED, "SMF2027-HOTELS-TEC-1")
    mine = add_routed_line(item, 2, "TECHOPS_NET")
    theirs = add_routed_line(other, 1, "TECHOPS_GEN")
    for line in (mine, theirs):
        db.session.get(TechOpsLineDetail, line.id).space_id = room.id
    db.session.commit()
    set_review_status(theirs, REVIEW_STATUS_APPROVED)

    groups = group_rows(worklist_rows(_cycle(), ALL_TEAMS), "room")

    assert [(g.label, g.approved, g.pending) for g in groups] == [
        ("Chesapeake A", 1, 1), ("Department-wide", 0, 1)]


def test_totals_split_status_and_show_estimates(techops_review):
    item = techops_review["item"]
    small = add_routed_line(item, 2, "TECHOPS_NET", service_code="ETHERNET")
    large = add_routed_line(item, 3, "TECHOPS_NET", service_code="ETHERNET")
    pay = add_routed_line(item, 4, "TREASURY", service_code="PAYMENTS")
    db.session.get(TechOpsLineDetail, small.id).config = {"rough_count": "3-5"}
    db.session.get(TechOpsLineDetail, large.id).config = {"rough_count": "10+"}
    db.session.get(TechOpsLineDetail, pay.id).quantity = 2
    db.session.commit()
    set_review_status(large, REVIEW_STATUS_APPROVED)

    totals = {t.service.code: t for t in worklist_totals(worklist_rows(_cycle(), ALL_TEAMS))}

    assert (totals["ETHERNET"].approved, totals["ETHERNET"].pending) == (1, 1)
    assert totals["ETHERNET"].estimate_label == "13 to 15+ drops, requester estimates"
    assert totals["PAYMENTS"].estimate_label == "2 checkout points"
    assert totals["OTHER"].estimate_label is None
