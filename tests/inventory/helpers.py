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
