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


def test_a_sort_order_outside_the_allowed_range_is_refused(client, inv):
    login(client, "test:invadmin")
    pallet = inv.kinds["PALLET"]
    for bad in ("-1", "10001", "abc", "1.5"):
        response = client.post(f"/inv/admin/kinds/{pallet.id}", data={
            "name": "Renamed", "sort_order": bad, "is_active": "1"},
            follow_redirects=True)
        assert response.status_code == 200
        assert "Sort order must be a whole number from 0 to 10000" in response.get_data(as_text=True)
        response = client.post("/inv/admin/kinds", data={"name": "Shelf", "sort_order": bad},
                               follow_redirects=True)
        assert "Sort order must be a whole number from 0 to 10000" in response.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(InvContainerKind, pallet.id).name == "Pallet"
    assert InvContainerKind.query.filter_by(code="SHELF").first() is None


def test_a_blank_or_boundary_sort_order_is_saved(client, inv):
    login(client, "test:invadmin")
    pallet = inv.kinds["PALLET"]
    client.post(f"/inv/admin/kinds/{pallet.id}", data={
        "name": "Pallet", "sort_order": "", "is_active": "1"})
    assert db.session.get(InvContainerKind, pallet.id).sort_order is None
    client.post(f"/inv/admin/kinds/{pallet.id}", data={
        "name": "Pallet", "sort_order": "10000", "is_active": "1"})
    assert db.session.get(InvContainerKind, pallet.id).sort_order == 10000
    client.post("/inv/admin/kinds", data={"name": "Shelf", "sort_order": "0"})
    assert InvContainerKind.query.filter_by(code="SHELF").one().sort_order == 0


def test_kinds_page_bounds_the_sort_inputs(client, inv):
    login(client, "test:invadmin")
    html = client.get("/inv/admin/kinds").get_data(as_text=True)
    assert html.count('min="0" max="10000"') >= 2


def test_fixed_spot_is_set_and_cleared_on_the_kinds_page(client, inv):
    kind = inv.kinds["PALLET"]
    login(client, "test:invadmin")
    page = client.get("/inv/admin/kinds").get_data(as_text=True)
    assert "<h1>Location and container kinds</h1>" in page and "Fixed spot" in page
    client.post(f"/inv/admin/kinds/{kind.id}", data={"name": "Pallet", "is_active": "1", "is_fixed": "1"})
    assert db.session.get(InvContainerKind, kind.id).is_fixed is True
    client.post(f"/inv/admin/kinds/{kind.id}", data={"name": "Pallet", "is_active": "1"})
    assert db.session.get(InvContainerKind, kind.id).is_fixed is False


def test_a_kind_created_with_fixed_spot_saves_it(client, inv):
    login(client, "test:invadmin")
    client.post("/inv/admin/kinds", data={"name": "Cage Bay", "is_fixed": "1"})
    assert InvContainerKind.query.filter_by(code="CAGE_BAY").one().is_fixed is True
