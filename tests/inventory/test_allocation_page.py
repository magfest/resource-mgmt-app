"""The read-only allocation page: shortfall, department lines and assets."""
import html
import re

from sqlalchemy import event as sa_event

from app import db
from app.models import InvAllocation
from tests.inventory.helpers import login, make_allocation, make_asset, supply_line


def _page(client, alloc, **args):
    query = "&".join(f"{k}={v}" for k, v in args.items())
    return client.get(f"/inv/allocation?{query}").get_data(as_text=True)


def _assets_page(client, alloc, **args):
    return _page(client, alloc, event=alloc.event.id, item=alloc.tv.id, **args)


def _select(page, unit):
    """The inside of the unit's line dropdown."""
    found = re.search(rf'<select[^>]*name="to_{unit.id}"[^>]*>(.*?)</select>', page, re.S)
    assert found, page
    return found.group(1)


def _selected(page, unit):
    """The (value, text) of every selected option in the unit's dropdown."""
    return [(value, html.unescape(text)) for value, text in re.findall(
        r'<option value="([^"]*)"[^>]* selected[^>]*>([^<]*)</option>', _select(page, unit))]


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
    assert "U-0001" in page and "Lobby TV 1" in page and "Not allocated" in page
    lines = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id, tab="lines")
    assert "one 4K" in lines and "0 of 4 allocated" in lines and "not reviewed" in lines
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
    assert "No item needs pre-allocation yet. Switch it on from an item's About tab." in page


def test_unallocated_only_hides_allocated_assets(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2)
    make_allocation(make_asset(alloc.tv, "U-0001"), line)
    make_asset(alloc.tv, "U-0002")
    db.session.commit()
    login(client, "test:admin")
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id, show="unallocated")
    assets = page.split("Allocation views")[1]
    assert "U-0002" in assets and "U-0001" not in assets
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
    assert _selected(page, held) == [(str(line.id), "Alpha Team · SUP-1 line 1 · one 4K · 1 left")]
    assert "U-0001 is in repair" in page
    assert "U-0002" not in page


def test_an_allocation_for_another_event_names_it(client, alloc):
    other = supply_line(alloc, alloc.alpha, 2, event=alloc.other_event, notes="one 4K")
    supply_line(alloc, alloc.beta, 1)
    unit = make_asset(alloc.tv, "U-0001")
    make_allocation(unit, other, words="one 4K")
    db.session.commit()
    login(client, "test:admin")
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id)
    assert _selected(page, unit) == [(str(other.id), "Alpha Team · SUP-1 line 1 · one 4K (TST28)")]
    login(client, "test:operator")
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id)
    assert "Allocated to Alpha Team · one 4K (TST28)" in page


def test_person_text_is_escaped(client, alloc):
    supply_line(alloc, alloc.alpha, 2, notes="<b>x</b>")
    db.session.commit()
    login(client, "test:admin")
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id, tab="lines")
    assert "&lt;b&gt;" in page and "<b>x</b>" not in page



def test_asset_rows_run_in_a_fixed_number_of_selects(client, alloc):
    login(client, "test:admin")

    def selects_for(n):
        for i in range(n):
            line = supply_line(alloc, alloc.alpha, 2, event=alloc.other_event)
            make_allocation(make_asset(alloc.tv, f"U-{n}{i:03d}"), line)
        db.session.commit()
        seen = [0]

        def count(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith("SELECT"):
                seen[0] += 1

        sa_event.listen(db.engine, "before_cursor_execute", count)
        try:
            _page(client, alloc, event=alloc.event.id, item=alloc.tv.id)
        finally:
            sa_event.remove(db.engine, "before_cursor_execute", count)
        db.session.expire_all()
        return seen[0]

    assert selects_for(1) == selects_for(5)


def test_a_stale_pick_falls_back_to_the_first_selection(client, alloc):
    supply_line(alloc, alloc.alpha, 3)
    db.session.commit()
    login(client, "test:admin")
    for args in ({"pick": "item:999999"}, {"item": 999999}, {"service": 999999}):
        page = _page(client, alloc, event=alloc.event.id, **args)
        assert "Test TV: 3 requested · 0 approved · 0 allocated · 0 free" in page


def test_a_printer_item_without_supply_lines_shows_only_its_service(client, alloc):
    from tests.inventory.helpers import techops_line
    techops_line(alloc, alloc.alpha, usage="front desk")
    db.session.commit()
    login(client, "test:admin")
    page = _page(client, alloc, event=alloc.event.id)
    assert ">TechOps: Printer<" in page and ">Test Printer<" not in page
    assert "front desk" in _page(client, alloc, event=alloc.event.id, tab="lines",
                                 pick=f"service:{alloc.printer_service.id}")
    supply_line(alloc, alloc.alpha, 1, item=alloc.printer)
    db.session.commit()
    assert ">Test Printer<" in _page(client, alloc, event=alloc.event.id)


def test_each_line_lists_the_asset_numbers_allocated_to_it(client, alloc):
    line = supply_line(alloc, alloc.alpha, 3, notes="one 4K")
    make_allocation(make_asset(alloc.tv, "U-0001"), line)
    make_allocation(make_asset(alloc.tv, "U-0002"), line)
    db.session.commit()
    login(client, "test:admin")
    block = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id, tab="lines")
    assert "U-0001, U-0002" in block


