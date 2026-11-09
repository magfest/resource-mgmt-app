"""Helpers shared by inventory tests."""
from app import db
from app.models import (
    InvAllocation, InvContainer, SupplyOrderLineDetail, TechOpsLineDetail,
    WorkItem, WorkLine, WorkPortfolio,
)


def login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def make_container(inv, label_code, kind="PALLET", parent=None, name=None):
    container = InvContainer(
        label_code=label_code, kind_id=inv.kinds[kind].id,
        parent_id=parent.id if parent else None, name=name,
    )
    db.session.add(container)
    db.session.flush()
    return container


def enrolled_client(client, name="Scanner 1", station_code="LOOKUP", home_container=None):
    """Enroll a device through the real code form; the client keeps only its cookie.

    A UNIT device needs a home container, so one is made when none is given.
    """
    from app.inventory.devices import create_enroll_code
    from app.models import InvContainer, InvContainerKind, InvEnrollCode
    if station_code == "UNIT" and home_container is None:
        kind = db.session.query(InvContainerKind).filter_by(code="LOCATION").first()
        home_container = InvContainer(label_code="C-UHOME", kind_id=kind.id, name="Unit home")
        db.session.add(home_container)
        db.session.flush()
    raw = create_enroll_code(name, "test:invadmin", station_code=station_code,
                             home_container=home_container)
    db.session.commit()
    with client.session_transaction() as sess:
        sess.clear()
    response = client.post("/inv/scan/enroll", data={"code": raw})
    assert response.status_code == 302, response.get_data(as_text=True)
    return db.session.query(InvEnrollCode).one().device


def seed_gear_kind(code, name, fields):
    """Create a gear kind with fields given as (code, name, type, unit_or_choices[, label]).

    A list or tuple in the fourth slot becomes the choices of a CHOICE field; a
    choice may be a (label, label_text) pair. The optional fifth slot is a dict
    of on_label and label_text. Returns the kind; kind.fields is in order.
    """
    from app.models import InvField, InvFieldChoice, InvGearKind
    kind = InvGearKind(code=code, name=name)
    db.session.add(kind)
    db.session.flush()
    for position, spec in enumerate(fields):
        fcode, fname, ftype, extra = spec[:4]
        label = spec[4] if len(spec) > 4 else {}
        field = InvField(gear_kind_id=kind.id, code=fcode, name=fname, field_type=ftype,
                         unit_label=extra if isinstance(extra, str) else None,
                         position=position, on_label=label.get("on_label", False),
                         label_text=label.get("label_text"))
        db.session.add(field)
        db.session.flush()
        for cpos, choice in enumerate(extra if isinstance(extra, (list, tuple)) else ()):
            text, short = (choice, None) if isinstance(choice, str) else choice
            db.session.add(InvFieldChoice(field_id=field.id, label=text, label_text=short,
                                          position=cpos))
    db.session.flush()
    db.session.refresh(kind)
    return kind


def latest_prep_status(unit):
    """The status of the unit's newest live check row from any event, or None."""
    from app.inventory.last_check import latest_check_rows
    row = latest_check_rows([unit.id]).get(unit.id)
    return row.status if row else None


def _line_in(work_type, prefix, dept, event, status, line_status):
    """Add one work item with one bare line under the (event, dept) portfolio."""
    portfolio = WorkPortfolio.query.filter_by(
        work_type_id=work_type.id, event_cycle_id=event.id, department_id=dept.id).first()
    if portfolio is None:
        portfolio = WorkPortfolio(work_type_id=work_type.id, event_cycle_id=event.id,
                                  department_id=dept.id, created_by_user_id="test:admin")
        db.session.add(portfolio)
        db.session.flush()
    number = portfolio.next_public_id_seq
    portfolio.next_public_id_seq = number + 1
    work_item = WorkItem(portfolio_id=portfolio.id, status=status, created_by_user_id="test:admin",
                         public_id=f"{event.code}-{dept.code}-{prefix}-{number}")
    db.session.add(work_item)
    db.session.flush()
    line = WorkLine(work_item_id=work_item.id, line_number=1, status=line_status)
    db.session.add(line)
    db.session.flush()
    return line


def supply_line(alloc, dept, requested, approved=None, status="SUBMITTED",
                line_status="PENDING", notes=None, event=None, item=None):
    """A Supply order line asking for `requested` of `item` (the TV by default)."""
    event = event or alloc.event
    line = _line_in(alloc.supply, "SUP", dept, event, status, line_status)
    db.session.add(SupplyOrderLineDetail(
        work_line_id=line.id, item_id=(item or alloc.tv).id,
        quantity_requested=requested, quantity_approved=approved,
        requester_notes=notes))
    db.session.flush()
    return line


def techops_line(alloc, dept, line_status="PENDING", status="SUBMITTED", space=None,
                 location=None, usage=None, event=None):
    """A TechOps printer line, one instance per line."""
    event = event or alloc.event
    line = _line_in(alloc.techops, "TEC", dept, event, status, line_status)
    db.session.add(TechOpsLineDetail(
        work_line_id=line.id, service_type_id=alloc.printer_service.id,
        space_id=space.id if space else None, location=location, usage=usage))
    db.session.flush()
    return line


def make_asset(item, label, name=None, condition="IN_SERVICE"):
    """An asset of `item` carrying a U- label, through the ledger."""
    from app.inventory.ledger import Actor, add_unit
    unit = add_unit(item, None, Actor(user_id="test:invadmin"),
                    [("FESTOPS_LABEL", label)], name=name)
    unit.condition = condition
    db.session.flush()
    return unit


def make_allocation(unit, line, words="invented words"):
    """Write an allocation row directly, bypassing the rules."""
    portfolio = db.session.get(
        WorkPortfolio, db.session.get(WorkItem, line.work_item_id).portfolio_id)
    event_id, dept_id = portfolio.event_cycle_id, portfolio.department_id
    row = InvAllocation(unit_id=unit.id, work_line_id=line.id, event_cycle_id=event_id,
                        department_id=dept_id, line_label=words, allocated_by_user_id="test:admin")
    db.session.add(row)
    db.session.flush()
    return row
