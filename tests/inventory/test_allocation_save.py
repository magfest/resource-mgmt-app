"""Batch save from the allocation page."""
from app import db
from app.inventory.allocation import RowChange, current_allocation, save_changes
from app.models import InvAllocation, InvAllocationEvent
from tests.inventory.helpers import login, make_allocation, make_asset, supply_line


def _save(client, alloc, rows):
    """Post rows given as (unit, was_line, to_line); a line is a WorkLine or None."""
    data = {"event": alloc.event.id, "item": alloc.tv.id}
    for unit, was, to in rows:
        data[f"was_{unit.id}"] = was.id if was else ""
        data[f"to_{unit.id}"] = to.id if to else ""
    return client.post("/inv/allocation/save", data=data, follow_redirects=True)


def _held(unit):
    row = db.session.query(InvAllocation).filter_by(unit_id=unit.id).first()
    return row.work_line_id if row else None


def test_save_allocates_moves_and_releases_in_one_post(client, alloc):
    a = supply_line(alloc, alloc.alpha, 2, notes="any")
    b = supply_line(alloc, alloc.beta, 2, notes="any")
    u1, u2, u3 = (make_asset(alloc.tv, f"U-000{i}") for i in (1, 2, 3))
    make_allocation(u2, a)
    make_allocation(u3, b)
    db.session.commit()
    login(client, "test:admin")
    page = _save(client, alloc, [(u1, None, a), (u2, a, b), (u3, b, None)]).get_data(as_text=True)
    assert "Saved 3 changes." in page
    assert (_held(u1), _held(u2), _held(u3)) == (a.id, b.id, None)
    actions = sorted(e.action for e in db.session.query(InvAllocationEvent)
                     .filter(InvAllocationEvent.unit_id.in_([u1.id, u2.id, u3.id])))
    assert actions == ["ALLOCATE", "MOVE", "RELEASE"]


def test_two_rows_to_one_line_are_one_allocate(client, alloc):
    line = supply_line(alloc, alloc.alpha, 4, notes="any")
    u1, u2 = (make_asset(alloc.tv, f"U-000{i}") for i in (1, 2))
    db.session.commit()
    login(client, "test:admin")
    page = _save(client, alloc, [(u1, None, line), (u2, None, line)]).get_data(as_text=True)
    assert "Saved 2 changes." in page
    assert _held(u1) == line.id and _held(u2) == line.id


def test_a_row_changed_by_someone_else_is_skipped_and_named(client, alloc):
    a = supply_line(alloc, alloc.alpha, 2, notes="any")
    b = supply_line(alloc, alloc.beta, 2, notes="any")
    u1, u2 = (make_asset(alloc.tv, f"U-000{i}") for i in (1, 2))
    make_allocation(u2, b)
    db.session.commit()
    login(client, "test:admin")
    page = _save(client, alloc, [(u1, None, a), (u2, None, a)]).get_data(as_text=True)
    assert "U-0002 was changed by someone else; it was not saved." in page
    assert "Saved 1 change." in page
    assert _held(u2) == b.id and _held(u1) == a.id


def test_a_row_already_holding_the_choice_is_quiet(client, alloc):
    a = supply_line(alloc, alloc.alpha, 2, notes="any")
    u2 = make_asset(alloc.tv, "U-0002")
    make_allocation(u2, a)
    db.session.commit()
    login(client, "test:admin")
    page = _save(client, alloc, [(u2, None, a)]).get_data(as_text=True)
    assert "Nothing to save." in page and "changed by someone else" not in page


def test_a_stale_only_save_does_not_say_nothing_to_save(client, alloc):
    a = supply_line(alloc, alloc.alpha, 2, notes="any")
    b = supply_line(alloc, alloc.beta, 2, notes="any")
    u2 = make_asset(alloc.tv, "U-0002")
    make_allocation(u2, b)
    db.session.commit()
    login(client, "test:admin")
    page = _save(client, alloc, [(u2, None, a)]).get_data(as_text=True)
    assert "changed by someone else" in page and "Nothing to save." not in page


def test_a_refused_save_writes_nothing(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2, notes="any")
    units = [make_asset(alloc.tv, f"U-000{i}") for i in (1, 2, 3)]
    db.session.commit()
    login(client, "test:admin")
    response = _save(client, alloc, [(u, None, line) for u in units])
    assert response.status_code == 200
    assert "has 2 left, and you picked 3." in response.get_data(as_text=True)
    assert db.session.query(InvAllocation).count() == 0
    assert db.session.query(InvAllocationEvent).count() == 0


def test_a_release_and_a_fill_in_one_save(client, alloc):
    line = supply_line(alloc, alloc.alpha, 1, notes="any")
    u2, u3 = (make_asset(alloc.tv, f"U-000{i}") for i in (2, 3))
    make_allocation(u2, line)
    db.session.commit()
    login(client, "test:admin")
    page = _save(client, alloc, [(u2, line, None), (u3, None, line)]).get_data(as_text=True)
    assert "Saved 2 changes." in page
    assert _held(u2) is None and _held(u3) == line.id


def test_moves_that_free_each_other_apply_in_passes(alloc):
    l1 = supply_line(alloc, alloc.alpha, 1, notes="one")
    l2 = supply_line(alloc, alloc.beta, 1, notes="two")
    l3 = supply_line(alloc, alloc.alpha, 1, notes="three")
    a, b = (make_asset(alloc.tv, f"U-000{i}") for i in (1, 2))
    make_allocation(a, l1)
    make_allocation(b, l2)
    db.session.commit()
    result = save_changes([RowChange(a, l1.id, l2.id), RowChange(b, l2.id, l3.id)], "test:admin")
    assert result.saved == 2 and result.stale == []
    assert current_allocation(a.id).work_line_id == l2.id
    assert current_allocation(b.id).work_line_id == l3.id


