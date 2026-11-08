"""Helpers shared by inventory tests."""
from app import db
from app.models import InvContainer


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


def enrolled_client(client, name="Scanner 1", station_code="LOOKUP"):
    """Enroll a device through the real code form; the client keeps only its cookie."""
    from app.inventory.devices import create_enroll_code
    from app.models import InvEnrollCode
    raw = create_enroll_code(name, "test:invadmin", station_code=station_code)
    db.session.commit()
    with client.session_transaction() as sess:
        sess.clear()
    response = client.post("/inv/scan/enroll", data={"code": raw})
    assert response.status_code == 302, response.get_data(as_text=True)
    return db.session.query(InvEnrollCode).one().device


def seed_gear_kind(code, name, fields):
    """Create a gear kind with fields given as (code, name, type, unit_or_choices).

    A list or tuple in the last slot becomes the choices of a CHOICE field.
    Returns the kind; each field is reachable as kind.fields in order.
    """
    from app.models import InvField, InvFieldChoice, InvGearKind
    kind = InvGearKind(code=code, name=name)
    db.session.add(kind)
    db.session.flush()
    for position, (fcode, fname, ftype, extra) in enumerate(fields):
        field = InvField(gear_kind_id=kind.id, code=fcode, name=fname, field_type=ftype,
                         unit_label=extra if isinstance(extra, str) else None,
                         position=position)
        db.session.add(field)
        db.session.flush()
        for cpos, label in enumerate(extra if isinstance(extra, (list, tuple)) else ()):
            db.session.add(InvFieldChoice(field_id=field.id, label=label, position=cpos))
    db.session.flush()
    db.session.refresh(kind)
    return kind
