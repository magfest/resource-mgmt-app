"""Picker, allocate, move and release from the allocation page."""
from app import db
from app.models import InvAllocation
from tests.inventory.helpers import login, make_allocation, make_asset, supply_line


def _post(client, action, alloc, **data):
    data.update(event=alloc.event.id, item=alloc.tv.id)
    return client.post(f"/inv/allocation/{action}", data=data, follow_redirects=True)


def _page(client, alloc):
    return client.get(f"/inv/allocation?event={alloc.event.id}&item={alloc.tv.id}").get_data(as_text=True)


def test_operator_cannot_write(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2)
    unit = make_asset(alloc.tv, "U-0001")
    db.session.commit()
    login(client, "test:operator")
    for action, data in (("allocate", {"unit": unit.id, "line_id": line.id}),
                         ("release", {"unit": unit.id})):
        assert client.post(f"/inv/allocation/{action}", data=data).status_code == 403
    assert client.get(f"/inv/allocation/pick?unit={unit.id}").status_code == 403
    assert db.session.query(InvAllocation).count() == 0


def test_picker_lists_open_lines_with_what_is_left(client, alloc):
    supply_line(alloc, alloc.alpha, 4, notes="one 4K")
    full = supply_line(alloc, alloc.beta, 1, notes="any")
    make_allocation(make_asset(alloc.tv, "U-0009"), full)
    unit = make_asset(alloc.tv, "U-0001")
    db.session.commit()
    login(client, "test:techopsadmin")
    page = client.get(f"/inv/allocation/pick?event={alloc.event.id}&item={alloc.tv.id}"
                      f"&unit={unit.id}").get_data(as_text=True)
    assert "Allocate U-0001 to…" in page and "Alpha Team · one 4K · 4 left" in page
    assert "Beta Team · any" not in page


def test_picker_without_a_line_says_so(client, alloc):
    unit = make_asset(alloc.tv, "U-0001")
    full = supply_line(alloc, alloc.alpha, 1)
    make_allocation(make_asset(alloc.tv, "U-0009"), full)
    db.session.commit()
    login(client, "test:admin")
    page = client.get(f"/inv/allocation/pick?event={alloc.event.id}&item={alloc.tv.id}"
                      f"&unit={unit.id}").get_data(as_text=True)
    assert "No line has room left for this event." in page


def test_move_picker_omits_the_current_line(client, alloc):
    alpha = supply_line(alloc, alloc.alpha, 2, notes="any")
    supply_line(alloc, alloc.beta, 2, notes="any")
    unit = make_asset(alloc.tv, "U-0001")
    make_allocation(unit, alpha, words="any")
    db.session.commit()
    login(client, "test:admin")
    page = client.get(f"/inv/allocation/pick?event={alloc.event.id}&item={alloc.tv.id}"
                      f"&unit={unit.id}&move=1").get_data(as_text=True)
    assert "Move U-0001 to…" in page
    assert "Beta Team · any · 2 left" in page and "Alpha Team · any" not in page


def test_ticked_assets_allocate_in_one_action(client, alloc):
    line = supply_line(alloc, alloc.alpha, 4, notes="any")
    units = [make_asset(alloc.tv, f"U-000{i}") for i in (1, 2, 3)]
    db.session.commit()
    login(client, "test:supplyadmin")
    page = _post(client, "allocate", alloc, unit=[u.id for u in units],
                 line_id=line.id).get_data(as_text=True)
    assert "Allocated 3 assets to Alpha Team · any." in page
    assert "3 of 4 allocated" in page


def test_a_refused_batch_flashes_and_writes_nothing(client, alloc):
    line = supply_line(alloc, alloc.alpha, 1, notes="any")
    units = [make_asset(alloc.tv, f"U-000{i}") for i in (1, 2)]
    db.session.commit()
    login(client, "test:admin")
    page = _post(client, "allocate", alloc, unit=[u.id for u in units],
                 line_id=line.id).get_data(as_text=True)
    assert "Alpha Team · any has 1 left, and you picked 2." in page
    assert db.session.query(InvAllocation).count() == 0


def test_nothing_ticked_is_refused(client, alloc):
    line = supply_line(alloc, alloc.alpha, 1)
    db.session.commit()
    login(client, "test:admin")
    page = _post(client, "allocate", alloc, line_id=line.id).get_data(as_text=True)
    assert "Tick at least one asset." in page


def test_move_and_release_from_the_page(client, alloc):
    alpha = supply_line(alloc, alloc.alpha, 2, notes="any")
    beta = supply_line(alloc, alloc.beta, 2, notes="any")
    unit = make_asset(alloc.tv, "U-0001")
    make_allocation(unit, alpha, words="any")
    db.session.commit()
    login(client, "test:admin")
    page = _post(client, "move", alloc, unit=unit.id, line_id=beta.id).get_data(as_text=True)
    assert "Moved U-0001 to Beta Team · any." in page
    assert db.session.query(InvAllocation).one().work_line_id == beta.id
    page = _post(client, "release", alloc, unit=unit.id).get_data(as_text=True)
    assert "Released U-0001." in page
    assert db.session.query(InvAllocation).count() == 0


def test_a_stale_allocate_on_an_allocated_asset_is_refused(client, alloc):
    alpha = supply_line(alloc, alloc.alpha, 2, notes="any")
    beta = supply_line(alloc, alloc.beta, 2, notes="any")
    unit = make_asset(alloc.tv, "U-0001")
    make_allocation(unit, beta, words="any")
    db.session.commit()
    login(client, "test:admin")
    page = _post(client, "allocate", alloc, unit=unit.id, line_id=alpha.id).get_data(as_text=True)
    assert "U-0001 is already allocated to Beta Team · any." in page
    assert db.session.query(InvAllocation).one().work_line_id == beta.id


def test_an_unknown_unit_is_a_404(client, alloc):
    line = supply_line(alloc, alloc.alpha, 1)
    db.session.commit()
    login(client, "test:admin")
    response = client.post("/inv/allocation/allocate",
                           data={"unit": 999999, "line_id": line.id})
    assert response.status_code == 404


def test_rows_show_actions_only_to_allocators(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2, notes="any")
    make_asset(alloc.tv, "U-0001")
    make_allocation(make_asset(alloc.tv, "U-0002"), line, words="any")
    db.session.commit()
    login(client, "test:techopsadmin")
    page = _page(client, alloc)
    for text in ("Allocate to…", "Allocate ticked to…", 'form="alloc-pick"', "Move to…", "Release"):
        assert text in page
    login(client, "test:operator")
    page = _page(client, alloc)
    for text in ("Allocate to…", "Allocate ticked to…", 'form="alloc-pick"', "Move to…", "Release"):
        assert text not in page


def test_the_page_has_no_inline_handlers(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2, notes="any")
    unit = make_asset(alloc.tv, "U-0001")
    db.session.commit()
    login(client, "test:admin")
    pages = [_page(client, alloc),
             client.get(f"/inv/allocation/pick?event={alloc.event.id}&item={alloc.tv.id}"
                        f"&unit={unit.id}").get_data(as_text=True)]
    for page in pages:
        assert "onclick=" not in page and "onchange=" not in page
        # The layout's own <style> sits in <head>; every style this page adds carries a nonce.
        assert "<style>" not in page.split("</head>", 1)[1]