def test_a_removed_line_marks_its_asset_row_and_a_live_line_does_not(client, alloc):
    gone = supply_line(alloc, alloc.alpha, 2, notes="one 4K")
    live = supply_line(alloc, alloc.beta, 2)
    make_allocation(make_asset(alloc.tv, "U-0001"), gone)
    make_allocation(make_asset(alloc.tv, "U-0002"), live)
    db.session.commit()
    login(client, "test:admin")
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id)
    assert "Line changed: removed" not in page
    db.session.delete(gone)
    db.session.commit()
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id)
    rows = page.split("Allocation views")[1].split("<li>")
    row_of = lambda number: next(r for r in rows if f"<strong>{number}</strong>" in r)
    assert "Line changed: removed" in row_of("U-0001")
    assert "Line changed: removed" not in row_of("U-0002")


NOTE_120 = "Four wall-mounted televisions for the registration hall, with brackets, power strips and a spare remote, " \
           "delivered Thursday."[:120]


def _lines_page(client, alloc, **args):
    return _page(client, alloc, event=alloc.event.id, item=alloc.tv.id, tab="lines", **args)


def test_lines_tab_shows_order_line_full_note_and_numbers(client, alloc):
    line = supply_line(alloc, alloc.alpha, 4, notes=NOTE_120)
    make_allocation(make_asset(alloc.tv, "U-0001"), line)
    db.session.commit()
    login(client, "test:admin")
    page = _lines_page(client, alloc)
    assert len(NOTE_120) == 120 and NOTE_120 in page
    assert "TST27-ALPHA-SUP-1 · line 1" in page
    assert "Requested 4 · Approved not reviewed · 1 of 4 allocated · 3 short · U-0001" in page
    assert "Alpha Team · 1 line · 1 of 4 allocated · 3 short" in page
    assert "Department lines (1)" in page and "Assets (1)" in page
    assert "<table" not in page


def test_lines_tab_says_no_note(client, alloc):
    supply_line(alloc, alloc.alpha, 2)
    db.session.commit()
    login(client, "test:admin")
    assert "No note" in _lines_page(client, alloc)


def test_lines_tab_filters_by_department_and_show(client, alloc):
    supply_line(alloc, alloc.alpha, 2)
    full = supply_line(alloc, alloc.beta, 1, notes="beta full")
    make_allocation(make_asset(alloc.tv, "U-0001"), full)
    db.session.commit()
    login(client, "test:admin")
    page = _lines_page(client, alloc, dept=alloc.beta.id)
    assert "Alpha Team ·" not in page.split("Allocation views")[-1] and "beta full" not in page
    assert "beta full" in _lines_page(client, alloc, lines="all")
    assert "beta full" not in _lines_page(client, alloc, lines="junk")
    assert "TST27-ALPHA-SUP-1" in _lines_page(client, alloc, lines="junk")
    assert "beta full" in _lines_page(client, alloc, lines="notes")
    assert "TST27-ALPHA-SUP-1" not in _lines_page(client, alloc, lines="notes")


def test_unknown_department_means_all_departments(client, alloc):
    supply_line(alloc, alloc.alpha, 2)
    db.session.commit()
    login(client, "test:admin")
    assert "TST27-ALPHA-SUP-1" in _lines_page(client, alloc, dept=999999)


def test_tabs_carry_filters(client, alloc):
    supply_line(alloc, alloc.alpha, 2)
    supply_line(alloc, alloc.beta, 2)
    db.session.commit()
    login(client, "test:admin")
    page = _lines_page(client, alloc, dept=alloc.beta.id, lines="all")
    link = re.search(r'<a [^>]*href="([^"]*)"[^>]*>\s*Assets \(', page)
    assert link, page
    href = html.unescape(link.group(1))
    assert f"dept={alloc.beta.id}" in href and "lines=all" in href and "tab=assets" in href
    assert f"pick=item:{alloc.tv.id}" in href
    # Plain links in a tablist would need role="tab"; aria-current marks the open view instead.
    assert 'role="tablist"' not in page and 'aria-current="page"' in page


