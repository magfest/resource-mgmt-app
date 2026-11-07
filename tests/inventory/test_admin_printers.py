"""The Printers page, the print queue page, and batches sent through the queue."""
from datetime import datetime

from app import db
from app.inventory.devices import enroll_device
from app.inventory.labels import allocate
from app.inventory.ledger import Actor, add_unit
from app.inventory.printing import render_job
from app.models import InvPrinter, InvPrintJob, InvPrintRoute
from app.models.constants import (
    INV_JOB_CANCELLED, INV_JOB_CONTAINER, INV_JOB_FAILED, INV_JOB_OWNED_UNIT,
    INV_JOB_QUEUED, INV_JOB_SOURCE_BATCH, INV_JOB_SOURCE_SCAN,
)
from tests.inventory.helpers import login, make_container

PRINTERS = "/inv/admin/printers"
QUEUE_POST = "/inv/admin/labels/queue"


def _station(name="Print PC"):
    device, _ = enroll_device(name, "test:invadmin", station_code="PRINT")
    db.session.flush()
    return device


def _printer(name="Zebra A", device=None, **kw):
    printer = InvPrinter(name=name, dpi=kw.get("dpi", 203), width_in=2, height_in=3,
                         device_id=device.id if device else None, device_name=name)
    db.session.add(printer)
    db.session.flush()
    return printer


def _route(job_type, printer):
    db.session.add(InvPrintRoute(job_type=job_type, printer_id=printer.id))
    db.session.flush()


def _form(device, **over):
    data = {"name": "Zebra B", "dpi": "300", "size": "2x3", "stock": "Permanent",
            "device_id": str(device.id), "device_name": "ZDesigner GX430t"}
    data.update(over)
    return data


def test_admin_sets_a_printer_and_routes_and_an_operator_cannot(client, inv):
    device = _station()
    db.session.commit()
    login(client, "test:operator")
    assert client.get(PRINTERS).status_code == 403
    assert client.post(PRINTERS, data=_form(device)).status_code == 403
    login(client, "test:invadmin")
    assert client.post(PRINTERS, data=_form(device)).status_code == 302
    printer = db.session.query(InvPrinter).one()
    assert (printer.name, printer.dpi, float(printer.width_in), float(printer.height_in)) == (
        "Zebra B", 300, 2.0, 3.0)
    assert printer.device_id == device.id and printer.device_name == "ZDesigner GX430t"
    html = client.get(PRINTERS).get_data(as_text=True)
    assert "2 x 3 in" in html and "Copy this from the print station page." in html
    client.post(PRINTERS + "/routes", data={"job_type_CONTAINER": str(printer.id)})
    assert db.session.query(InvPrintRoute).one().job_type == "CONTAINER"
    client.post(PRINTERS + "/routes", data={"job_type_CONTAINER": ""})
    assert db.session.query(InvPrintRoute).count() == 0
    login(client, "test:operator")
    assert client.post(PRINTERS + "/routes", data={}).status_code == 403


def test_an_unsupported_label_size_is_refused(client, inv):
    device = _station()
    db.session.commit()
    login(client, "test:invadmin")
    for over in ({"size": "4x6"}, {"dpi": "600"}, {"size": "abc"}):
        response = client.post(PRINTERS, data=_form(device, **over), follow_redirects=True)
        assert "label size" in response.get_data(as_text=True).lower() or "dpi" in response.get_data(as_text=True).lower()
    assert db.session.query(InvPrinter).count() == 0


def test_sending_a_batch_queues_one_job_per_code_with_one_batch_id(client, inv):
    printer = _printer()
    _route(INV_JOB_CONTAINER, printer)
    for n in (1, 2, 3):
        make_container(inv, f"C-000{n}", name=f"Bin {n}")
    db.session.commit()
    login(client, "test:operator")
    response = client.post(QUEUE_POST, data={"codes": "C-0001\nC-0002\nC-0003"})
    assert response.status_code == 302 and response.headers["Location"].endswith("/inv/admin/print-queue")
    jobs = db.session.query(InvPrintJob).all()
    assert len(jobs) == 3
    assert {j.job_type for j in jobs} == {INV_JOB_CONTAINER}
    assert {j.source for j in jobs} == {INV_JOB_SOURCE_BATCH}
    assert len({j.batch_id for j in jobs}) == 1 and jobs[0].batch_id
    assert all(j.printed_date is None and j.printer_id == printer.id for j in jobs)
    assert all(j.container_id for j in jobs)
    html = client.get("/inv/admin/print-queue").get_data(as_text=True)
    assert "Sent 3 labels to the print queue." in html


