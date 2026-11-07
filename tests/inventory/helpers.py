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
    """Enroll a device and leave the client holding only its cookie."""
    from app.inventory.devices import COOKIE_NAME, enroll_device
    device, raw = enroll_device(name, "test:invadmin", station_code=station_code)
    db.session.commit()
    with client.session_transaction() as sess:
        sess.clear()
    client.set_cookie(COOKIE_NAME, raw, path="/inv/scan/")
    return device
