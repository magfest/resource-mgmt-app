"""The read-only allocation page: shortfall, department lines and assets."""
from app import db
from tests.inventory.helpers import login, make_allocation, make_asset, supply_line


def _page(client, alloc, **args):
    query = "&".join(f"{k}={v}" for k, v in args.items())
    return client.get(f"/inv/allocation?{query}").get_data(as_text=True)


def test_operator_reads_and_nobody_is_refused(client, alloc):
    login(client, "test:nobody")
    assert client.get("/inv/allocation").status_code == 403
    login(client, "test:operator")
    assert client.get("/inv/allocation").status_code == 200


def test_page_shows_shortfall_lines_and_assets(client, alloc):
    supply_line(alloc, alloc.alpha, 4, approved=2, notes="one 4K")
    supply_line(alloc, alloc.beta, 2)
    for i in range(1, 4):
        make_asset(alloc.tv, f"U-000{i}", name=f"Lobby TV {i}")
    db.session.commit()
    login(client, "test:techopsadmin")
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id)
    assert "Test TV: 6 requested · 2 approved · 0 allocated · 3 free" in page
    assert "Free means in service and allocated to nothing, for any event." in page
    assert "Alpha Team · one 4K" in page and "0 of 4 allocated" in page and "not reviewed" in page
    assert "U-0001" in page and "Lobby TV 1" in page and "Not allocated" in page
    assert "<table" not in page


def test_default_event_is_preselected_and_falls_back_to_the_first(client, alloc):
    supply_line(alloc, alloc.alpha, 1, event=alloc.other_event)
    db.session.commit()
    login(client, "test:admin")
    page = _page(client, alloc)
    assert f'<option value="{alloc.other_event.id}" selected>' in page
    supply_line(alloc, alloc.alpha, 1)
    db.session.commit()
    page = _page(client, alloc)
    assert f'<option value="{alloc.event.id}" selected>' in page


def test_empty_states(client, inv):
    login(client, "test:admin")
    page = client.get("/inv/allocation").get_data(as_text=True)
    assert "No event has submitted requests or allocations yet." in page
    assert "No item needs pre-allocation yet. Switch it on from an item&#39;s About tab." in page


def test_unallocated_only_hides_allocated_assets(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2)
    make_allocation(make_asset(alloc.tv, "U-0001"), line)
    make_asset(alloc.tv, "U-0002")
    db.session.commit()
    login(client, "test:admin")
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id, show="unallocated")
    assert "U-0002" in page and "U-0001" not in page
    assert "U-0001" in _page(client, alloc, event=alloc.event.id, item=alloc.tv.id)


def test_an_allocated_asset_in_repair_still_lists_with_its_warning(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2, notes="one 4K")
    held = make_asset(alloc.tv, "U-0001")
    make_allocation(held, line, words="one 4K")
    make_asset(alloc.tv, "U-0002", condition="REPAIR")
    held.condition = "REPAIR"
    db.session.commit()
    login(client, "test:admin")
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id)
    assert "Allocated to Alpha Team · one 4K" in page
    assert "U-0001 is in repair" in page
    assert "U-0002" not in page


def test_an_allocation_for_another_event_names_it(client, alloc):
    other = supply_line(alloc, alloc.alpha, 2, event=alloc.other_event, notes="one 4K")
    supply_line(alloc, alloc.beta, 1)
    make_allocation(make_asset(alloc.tv, "U-0001"), other, words="one 4K")
    db.session.commit()
    login(client, "test:admin")
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id)
    assert "Allocated to Alpha Team · one 4K (TST28)" in page


def test_person_text_is_escaped(client, alloc):
    supply_line(alloc, alloc.alpha, 2, notes="<b>x</b>")
    db.session.commit()
    login(client, "test:admin")
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id)
    assert "&lt;b&gt;" in page and "<b>x</b>" not in page

