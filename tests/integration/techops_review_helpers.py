"""Shared setup for TechOps review-flow tests built on the techops_review fixture."""
from app import db
from app.models import (
    ApprovalGroup, Department, DepartmentMembership,
    DepartmentMembershipWorkTypeAccess, Space, SpaceAssignment,
    TechOpsLineDetail, TechOpsServiceType, User, UserRole, Venue,
    WorkItem, WorkLine, WorkLineReview, WorkPortfolio, WorkType,
    REQUEST_KIND_PRIMARY, REVIEW_STAGE_APPROVAL_GROUP, REVIEW_STATUS_PENDING,
    SPACE_KIND_ROOM, WORK_LINE_STATUS_PENDING,
)
from tests.integration.test_techops_review_affordances import BASE, ITEM_ID


def login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def add_user_with_role(user_id, role_code, work_type_code="TECHOPS"):
    """Create a user holding one work-type-scoped role and nothing else."""
    db.session.add(User(id=user_id, email=f"{user_id.split(':')[1]}@test.local",
                        display_name=user_id, is_active=True))
    db.session.flush()
    work_type = WorkType.query.filter_by(code=work_type_code).one()
    db.session.add(UserRole(user_id=user_id, role_code=role_code,
                            work_type_id=work_type.id))
    db.session.commit()


def add_routed_line(item, line_number, group_code, service_code="WIFI"):
    """Add a submitted line routed to group_code with a PENDING review."""
    group = ApprovalGroup.query.filter_by(code=group_code).one()
    line = WorkLine(work_item_id=item.id, line_number=line_number,
                    status=WORK_LINE_STATUS_PENDING)
    db.session.add(line)
    db.session.flush()
    service = TechOpsServiceType.query.filter_by(code=service_code).one()
    db.session.add(TechOpsLineDetail(
        work_line_id=line.id, service_type_id=service.id,
        description=f"{service_code} line", routed_approval_group_id=group.id,
    ))
    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=group.id, status=REVIEW_STATUS_PENDING,
        created_by_user_id="test:author",
    ))
    db.session.commit()
    return line


def grant_department_access(user_id, item, can_edit=True):
    """Give user_id TechOps access to the item's department for its event.

    Creating a request grants no view right; the department membership does.
    """
    portfolio = item.portfolio
    membership = DepartmentMembership(
        user_id=user_id, department_id=portfolio.department_id,
        event_cycle_id=portfolio.event_cycle_id,
    )
    db.session.add(membership)
    db.session.flush()
    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=membership.id, work_type_id=portfolio.work_type_id,
        can_view=True, can_edit=can_edit,
    ))
    db.session.commit()


def line_url(line_number, action):
    return f"{BASE}/item/{ITEM_ID}/line/{line_number}/{action}"


def add_space(name, holder=None):
    """Add a room at the fixture venue; holder is a Department to assign it to."""
    from app.models import EventCycle
    cycle = EventCycle.query.filter_by(code="SMF2027").one()
    venue = Venue.query.filter_by(code="GLN").first()
    if venue is None:
        venue = Venue(code="GLN", name="Gaylord National")
        db.session.add(venue)
        db.session.flush()
    space = Space(venue_id=venue.id, name=name,
                  code=name.upper().replace(" ", "")[:32],
                  kind=SPACE_KIND_ROOM, is_active=True)
    db.session.add(space)
    db.session.flush()
    if holder is not None:
        db.session.add(SpaceAssignment(space_id=space.id, event_cycle_id=cycle.id,
                                       department_id=holder.id))
    db.session.commit()
    return space


def set_review_status(line, status):
    review = WorkLineReview.query.filter_by(work_line_id=line.id).one()
    review.status = status
    db.session.commit()


def add_request(dept_code, status, public_id):
    """Add a department and a TechOps request of the given status in SMF2027."""
    from app.models import EventCycle
    cycle = EventCycle.query.filter_by(code="SMF2027").one()
    work_type = WorkType.query.filter_by(code="TECHOPS").one()
    dept = Department.query.filter_by(code=dept_code).first()
    if dept is None:
        dept = Department(code=dept_code, name=dept_code.title(), is_active=True)
        db.session.add(dept)
        db.session.flush()
    portfolio = WorkPortfolio(work_type_id=work_type.id, event_cycle_id=cycle.id,
                              department_id=dept.id, created_by_user_id="test:author")
    db.session.add(portfolio)
    db.session.flush()
    item = WorkItem(portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
                    status=status, public_id=public_id,
                    created_by_user_id="test:author")
    db.session.add(item)
    db.session.commit()
    return item
