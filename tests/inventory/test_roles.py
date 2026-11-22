"""Who reaches inventory pages, and the Users-form trap that strips roles."""
import re

from app.models import ROLE_INVENTORY_ADMIN, ROLE_INVENTORY_OPERATOR, UserRole
from tests.inventory.helpers import login


def test_saving_a_user_in_users_admin_keeps_inventory_roles(client, inv):
    login(client, "test:admin")
    form = client.get("/admin/config/users/test:invadmin").get_data(as_text=True)
    assert re.search(r'name="role_inventory_admin" value="1"\s*checked', form)

    client.post("/admin/config/users/test:invadmin", data={
        "email": "invadmin@test.local", "display_name": "Invadmin",
        "is_active": "1", "role_inventory_admin": "1",
    })
    roles = {r.role_code for r in UserRole.query.filter_by(user_id="test:invadmin")}
    assert roles == {ROLE_INVENTORY_ADMIN}


def test_inventory_admin_grants_operator_and_operator_cannot_reach_admin(client, inv):
    login(client, "test:invadmin")
    response = client.post("/inv/admin/operators", data={"email": "nobody@test.local"})
    assert response.status_code == 302
    assert UserRole.query.filter_by(user_id="test:nobody",
                                    role_code=ROLE_INVENTORY_OPERATOR).count() == 1

    login(client, "test:operator")
    assert client.get("/inv/").status_code == 200
    assert client.get("/inv/admin/operators").status_code == 403


def test_user_without_inventory_role_is_refused(client, inv):
    login(client, "test:nobody")
    assert client.get("/inv/").status_code == 403
