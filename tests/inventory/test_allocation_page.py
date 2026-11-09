"""The read-only allocation page: shortfall, department lines and assets."""
import html
import re

from sqlalchemy import event as sa_event

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
    assets = page.split("Allocation views")[1]
    first, second = assets.split("U-0002")
    assert "Line changed: removed" in first.split("U-0001")[1]
    assert "Line changed: removed" not in second


def test_ticked_button_shows_above_and_below_only_with_a_tickable_asset(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2)
    make_allocation(make_asset(alloc.tv, "U-0001"), line)
    db.session.commit()
    login(client, "test:admin")
    assert "Allocate ticked to…" not in _page(client, alloc, event=alloc.event.id, item=alloc.tv.id)
    make_asset(alloc.tv, "U-0002")
    db.session.commit()
    page = _page(client, alloc, event=alloc.event.id, item=alloc.tv.id)
    assert page.count("Allocate ticked to…") == 2


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
    assert 'role="tablist"' in page and 'aria-current="page"' in page


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
