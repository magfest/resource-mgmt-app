"""The print job queue: routing, collection order, timeouts and cancellation."""
from datetime import datetime, timedelta

import pytest

from app import db
from app.inventory.devices import enroll_device
from app.inventory.ledger import Actor, InventoryError
from app.inventory.printing import (
    CLAIM_TIMEOUT, COLLECT_LIMIT, cancel_batch, cancel_job, collect_jobs,
    printer_for, printer_status, report_job, request_label,
)
from app.models import InvPrinter, InvPrintJob, InvPrintRoute
from app.models.constants import (
    INV_JOB_CANCELLED, INV_JOB_CLAIMED,
    INV_JOB_FAILED, INV_JOB_OWNED_UNIT, INV_JOB_QUEUED, INV_JOB_RENTED_UNIT,
    INV_JOB_SENT, INV_JOB_SOURCE_BATCH, INV_JOB_SOURCE_SCAN, INV_STATION_PRINT,
)

T0 = datetime(2026, 11, 7, 12, 0, 0)
NO_ONE = Actor(user_id="test:invadmin")


def _printer(name, device, job_type=None):
    printer = InvPrinter(name=name, dpi=203, width_in=2, height_in=1,
                         device_id=device.id, device_name=name)
    db.session.add(printer)
    db.session.flush()
    if job_type:
        db.session.add(InvPrintRoute(job_type=job_type, printer_id=printer.id))
        db.session.flush()
    return printer


def _station(name="Print PC"):
    device, _ = enroll_device(name, "test:invadmin", station_code=INV_STATION_PRINT)
    return device


def _ask(source=INV_JOB_SOURCE_SCAN, **kw):
    return request_label(INV_JOB_OWNED_UNIT, kw.pop("code", "FOP-1"), {"name": "Laptop"},
                         NO_ONE, source, **kw)


def test_job_type_chooses_the_printer_and_a_missing_route_is_refused(inv):
    device = _station()
    permanent = _printer("Permanent", device, INV_JOB_OWNED_UNIT)
    job = _ask()
    assert job.printer_id == permanent.id and job.status == INV_JOB_QUEUED
    assert job.performed_by_user_id == "test:invadmin"
    with pytest.raises(InventoryError) as err:
        request_label(INV_JOB_RENTED_UNIT, "FOP-2", {}, NO_ONE, INV_JOB_SOURCE_SCAN)
    assert str(err.value) == "No printer is set for this label. Ask an inventory admin."


def test_an_inactive_printer_counts_as_no_route(inv):
    permanent = _printer("Permanent", _station(), INV_JOB_OWNED_UNIT)
    permanent.is_active = False
    with pytest.raises(InventoryError):
        printer_for(INV_JOB_OWNED_UNIT)


def test_scans_are_collected_ahead_of_an_older_batch(inv):
    device = _station()
    _printer("Permanent", device, INV_JOB_OWNED_UNIT)
    for n in range(3):
        _ask(INV_JOB_SOURCE_BATCH, code=f"B-{n}", batch_id="b1")
    scan = _ask(INV_JOB_SOURCE_SCAN, code="S-1")
    jobs = collect_jobs(device, now=T0)
    assert jobs[0].id == scan.id and len(jobs) == 4
    assert all(j.status == INV_JOB_CLAIMED and j.claimed_at == T0 for j in jobs)


def test_a_station_collects_at_most_ten_and_only_its_own_printers(inv):
    a, b = _station("A"), _station("B")
    _printer("Permanent", a, INV_JOB_OWNED_UNIT)
    _printer("Removable", b, INV_JOB_RENTED_UNIT)
    for n in range(12):
        _ask(code=f"A-{n}")
    other = request_label(INV_JOB_RENTED_UNIT, "R-1", {}, NO_ONE, INV_JOB_SOURCE_SCAN)
    jobs = collect_jobs(a, now=T0)
    assert len(jobs) == COLLECT_LIMIT == 10
    assert other.id not in {j.id for j in jobs}
    assert other.status == INV_JOB_QUEUED


def test_collecting_with_no_printers_still_records_the_visit(inv):
    device = _station()
    assert collect_jobs(device, now=T0) == []
    assert device.last_seen_at == T0


def test_a_claim_with_no_report_fails_after_a_minute_and_is_not_resent(inv):
    device = _station()
    _printer("Permanent", device, INV_JOB_OWNED_UNIT)
    job = _ask()
    assert collect_jobs(device, now=T0) == [job]
    assert collect_jobs(device, now=T0 + CLAIM_TIMEOUT + timedelta(seconds=1)) == []
    assert job.status == INV_JOB_FAILED
    assert job.error == "No report from the print station."
    assert job.finished_at is not None


