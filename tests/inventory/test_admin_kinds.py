from app import db
from app.models import InvContainerKind
from tests.inventory.helpers import login, make_container


def test_admin_adds_a_kind_with_a_derived_code(client, inv):
    login(client, "test:invadmin")
    client.post("/inv/admin/kinds", data={"name": "Rolling Cart"})
    kind = InvContainerKind.query.filter_by(code="ROLLING_CART").one()
    assert kind.name == "Rolling Cart"


def test_a_kind_in_use_can_be_deactivated_and_still_lists(client, inv):
    make_container(inv, "C-0001")
    pallet = inv.kinds["PALLET"]
    login(client, "test:invadmin")
    client.post(f"/inv/admin/kinds/{pallet.id}", data={
        "name": "Pallet", "sort_order": "50"})
    assert db.session.get(InvContainerKind, pallet.id).is_active is False
    assert "Pallet" in client.get("/inv/admin/kinds").get_data(as_text=True)


def test_operator_cannot_manage_kinds(client, inv):
    login(client, "test:operator")
    assert client.post("/inv/admin/kinds", data={"name": "Shelf"}).status_code == 403
