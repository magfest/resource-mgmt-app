"""Opening the category edit form and saving it unchanged must not write "None".

The prefill printed Python's None as text for NULL columns, and the save
path keeps any non-blank string, so a no-op save corrupted every empty field.
"""
from __future__ import annotations

import re

from app import db
from app.models import (
    SupplyCategory,
    User,
    UserRole,
    WorkType,
    ROLE_SUPER_ADMIN,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _seed(app):
    admin = User(id="test:admin", email="admin@test.local", display_name="Test Admin", is_active=True)
    db.session.add(admin)
    db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SUPER_ADMIN))
    db.session.add(WorkType(code="SUPPLY", name="Supply Orders", is_active=True))
    category = SupplyCategory(code="OFFICE", name="Office Supplies", is_active=True, description=None)
    db.session.add(category)
    db.session.flush()
    db.session.commit()
    return category.id


def _textarea_value(html: str, name: str) -> str:
    match = re.search(rf'name="{name}"[^>]*>(.*?)</textarea>', html, re.S)
    assert match, f"no textarea named {name}"
    return match.group(1)


def test_category_saved_unchanged_keeps_null_description(app, client):
    category_id = _seed(app)
    _login(client, "test:admin")
    html = client.get(f"/admin/config/supply-categories/{category_id}").get_data(as_text=True)
    assert _textarea_value(html, "description") == ""

    client.post(
        f"/admin/config/supply-categories/{category_id}",
        data={"code": "OFFICE", "name": "Office Supplies", "is_active": "on",
              "description": _textarea_value(html, "description")},
    )
    category = db.session.get(SupplyCategory, category_id)
    db.session.refresh(category)
    assert category.description is None