def test_a_claim_on_a_deactivated_printer_still_fails(inv):
    station = _station()
    printer = _printer("Main", station, INV_JOB_OWNED_UNIT)
    job = _ask()
    collect_jobs(station, now=T0)
    printer.is_active = False
    collect_jobs(station, now=T0 + CLAIM_TIMEOUT + timedelta(seconds=1))
    assert job.status == INV_JOB_FAILED


def test_a_claim_inside_the_timeout_is_left_alone(inv):
    device = _station()
    _printer("Permanent", device, INV_JOB_OWNED_UNIT)
    job = _ask()
    collect_jobs(device, now=T0)
    collect_jobs(device, now=T0 + timedelta(seconds=30))
    assert job.status == INV_JOB_CLAIMED


def test_a_failed_job_keeps_its_error_and_a_sent_job_finishes(inv):
    device = _station()
    _printer("Permanent", device, INV_JOB_OWNED_UNIT)
    bad, good = _ask(code="X-1"), _ask(code="X-2")
    collect_jobs(device, now=T0)
    report_job(bad, False, "Printer offline", now=T0 + timedelta(seconds=2))
    report_job(good, True, now=T0 + timedelta(seconds=3))
    assert (bad.status, bad.error) == (INV_JOB_FAILED, "Printer offline")
    assert good.status == INV_JOB_SENT and good.error is None
    assert good.finished_at == T0 + timedelta(seconds=3)
    # A late report on a finished job changes nothing.
    report_job(good, False, "Late", now=T0 + timedelta(seconds=9))
    assert good.status == INV_JOB_SENT and good.error is None


def test_a_repeated_request_id_creates_no_second_job(inv):
    _printer("Permanent", _station(), INV_JOB_OWNED_UNIT)
    first = _ask(client_request_id="req-1")
    second = _ask(client_request_id="req-1")
    assert first.id == second.id
    assert db.session.query(InvPrintJob).count() == 1


def test_cancelling_a_batch_only_touches_queued_jobs(inv):
    device = _station()
    _printer("Permanent", device, INV_JOB_OWNED_UNIT)
    jobs = [_ask(INV_JOB_SOURCE_BATCH, code=f"B-{n}", batch_id="b9") for n in range(3)]
    jobs[0].status = INV_JOB_CLAIMED
    assert cancel_batch("b9") == 2
    assert jobs[0].status == INV_JOB_CLAIMED
    assert {j.status for j in jobs[1:]} == {INV_JOB_CANCELLED}


def test_cancel_job_refuses_a_job_that_is_not_queued(inv):
    device = _station()
    _printer("Permanent", device, INV_JOB_OWNED_UNIT)
    job = _ask()
    collect_jobs(device, now=T0)
    with pytest.raises(InventoryError):
        cancel_job(job)
    queued = _ask(code="Q-1")
    cancel_job(queued)
    assert queued.status == INV_JOB_CANCELLED and queued.finished_at is not None


def test_status_reports_an_absent_station_and_waiting_jobs(inv):
    device = _station()
    printer = _printer("Permanent", device, INV_JOB_OWNED_UNIT)
    _ask(code="W-1")
    _ask(code="W-2")
    device.last_seen_at = T0 - timedelta(seconds=16)
    assert printer_status(printer, now=T0) == {"station_seen": False, "waiting": 2}
    device.last_seen_at = T0 - timedelta(seconds=5)
    assert printer_status(printer, now=T0)["station_seen"] is True


def _unit(inv, idents, **kw):
    from app.inventory.ledger import add_unit
    unit = add_unit(inv.laptop, None, NO_ONE, idents)
    for key, value in kw.items():
        setattr(unit, key, value)
    db.session.flush()
    return unit


def test_unit_number_prefers_the_active_tag_and_skips_a_retired_one(inv):
    from app.inventory.printing import unit_number
    # Only a rented unit can hold both kinds; an owned unit is limited to one number.
    from app.inventory.ledger import add_identifier
    unit = _unit(inv, [("FESTOPS_LABEL", "U-00001")], ownership="RENTED")
    add_identifier(unit, "MAGFEST_TAG", "FEST00001")
    assert unit_number(unit) == "FEST00001"
    for ident in unit.identifiers:
        if ident.kind == "MAGFEST_TAG":
            ident.retired_at = T0
    assert unit_number(unit) == "U-00001"


