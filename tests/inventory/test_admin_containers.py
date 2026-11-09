from app import db
from app.models import InvContainer, InvMovement
from tests.inventory.helpers import login, make_container


def test_batch_create_allocates_codes_and_logs_each(client, inv):
    login(client, "test:invadmin")
    client.post("/inv/admin/containers", data={
        "kind_id": str(inv.kinds["PALLET"].id), "count": "3"})
    codes = [c.label_code for c in InvContainer.query.order_by(InvContainer.id)]
    assert codes == ["C-0001", "C-0002", "C-0003"]
    assert InvMovement.query.filter(InvMovement.subject_container_id.isnot(None)).count() == 3


def test_moving_a_container_into_its_child_is_refused(client, inv):
    login(client, "test:invadmin")
    client.post("/inv/admin/containers", data={"kind_id": str(inv.kinds["PALLET"].id), "count": "1"})
    client.post("/inv/admin/containers", data={
        "kind_id": str(inv.kinds["BIN"].id), "count": "1", "parent_code": "C-0001"})
    pallet = InvContainer.query.filter_by(label_code="C-0001").one()

    response = client.post(f"/inv/admin/containers/{pallet.id}", data={
        "kind_id": str(inv.kinds["PALLET"].id), "parent_code": "C-0002"},
        follow_redirects=True)
    assert "inside itself" in response.get_data(as_text=True)
    assert InvContainer.query.filter_by(label_code="C-0001").one().parent_id is None


def test_operator_cannot_create_containers(client, inv):
    login(client, "test:operator")
    response = client.post("/inv/admin/containers", data={
        "kind_id": str(inv.kinds["BIN"].id), "count": "1"})
    assert response.status_code == 403


def test_inactive_kind_stays_selected_when_renaming(client, inv):
    pallet_container = make_container(inv, "C-0001", kind="PALLET")
    inv.kinds["PALLET"].is_active = False
    db.session.commit()
    login(client, "test:invadmin")

    page = client.get(f"/inv/admin/containers?edit={pallet_container.id}")
    assert "(inactive)" in page.get_data(as_text=True)

    client.post(f"/inv/admin/containers/{pallet_container.id}", data={
        "kind_id": str(inv.kinds["PALLET"].id), "name": "Front stage pallet"})
    saved = db.session.get(InvContainer, pallet_container.id)
    assert saved.kind_id == inv.kinds["PALLET"].id
    assert saved.name == "Front stage pallet"


def test_batch_create_reports_a_code_that_is_already_a_container(client, inv, monkeypatch):
    from app.inventory import ledger
    make_container(inv, "C-0001", kind="BIN")
    db.session.commit()
    # Skip the pre-check so the unique constraint decides, as it does in a race.
    monkeypatch.setattr(ledger, "_container_exists", lambda code: False)
    monkeypatch.setattr("app.routes.inventory.admin_containers.allocate",
                        lambda prefix, count: ["C-0001"])
    login(client, "test:invadmin")
    response = client.post("/inv/admin/containers", data={
        "kind_id": str(inv.kinds["BIN"].id), "count": "1"}, follow_redirects=True)
    assert response.status_code == 200
    assert "C-0001 is already a container" in response.get_data(as_text=True)
    assert InvContainer.query.count() == 1


def test_containers_page_lists_locations_then_containers(client, inv):
    shelf = make_container(inv, "C-9101", kind="LOCATION", name="Shelf A")
    make_container(inv, "C-9102", kind="PALLET", parent=shelf)
    db.session.commit()
    login(client, "test:invadmin")
    page = client.get("/inv/admin/containers").get_data(as_text=True)
    assert "<h1>Locations and containers</h1>" in page
    locations, containers = page.split("<h2>Containers</h2>")
    assert "<h2>Locations</h2>" in locations and "C-9101" in locations and "C-9102" not in locations
    assert "C-9102" in containers