def test_a_batch_with_no_route_says_so_and_creates_nothing(client, inv):
    make_container(inv, "C-0001")
    db.session.commit()
    login(client, "test:invadmin")
    response = client.post(QUEUE_POST, data={"codes": "C-0001"})
    assert "No printer is set for this label. Ask an inventory admin." in response.get_data(as_text=True)
    assert db.session.query(InvPrintJob).count() == 0


def test_a_unit_batch_routes_by_ownership_and_dates_the_label(client, inv):
    owned_printer, rented_printer = _printer("Owned"), _printer("Rented")
    _route(INV_JOB_OWNED_UNIT, owned_printer)
    _route("RENTED_UNIT", rented_printer)
    add_unit(inv.laptop, None, Actor(user_id="test:invadmin"), [("FESTOPS_LABEL", "U-00001")])
    unit = add_unit(inv.laptop, None, Actor(user_id="test:invadmin"), [("FESTOPS_LABEL", "U-00002")])
    unit.ownership = "RENTED"
    db.session.commit()
    login(client, "test:invadmin")
    client.post(QUEUE_POST, data={"codes": "U-00001\nU-00002"})
    jobs = {j.code: j for j in db.session.query(InvPrintJob).all()}
    assert jobs["U-00001"].job_type == "OWNED_UNIT" and jobs["U-00001"].printer_id == owned_printer.id
    assert jobs["U-00002"].job_type == "RENTED_UNIT" and jobs["U-00002"].printer_id == rented_printer.id
    assert all(j.printed_date and j.unit_id for j in jobs.values())


def test_a_reserved_batch_queues_codes_with_no_record(client, inv):
    printer = _printer()
    _route(INV_JOB_OWNED_UNIT, printer)
    codes = allocate("U", 2)
    db.session.commit()
    login(client, "test:invadmin")
    client.post(QUEUE_POST, data={"codes": "\n".join(codes)})
    jobs = db.session.query(InvPrintJob).order_by(InvPrintJob.id).all()
    assert [j.code for j in jobs] == codes
    for job in jobs:
        assert job.fields == {"rows": [], "footer": None}
        assert job.printed_date is None and job.unit_id is None
        assert job.job_type == INV_JOB_OWNED_UNIT
        assert f"^FD{job.code}^FS" in render_job(job)


def test_a_never_issued_code_is_named_and_not_queued(client, inv):
    printer = _printer()
    _route(INV_JOB_OWNED_UNIT, printer)
    allocate("U", 1)
    db.session.commit()
    login(client, "test:invadmin")
    response = client.post(QUEUE_POST, data={"codes": "U-00001\nU-00099"}, follow_redirects=True)
    html = response.get_data(as_text=True)
    assert "U-00099" in html and "Sent 1 label to the print queue." in html
    assert [j.code for j in db.session.query(InvPrintJob).all()] == ["U-00001"]
    reprint = client.post("/inv/admin/labels/reprint", data={"codes": "U-00001"}).get_data(as_text=True)
    assert "Not printed" in reprint


def test_an_unprintable_tag_is_named_and_the_rest_are_queued(client, inv):
    printer = _printer()
    _route(INV_JOB_OWNED_UNIT, printer)
    long_tag = "T" * 30
    add_unit(inv.laptop, None, Actor(user_id="test:invadmin"), [("MAGFEST_TAG", long_tag)])
    add_unit(inv.laptop, None, Actor(user_id="test:invadmin"), [("FESTOPS_LABEL", "U-00001")])
    db.session.commit()
    login(client, "test:invadmin")
    html = client.post(QUEUE_POST, data={"codes": f"{long_tag}\nU-00001"},
                       follow_redirects=True).get_data(as_text=True)
    assert long_tag in html
    assert [j.code for j in db.session.query(InvPrintJob).all()] == ["U-00001"]