def test_unknown_tab_means_assets(client, alloc):
    make_asset(alloc.tv, "U-0001")
    supply_line(alloc, alloc.alpha, 2)
    db.session.commit()
    login(client, "test:admin")
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id, tab="junk")
    assert "U-0001" in page and "TST27-ALPHA-SUP-1" not in page


def test_lines_tab_escapes_notes(client, alloc):
    supply_line(alloc, alloc.alpha, 2, notes="<b>4 TVs</b>")
    db.session.commit()
    login(client, "test:admin")
    page = _lines_page(client, alloc)
    assert "&lt;b&gt;4 TVs&lt;/b&gt;" in page and "<b>4 TVs</b>" not in page


def test_removed_row_shows_saved_words_pill_and_asset(client, alloc):
    row = make_allocation(make_asset(alloc.tv, "U-0001"), supply_line(alloc, alloc.alpha, 1),
                          "Gone TVs")
    row.work_line_id = None
    db.session.commit()
    login(client, "test:admin")
    page = _lines_page(client, alloc)
    assert "Gone TVs" in page and "Line changed: removed" in page and "U-0001" in page


def test_lines_tab_runs_in_a_fixed_number_of_selects(client, alloc):
    login(client, "test:admin")
    depts = [alloc.alpha, alloc.beta]

    def selects_for(n):
        for i in range(n):
            line = supply_line(alloc, depts[i % 2], 2, notes=f"note {n}{i}")
            make_allocation(make_asset(alloc.tv, f"U-{n}{i:03d}"), line)
        db.session.commit()
        seen = [0]

        def count(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith("SELECT"):
                seen[0] += 1

        sa_event.listen(db.engine, "before_cursor_execute", count)
        try:
            _lines_page(client, alloc, lines="all")
        finally:
            sa_event.remove(db.engine, "before_cursor_execute", count)
        db.session.expire_all()
        return seen[0]

    assert selects_for(1) == selects_for(5)


def test_each_asset_row_has_a_line_select_set_to_its_line(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2, notes="any")
    held = make_asset(alloc.tv, "U-0001")
    make_allocation(held, line, words="any")
    free = make_asset(alloc.tv, "U-0002")
    db.session.commit()
    login(client, "test:admin")
    page = _assets_page(client, alloc)
    assert _selected(page, held) == [(str(line.id), "Alpha Team · SUP-1 line 1 · any · 1 left")]
    assert _selected(page, free) == [("", "Not allocated")]
    assert '<optgroup label="Alpha Team">' in _select(page, free)
    assert page.count('action="/inv/allocation/save"') == 1
    assert f'name="was_{held.id}" value="{line.id}"' in page
    assert f'name="was_{free.id}" value=""' in page
    assets = page.split("Allocation views")[1]
    for text in ("Allocate to…", "Move to…", ">Release<", "Tick", "Allocate ticked", "/allocation/pick"):
        assert text not in assets
    assert "Save changes" in assets


def test_a_full_current_line_and_a_removed_line_still_show_as_selected(client, alloc):
    full = supply_line(alloc, alloc.alpha, 1, notes="one only")
    a = make_asset(alloc.tv, "U-0001")
    make_allocation(a, full, words="one only")
    b = make_asset(alloc.tv, "U-0002")
    row = make_allocation(b, supply_line(alloc, alloc.beta, 1), words="Gone TVs")
    row.work_line_id = None
    db.session.commit()
    login(client, "test:admin")
    page = _assets_page(client, alloc)
    assert _selected(page, a) == [(str(full.id), "Alpha Team · SUP-1 line 1 · one only")]
    assert _selected(page, b) == [("", "Line changed: removed · Gone TVs")]
    assert f'value="{full.id}"' not in _select(page, b)


def test_a_warned_asset_offers_only_its_line_and_not_allocated(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2, notes="any")
    supply_line(alloc, alloc.beta, 2, notes="other room")
    held = make_asset(alloc.tv, "U-0001")
    make_allocation(held, line, words="any")
    held.condition = "REPAIR"
    db.session.commit()
    login(client, "test:admin")
    body = _select(_assets_page(client, alloc), held)
    assert re.findall(r'<option value="([^"]*)"', body) == ["", str(line.id)]
    assert "other room" not in body and "<optgroup" not in body


def test_department_filter_narrows_assets_and_choices(client, alloc):
    mine_line = supply_line(alloc, alloc.alpha, 3, notes="alpha wants")
    theirs_line = supply_line(alloc, alloc.beta, 3, notes="beta wants")
    other = supply_line(alloc, alloc.alpha, 2, event=alloc.other_event)
    make_allocation(make_asset(alloc.tv, "U-0001"), mine_line)
    make_allocation(make_asset(alloc.tv, "U-0002"), theirs_line)
    make_allocation(make_asset(alloc.tv, "U-0003"), other)
    make_asset(alloc.tv, "U-0004")
    db.session.commit()
    login(client, "test:admin")
    page = _assets_page(client, alloc, dept=alloc.alpha.id)
    assets = page.split("Allocation views")[1]
    assert "U-0001" in assets and "U-0004" in assets
    assert "U-0002" not in assets and "U-0003" not in assets
    assert "beta wants" not in assets and "alpha wants" in assets
    assert "Assets (2)" in page
    assert "Assets (4)" in _assets_page(client, alloc)


def test_a_refused_save_keeps_every_pick(client, alloc):
    room = supply_line(alloc, alloc.alpha, 2, notes="room")
    held_line = supply_line(alloc, alloc.beta, 2, notes="held")
    full = supply_line(alloc, alloc.beta, 1, notes="full")
    u1, u2, u3, u4 = (make_asset(alloc.tv, f"U-000{i}") for i in (1, 2, 3, 4))
    make_allocation(u2, held_line)
    make_allocation(u4, full)
    db.session.commit()
    login(client, "test:admin")
    data = {"event": alloc.event.id, "item": alloc.tv.id, "tab": "assets",
            "dept": alloc.beta.id,
            f"was_{u1.id}": "", f"to_{u1.id}": room.id,
            f"was_{u2.id}": held_line.id, f"to_{u2.id}": "",
            f"was_{u3.id}": "", f"to_{u3.id}": full.id}
    response = client.post("/inv/allocation/save", data=data)
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'role="alert"' in page
    assert db.session.query(InvAllocation).count() == 2
    assert [v for v, _ in _selected(page, u2)] == [""]
    assert [v for v, _ in _selected(page, u3)] == [str(full.id)]
    assert f'name="was_{u2.id}" value="{held_line.id}"' in page
    assert f'name="was_{u3.id}" value=""' in page
    # Under the Beta filter an Alpha line is not a choice, yet the pick still shows.
    assert [v for v, _ in _selected(page, u1)] == [str(room.id)]
    assert f'name="was_{u1.id}" value=""' in page
    assert 'name="tab" value="assets"' in page and f'name="dept" value="{alloc.beta.id}"' in page


def test_row_never_shows_only_a_number(client, alloc):
    supply_line(alloc, alloc.alpha, 1)
    make_asset(alloc.tv, "U-0001")
    db.session.commit()
    login(client, "test:admin")
    assert '<div class="muted alloc-label">Test TV</div>' in _assets_page(client, alloc)


def test_readers_see_text_and_no_selects(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2, notes="any")
    unit = make_asset(alloc.tv, "U-0001")
    make_allocation(unit, line, words="any")
    make_asset(alloc.tv, "U-0002")
    db.session.commit()
    login(client, "test:operator")
    page = _assets_page(client, alloc)
    assert "Allocated to Alpha Team · any" in page and "Not allocated" in page
    for text in ("<select name=\"to_", "was_", "Save changes", "/inv/allocation/save", 'id="alloc-save"'):
        assert text not in page
    response = client.post("/inv/allocation/save", data={
        "event": alloc.event.id, f"was_{unit.id}": line.id, f"to_{unit.id}": ""})
    assert response.status_code == 403
    assert db.session.query(InvAllocation).count() == 1


def test_the_page_has_no_inline_handlers(client, alloc):
    supply_line(alloc, alloc.alpha, 2, notes="any")
    make_asset(alloc.tv, "U-0001")
    db.session.commit()
    login(client, "test:admin")
    page = _assets_page(client, alloc)
    assert not re.search(r"\son[a-z]+=", page.split("</head>", 1)[1])
    # The layout's own <style> sits in <head>; every style this page adds carries a nonce.
    assert "<style>" not in page.split("</head>", 1)[1]
    assert re.search(r'<script nonce="[^"]+" src="/static/inventory/allocation.js', page)


def test_the_picker_and_action_routes_are_gone(client, alloc):
    login(client, "test:admin")
    assert client.get("/inv/allocation/pick").status_code in (404, 405)
    for action in ("allocate", "move", "release"):
        assert client.post(f"/inv/allocation/{action}", data={}).status_code in (404, 405)


def test_dropdown_escapes_notes(client, alloc):
    supply_line(alloc, alloc.alpha, 2, notes="<b>4 TVs</b>")
    unit = make_asset(alloc.tv, "U-0001")
    db.session.commit()
    login(client, "test:admin")
    page = _assets_page(client, alloc)
    assert "&lt;b&gt;4 TVs&lt;/b&gt;" in _select(page, unit) and "<b>4 TVs</b>" not in page