def test_unit_fields_leave_out_blanks_and_pick_the_footer(inv):
    from app.inventory.printing import FOOTER_OWNED, FOOTER_RENTED, fields_for_unit
    owned = _unit(inv, [("FESTOPS_LABEL", "U-00001")])
    assert fields_for_unit(owned) == {"rows": [["Type", "Laptop"]], "footer": FOOTER_OWNED}
    rented = _unit(inv, [("FESTOPS_LABEL", "R-00038"), ("VENDOR_TAG", "A0038"),
                         ("SERIAL", "SN1")], ownership="RENTED", vendor_name="Vendor A")
    fields = fields_for_unit(rented)
    assert fields["rows"] == [["Type", "Laptop"], ["Vendor tag", "Vendor A: A0038"],
                              ["Serial", "SN1"]]
    assert fields["footer"] == FOOTER_RENTED
    rented.vendor_name = None
    assert ["Vendor tag", "A0038"] in fields_for_unit(rented)["rows"]


def test_container_and_item_fields_carry_no_footer(inv):
    from app.inventory.printing import fields_for_container, fields_for_item
    from tests.inventory.helpers import make_container
    named = make_container(inv, "C-0001", name="Main")
    plain = make_container(inv, "C-0002")
    assert fields_for_container(named) == {"rows": [["Kind", "Pallet"], ["Name", "Main"]],
                                           "footer": None}
    assert fields_for_container(plain)["rows"] == [["Kind", "Pallet"]]
    assert fields_for_item(inv.laptop) == {"rows": [["Type", "Laptop"]], "footer": None}


def test_render_job_adds_the_printed_date_row_only_when_the_job_has_one(inv):
    from datetime import date
    from app.inventory.printing import render_job
    printer = _printer("Big", _station(), INV_JOB_OWNED_UNIT)
    printer.height_in, printer.width_in, printer.dpi = 3, 2, 300
    db.session.flush()
    fields = {"rows": [["Type", "Laptop"]], "footer": None}
    dated = request_label(INV_JOB_OWNED_UNIT, "FEST00001", fields, NO_ONE,
                          INV_JOB_SOURCE_SCAN, printed_date=date(2026, 11, 7))
    undated = request_label(INV_JOB_OWNED_UNIT, "FEST00002", fields, NO_ONE,
                            INV_JOB_SOURCE_SCAN)
    assert "^FD2026-11-07^FS" in render_job(dated) and "^PW600" in render_job(dated)
    assert "Printed" not in render_job(undated)


def test_local_today_is_a_date():
    from datetime import date
    from app.inventory.printing import local_today
    assert isinstance(local_today(), date)


def _gear_unit(inv, name=None):
    from app.inventory.names import set_unit_name
    from app.inventory.unit_fields import set_unit_value
    from tests.inventory.helpers import seed_gear_kind
    kind = seed_gear_kind("TV", "TV", [("BRAND", "Brand", "TEXT", None)])
    inv.laptop.gear_kind_id = kind.id
    db.session.flush()
    db.session.refresh(inv.laptop)
    unit = _unit(inv, [("FESTOPS_LABEL", "U-00001")])
    set_unit_value(unit, kind.fields[0], "Sony")
    if name:
        set_unit_name(unit, name)
    return unit


def test_unit_fields_carry_the_name_and_details(inv):
    from app.inventory.printing import fields_for_unit
    fields = fields_for_unit(_gear_unit(inv, "MAG-TV-001"))
    assert fields["title"] == "MAG-TV-001"
    assert fields["rows"] == [["Type", "Laptop"], ["Details", "Sony"]]


def test_unit_fields_for_an_unnamed_unit_have_no_title_key(inv):
    from app.inventory.printing import fields_for_unit
    assert "title" not in fields_for_unit(_gear_unit(inv))


def test_renaming_a_unit_counts_as_changed_details(inv):
    from app.inventory.names import set_unit_name
    from app.inventory.printing import (
        INV_JOB_SOURCE_SCAN, fields_for_unit, reprint_options,
    )
    from datetime import date
    unit = _gear_unit(inv, "MAG-TV-001")
    device = _station()
    _printer("Zebra", device, INV_JOB_OWNED_UNIT)
    request_label(INV_JOB_OWNED_UNIT, "U-00001", fields_for_unit(unit), NO_ONE,
                  INV_JOB_SOURCE_SCAN, printed_date=date(2026, 11, 7), unit=unit)
    assert reprint_options(unit)["details_changed"] is False
    set_unit_name(unit, "MAG-TV-002")
    db.session.flush()
    assert reprint_options(unit)["details_changed"] is True


def test_render_job_prints_the_title(inv):
    from app.inventory.printing import fields_for_unit, render_job
    unit = _gear_unit(inv, "MAG-TV-001")
    _printer("Zebra", _station(), INV_JOB_OWNED_UNIT).height_in = 3
    job = request_label(INV_JOB_OWNED_UNIT, "U-00001", fields_for_unit(unit), NO_ONE,
                        INV_JOB_SOURCE_SCAN)
    assert "^FDMAG-TV-001^FS" in render_job(job)