def test_a_batch_can_be_cancelled_on_the_queue_page(client, inv):
    printer = _printer()
    jobs = [InvPrintJob(printer_id=printer.id, job_type=INV_JOB_CONTAINER, code=f"C-000{n}",
                        fields={"rows": [], "footer": None}, source=INV_JOB_SOURCE_BATCH,
                        status=INV_JOB_QUEUED, batch_id="abc123") for n in (1, 2)]
    db.session.add_all(jobs)
    db.session.commit()
    login(client, "test:operator")
    assert client.post("/inv/admin/print-queue/batch/abc123/cancel").status_code == 403
    login(client, "test:invadmin")
    assert "Zebra A" in client.get("/inv/admin/print-queue").get_data(as_text=True)
    client.post("/inv/admin/print-queue/batch/abc123/cancel")
    assert {j.status for j in db.session.query(InvPrintJob).all()} == {INV_JOB_CANCELLED}
    other = InvPrintJob(printer_id=printer.id, job_type=INV_JOB_CONTAINER, code="C-0009",
                        fields={"rows": [], "footer": None}, source=INV_JOB_SOURCE_BATCH,
                        status=INV_JOB_QUEUED)
    db.session.add(other)
    db.session.commit()
    client.post(f"/inv/admin/print-queue/{other.id}/cancel")
    assert db.session.get(InvPrintJob, other.id).status == INV_JOB_CANCELLED


def test_reprint_from_the_queue_creates_a_new_job_with_the_same_text(client, inv):
    old, new = _printer("Old"), _printer("New")
    _route(INV_JOB_OWNED_UNIT, new)
    unit = add_unit(inv.laptop, None, Actor(user_id="test:invadmin"), [("FESTOPS_LABEL", "U-00001")])
    original = InvPrintJob(
        printer_id=old.id, job_type=INV_JOB_OWNED_UNIT, code="U-00001", unit_id=unit.id,
        fields={"rows": [["Type", "Laptop"]], "footer": "Return"}, printed_date=datetime(2026, 11, 1).date(),
        source=INV_JOB_SOURCE_BATCH, status=INV_JOB_FAILED, error="Jammed")
    db.session.add(original)
    db.session.commit()
    login(client, "test:operator")
    assert client.post(f"/inv/admin/print-queue/{original.id}/reprint").status_code == 302
    jobs = db.session.query(InvPrintJob).order_by(InvPrintJob.id).all()
    assert len(jobs) == 2 and jobs[0].status == INV_JOB_FAILED
    copy = jobs[1]
    assert copy.printer_id == new.id and copy.source == INV_JOB_SOURCE_SCAN
    assert (copy.fields, copy.code, copy.printed_date, copy.unit_id, copy.job_type) == (
        original.fields, "U-00001", original.printed_date, unit.id, INV_JOB_OWNED_UNIT)


def test_reprint_goes_ahead_of_a_waiting_batch(client, inv):
    from app.inventory.printing import collect_jobs
    device = _station()
    printer = _printer(device=device)
    _route(INV_JOB_CONTAINER, printer)
    waiting = InvPrintJob(printer_id=printer.id, job_type=INV_JOB_CONTAINER, code="C-0001",
                          fields={"rows": [], "footer": None}, source=INV_JOB_SOURCE_BATCH,
                          status=INV_JOB_QUEUED)
    failed = InvPrintJob(printer_id=printer.id, job_type=INV_JOB_CONTAINER, code="C-0002",
                         fields={"rows": [], "footer": None}, source=INV_JOB_SOURCE_BATCH,
                         status=INV_JOB_FAILED)
    db.session.add_all([waiting, failed])
    db.session.commit()
    login(client, "test:operator")
    client.post(f"/inv/admin/print-queue/{failed.id}/reprint")
    collected = collect_jobs(device)
    assert [j.code for j in collected][0] == "C-0002"
    assert [j.code for j in collected] == ["C-0002", "C-0001"]


def test_sending_the_same_form_twice_queues_one_batch(client, inv):
    printer = _printer()
    _route(INV_JOB_CONTAINER, printer)
    for n in (1, 2):
        make_container(inv, f"C-000{n}", name=f"Bin {n}")
    db.session.commit()
    login(client, "test:operator")
    page = client.post("/inv/admin/labels/reprint", data={"codes": "C-0001\nC-0002"})
    html = page.get_data(as_text=True)
    assert 'name="send_token"' in html and 'name="dpi"' in html
    token = html.split('name="send_token" value="')[1].split('"')[0]
    form = {"codes": "C-0001\nC-0002", "send_token": token, "dpi": "203"}
    for _ in range(2):
        response = client.post(QUEUE_POST, data=form)
        assert response.status_code == 302
    jobs = db.session.query(InvPrintJob).all()
    assert len(jobs) == 2
    assert {j.client_request_id for j in jobs} == {f"{token}-0", f"{token}-1"}
    client.post(QUEUE_POST, data={"codes": "C-0001\nC-0002"})
    assert db.session.query(InvPrintJob).count() == 4