def test_operator_cannot_save(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2)
    unit = make_asset(alloc.tv, "U-0001")
    db.session.commit()
    login(client, "test:operator")
    assert _save(client, alloc, [(unit, None, line)]).status_code == 403
    assert db.session.query(InvAllocation).count() == 0


def test_an_unknown_unit_is_a_404(client, alloc):
    login(client, "test:admin")
    response = client.post("/inv/allocation/save",
                           data={"was_99999": "", "to_99999": "", "event": alloc.event.id})
    assert response.status_code == 404


def _post_raw(client, alloc, rows):
    data = {"event": alloc.event.id, "item": alloc.tv.id}
    for unit, was, to in rows:
        data[f"was_{unit.id}"] = was.id if was else ""
        data[f"to_{unit.id}"] = to.id if to else ""
    return client.post("/inv/allocation/save", data=data)


def test_a_refused_fill_undoes_an_earlier_release(client, alloc):
    a = supply_line(alloc, alloc.alpha, 2, notes="any")
    full = supply_line(alloc, alloc.beta, 1, notes="any")
    held, other, x, y = (make_asset(alloc.tv, f"U-000{i}") for i in (1, 2, 3, 4))
    make_allocation(held, a)
    make_allocation(other, full)
    db.session.commit()
    login(client, "test:admin")
    response = _post_raw(client, alloc, [(held, a, None), (x, None, full), (y, None, full)])
    assert response.status_code == 200
    assert _held(held) == a.id and _held(x) is None and _held(y) is None
    assert db.session.query(InvAllocationEvent).count() == 0


def test_a_refused_fill_undoes_an_earlier_move(client, alloc):
    a = supply_line(alloc, alloc.alpha, 2, notes="any")
    b = supply_line(alloc, alloc.beta, 2, notes="any")
    full = supply_line(alloc, alloc.alpha, 1, notes="any")
    mover, other, x, y = (make_asset(alloc.tv, f"U-000{i}") for i in (1, 2, 3, 4))
    make_allocation(mover, a)
    make_allocation(other, full)
    db.session.commit()
    login(client, "test:admin")
    response = _post_raw(client, alloc, [(mover, a, b), (x, None, full), (y, None, full)])
    assert response.status_code == 200
    assert _held(mover) == a.id and _held(x) is None
    assert db.session.query(InvAllocationEvent).count() == 0


def _removed_asset(alloc, label="U-0001"):
    """An asset still held on a line that was deleted."""
    unit = make_asset(alloc.tv, label)
    row = make_allocation(unit, supply_line(alloc, alloc.beta, 1), words="Gone TVs")
    row.work_line_id = None
    return unit


def _save_values(client, alloc, values, follow=True):
    """Post rows given as {unit: (was, to)} with the raw form values."""
    data = {"event": alloc.event.id, "item": alloc.tv.id}
    for unit, (was, to) in values.items():
        data[f"was_{unit.id}"] = was
        data[f"to_{unit.id}"] = to
    return client.post("/inv/allocation/save", data=data, follow_redirects=follow)


def test_a_removed_line_asset_can_be_released(client, alloc):
    unit = _removed_asset(alloc)
    db.session.commit()
    login(client, "test:admin")
    page = _save_values(client, alloc, {unit: ("removed", "")}).get_data(as_text=True)
    assert "Saved 1 change." in page
    assert current_allocation(unit.id) is None
    assert [e.action for e in db.session.query(InvAllocationEvent)] == ["RELEASE"]


def test_a_removed_line_asset_can_move_to_a_live_line(client, alloc):
    unit = _removed_asset(alloc)
    line = supply_line(alloc, alloc.alpha, 2, notes="any")
    db.session.commit()
    login(client, "test:admin")
    page = _save_values(client, alloc, {unit: ("removed", str(line.id))}).get_data(as_text=True)
    assert "Saved 1 change." in page
    assert _held(unit) == line.id
    assert [e.action for e in db.session.query(InvAllocationEvent)] == ["MOVE"]


def test_keeping_a_removed_line_is_nothing_to_save(client, alloc):
    unit = _removed_asset(alloc)
    db.session.commit()
    login(client, "test:admin")
    page = _save_values(client, alloc, {unit: ("removed", "removed")}).get_data(as_text=True)
    assert "Nothing to save." in page
    assert current_allocation(unit.id) is not None
    assert db.session.query(InvAllocationEvent).count() == 0


def test_choosing_removed_for_a_live_allocation_is_refused(client, alloc):
    line = supply_line(alloc, alloc.alpha, 2, notes="any")
    unit = make_asset(alloc.tv, "U-0001")
    make_allocation(unit, line)
    free = make_asset(alloc.tv, "U-0002")
    db.session.commit()
    login(client, "test:admin")
    response = _save_values(client, alloc, {unit: (str(line.id), "removed"),
                                            free: ("", str(line.id))}, follow=False)
    assert response.status_code == 200
    assert "That line is not open for allocation. Reload the page." in response.get_data(as_text=True)
    assert _held(unit) == line.id and _held(free) is None
    assert db.session.query(InvAllocationEvent).count() == 0
