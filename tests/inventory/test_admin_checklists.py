from app import db
from app.inventory.checks import active_check_items
from app.models import InvCheckItem
from tests.inventory.helpers import login


def _add(client, item, prompt, means="FAIL"):
    return client.post(f"/inv/admin/checklists/{item.id}/lines",
                       data={"prompt": prompt, "missing_means": means})


def _positions(item):
    db.session.expire_all()
    rows = InvCheckItem.query.filter_by(item_id=item.id).order_by(InvCheckItem.position).all()
    return [(r.position, r.prompt) for r in rows]


def test_admin_adds_and_orders_checklist_lines(client, inv):
    login(client, "test:invadmin")
    for prompt in ("Powered on?", "Remote present, no batteries?"):
        _add(client, inv.laptop, prompt)
    second = InvCheckItem.query.filter_by(prompt="Remote present, no batteries?").one()
    client.post(f"/inv/admin/checklists/lines/{second.id}/move", data={"direction": "up"})
    assert [i.prompt for i in active_check_items(inv.laptop)][0] == "Remote present, no batteries?"


def test_missing_means_must_be_fail_or_note(client, inv):
    login(client, "test:invadmin")
    response = client.post(f"/inv/admin/checklists/{inv.laptop.id}/lines",
                           data={"prompt": "Powered on?", "missing_means": "MAYBE"},
                           follow_redirects=True)
    assert "must mean FAIL or NOTE" in response.get_data(as_text=True)
    assert InvCheckItem.query.count() == 0
    _add(client, inv.laptop, "Powered on?", "NOTE")
    line = InvCheckItem.query.one()
    client.post(f"/inv/admin/checklists/lines/{line.id}",
                data={"prompt": "Changed", "missing_means": "MAYBE", "is_active": "1"})
    db.session.expire_all()
    assert (line.prompt, line.missing_means) == ("Powered on?", "NOTE")


def test_operators_cannot_edit_checklists(client, inv):
    login(client, "test:operator")
    assert _add(client, inv.laptop, "Powered on?").status_code == 403
    assert client.get("/inv/admin/checklists").status_code == 403
    assert InvCheckItem.query.count() == 0


def test_inactive_lines_leave_the_check_but_stay_on_the_page(client, inv):
    login(client, "test:invadmin")
    _add(client, inv.laptop, "Powered on?")
    _add(client, inv.laptop, "Screen cracked?")
    old = InvCheckItem.query.filter_by(prompt="Screen cracked?").one()
    client.post(f"/inv/admin/checklists/lines/{old.id}",
                data={"prompt": "Screen cracked?", "missing_means": "FAIL"})
    assert [i.prompt for i in active_check_items(inv.laptop)] == ["Powered on?"]
    page = client.get(f"/inv/admin/checklists?item_id={inv.laptop.id}").get_data(as_text=True)
    assert "Screen cracked?" in page


def test_moves_at_the_edges_change_nothing_and_positions_stay_contiguous(client, inv):
    login(client, "test:invadmin")
    for prompt in ("A", "B", "C"):
        _add(client, inv.laptop, prompt)
    a, b, c = (InvCheckItem.query.filter_by(prompt=p).one() for p in "ABC")
    client.post(f"/inv/admin/checklists/lines/{a.id}/move", data={"direction": "up"})
    client.post(f"/inv/admin/checklists/lines/{c.id}/move", data={"direction": "down"})
    assert _positions(inv.laptop) == [(0, "A"), (1, "B"), (2, "C")]
    client.post(f"/inv/admin/checklists/lines/{a.id}/move", data={"direction": "down"})
    assert _positions(inv.laptop) == [(0, "B"), (1, "A"), (2, "C")]


def test_a_blank_or_overlong_prompt_is_refused(client, inv):
    login(client, "test:invadmin")
    for bad in ("   ", "x" * 129):
        response = client.post(f"/inv/admin/checklists/{inv.laptop.id}/lines",
                               data={"prompt": bad, "missing_means": "FAIL"},
                               follow_redirects=True)
        assert "1 to 128 characters" in response.get_data(as_text=True)
    assert InvCheckItem.query.count() == 0


def test_only_serialized_models_are_offered(client, inv):
    login(client, "test:invadmin")
    page = client.get("/inv/admin/checklists").get_data(as_text=True)
    assert "Laptop" in page and "Blue Pen" not in page
    assert client.get(f"/inv/admin/checklists?item_id={inv.pens.id}").status_code == 404