def test_a_batch_over_the_limit_names_the_overflow(client, inv, monkeypatch):
    monkeypatch.setattr("app.routes.inventory.admin_labels.MAX_BATCH", 2)
    printer = _printer()
    _route(INV_JOB_CONTAINER, printer)
    for n in (1, 2, 3):
        make_container(inv, f"C-000{n}")
    db.session.commit()
    login(client, "test:operator")
    html = client.post("/inv/admin/labels/reprint",
                       data={"codes": "C-0001\nC-0002\nC-0003"}).get_data(as_text=True)
    assert "Not printed, over the limit of 2:" in html and "C-0003" in html
    html = client.post(QUEUE_POST, data={"codes": "C-0001\nC-0002\nC-0003"},
                       follow_redirects=True).get_data(as_text=True)
    assert "Sent 2 labels to the print queue." in html
    assert "over the limit of 2: C-0003" in html


def test_a_route_failure_does_not_repeat_the_skipped_codes_in_a_flash(client, inv):
    make_container(inv, "C-0001")
    db.session.commit()
    login(client, "test:invadmin")
    html = client.post(QUEUE_POST, data={"codes": "C-0001\nC-0999"}).get_data(as_text=True)
    assert html.count("C-0999") == 1


def test_reprinting_from_one_page_twice_makes_one_job(client, inv):
    printer = _printer()
    _route(INV_JOB_CONTAINER, printer)
    old = InvPrintJob(printer_id=printer.id, job_type=INV_JOB_CONTAINER, code="C-0001",
                      fields={"rows": [], "footer": None}, source=INV_JOB_SOURCE_BATCH,
                      status=INV_JOB_FAILED, error="Jammed")
    db.session.add(old)
    db.session.commit()
    login(client, "test:operator")
    html = client.get("/inv/admin/print-queue").get_data(as_text=True)
    token = html.split('name="page_token" value="')[1].split('"')[0]
    url = f"/inv/admin/print-queue/{old.id}/reprint"
    client.post(url, data={"page_token": token})
    client.post(url, data={"page_token": token})
    assert db.session.query(InvPrintJob).count() == 2
    client.post(url, data={"page_token": "another"})
    assert db.session.query(InvPrintJob).count() == 3


def test_reprint_refuses_a_cancelled_job(client, inv):
    printer = _printer()
    _route(INV_JOB_CONTAINER, printer)
    job = InvPrintJob(printer_id=printer.id, job_type=INV_JOB_CONTAINER, code="C-0001",
                      fields={"rows": [], "footer": None}, source=INV_JOB_SOURCE_BATCH,
                      status=INV_JOB_CANCELLED)
    db.session.add(job)
    db.session.commit()
    login(client, "test:operator")
    client.post(f"/inv/admin/print-queue/{job.id}/reprint")
    assert db.session.query(InvPrintJob).count() == 1


def test_a_printer_keeps_a_station_that_is_no_longer_active(client, inv):
    from datetime import datetime
    device = _station()
    printer = _printer(device=device)
    device.revoked_at = datetime.utcnow()
    db.session.commit()
    login(client, "test:invadmin")
    html = client.get(PRINTERS).get_data(as_text=True)
    assert f"{device.name} (not an active print station)" in html
    response = client.post(PRINTERS, data=_form(
        device, printer_id=str(printer.id), name="Renamed", device_name=printer.device_name))
    assert response.status_code == 302
    db.session.expire_all()
    printer = db.session.get(InvPrinter, printer.id)
    assert printer.name == "Renamed" and printer.device_id == device.id


def test_a_station_needs_a_device_name_and_two_printers_cannot_share_one(client, inv):
    device = _station()
    db.session.commit()
    login(client, "test:invadmin")
    html = client.post(PRINTERS, data=_form(device, device_name=""),
                       follow_redirects=True).get_data(as_text=True)
    assert "Enter the device name" in html
    assert db.session.query(InvPrinter).count() == 0
    client.post(PRINTERS, data=_form(device))
    html = client.post(PRINTERS, data=_form(device, name="Zebra C"),
                       follow_redirects=True).get_data(as_text=True)
    assert "already uses that device name" in html
    assert db.session.query(InvPrinter).count() == 1
    client.post(PRINTERS, data=_form(device, name="Zebra D", device_name="Other"))
    assert db.session.query(InvPrinter).count() == 2
