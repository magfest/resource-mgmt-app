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
