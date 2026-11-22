"""The print station page and its device API."""
from datetime import datetime, timedelta

from app import db
from app.inventory.devices import enroll_device
from app.models import InvPrinter, InvPrintJob
from app.models.constants import (
    INV_JOB_CLAIMED, INV_JOB_FAILED, INV_JOB_OWNED_UNIT, INV_JOB_QUEUED,
    INV_JOB_SENT, INV_JOB_SIGNIN_CARD, INV_JOB_SOURCE_SCAN,
)
from tests.inventory.helpers import enrolled_client, login

COLLECT = "/inv/scan/print/api/collect"
REPORT = "/inv/scan/print/api/report"
JSON = {"content_type": "application/json"}


def _printer(device, name="Zebra A", dpi=203):
    printer = InvPrinter(name=name, dpi=dpi, width_in=2, height_in=3,
                         device_id=device.id, device_name=name)
    db.session.add(printer)
    db.session.flush()
    return printer


def _job(printer, code="U-00001", status=INV_JOB_QUEUED):
    job = InvPrintJob(printer_id=printer.id, job_type=INV_JOB_OWNED_UNIT, code=code,
                      fields={"rows": [["Type", "Laptop"]], "footer": None},
                      source=INV_JOB_SOURCE_SCAN, status=status)
    db.session.add(job)
    db.session.commit()
    return job


def test_only_a_print_device_can_collect(client, inv):
    enrolled_client(client, station_code="COUNT")
    assert client.post(COLLECT, json={}).status_code == 403
    client.delete_cookie("inv_device", path="/inv/scan/")
    login(client, "test:invadmin")
    response = client.post(COLLECT, json={})
    assert response.status_code == 403
    assert response.is_json


def test_the_page_refuses_a_non_print_device_without_a_login_redirect(client, inv):
    enrolled_client(client, station_code="COUNT")
    response = client.get("/inv/scan/print/station")
    assert response.status_code == 403
    assert "Location" not in response.headers


def test_collect_returns_zpl_for_the_printers_dpi_and_size(client, inv):
    device = enrolled_client(client, station_code="PRINT")
    printer = _printer(device)
    job = _job(printer)
    body = client.post(COLLECT, json={}).get_json()
    assert body["ok"] is True
    assert [j["id"] for j in body["jobs"]] == [job.id]
    assert body["jobs"][0]["device_name"] == "Zebra A"
    assert "^PW406" in body["jobs"][0]["zpl"]
    assert body["printers"][0]["name"] == "Zebra A"
    assert db.session.get(InvPrintJob, job.id).status == INV_JOB_CLAIMED


def test_collect_does_not_create_a_user_session(app, client, inv):
    enrolled_client(client, station_code="PRINT")
    for path, method in ((COLLECT, client.post), ("/inv/scan/print/station", client.get)):
        response = method(path, **({"json": {}} if method == client.post else {}))
        for cookie in response.headers.getlist("Set-Cookie"):
            if cookie.startswith("session="):
                raw = cookie.split(";")[0].split("=", 1)[1]
                data = app.session_interface.get_signing_serializer(app).loads(raw)
                assert "active_user_id" not in data


def test_printers_report_last_job_and_recent_failures(client, inv):
    device = enrolled_client(client, station_code="PRINT")
    printer = _printer(device)
    failed = _job(printer, "U-00002", INV_JOB_FAILED)
    failed.finished_at = datetime.utcnow() - timedelta(minutes=5)
    old = _job(printer, "U-00003", INV_JOB_FAILED)
    old.finished_at = datetime.utcnow() - timedelta(hours=3)
    db.session.commit()
    row = client.post(COLLECT, json={}).get_json()["printers"][0]
    assert row["failed"] == 1
    assert row["last_job"]["status"] == INV_JOB_FAILED


def test_printer_status_names_a_card_job_without_its_value(client, inv):
    device = enrolled_client(client, station_code="PRINT")
    printer = _printer(device)
    db.session.add(InvPrintJob(printer_id=printer.id, job_type=INV_JOB_SIGNIN_CARD,
                               code="OP-ABCDEFGHJKMN", fields={"name": "Jamie Lee"},
                               source=INV_JOB_SOURCE_SCAN, status=INV_JOB_QUEUED))
    db.session.commit()
    body = client.post(COLLECT, json={}).get_json()
    assert body["printers"][0]["last_job"]["code"] == "Sign-in card"
    assert "OP-ABCDEFGHJKMN" not in str(body["printers"])


def test_report_finishes_only_this_stations_job(client, inv):
    device = enrolled_client(client, station_code="PRINT")
    other, _ = enroll_device("Other PC", "test:invadmin", station_code="PRINT")
    mine = _job(_printer(device), status=INV_JOB_CLAIMED)
    theirs = _job(_printer(other, "Zebra B"), "U-00009", INV_JOB_CLAIMED)
    assert client.post(REPORT, json={"job_id": theirs.id, "ok": True}).status_code == 409
    assert db.session.get(InvPrintJob, theirs.id).status == INV_JOB_CLAIMED
    body = client.post(REPORT, json={"job_id": mine.id, "ok": True}).get_json()
    assert body["ok"] is True
    assert db.session.get(InvPrintJob, mine.id).status == INV_JOB_SENT


def test_a_job_that_cannot_render_fails_and_the_rest_go_out(client, inv):
    device = enrolled_client(client, station_code="PRINT")
    printer = _printer(device)
    bad = _job(printer, "badéé")
    good = _job(printer, "U-00001")
    body = client.post(COLLECT, json={}).get_json()
    assert [j["id"] for j in body["jobs"]] == [good.id]
    bad = db.session.get(InvPrintJob, bad.id)
    assert bad.status == INV_JOB_FAILED
    assert bad.error == "Label code 'badéé' cannot be printed as a barcode."


def test_a_job_that_fails_to_render_for_another_reason_does_not_stop_the_rest(client, inv):
    device = enrolled_client(client, station_code="PRINT")
    printer = _printer(device)
    broken = _job(printer, "U-00002")
    broken.fields = {"rows": [None], "footer": None}
    good = _job(printer, "U-00001")
    body = client.post(COLLECT, json={}).get_json()
    assert [j["id"] for j in body["jobs"]] == [good.id]
    broken = db.session.get(InvPrintJob, broken.id)
    assert broken.status == INV_JOB_FAILED
    assert broken.error == "This label could not be rendered."


def test_collect_skips_printers_still_sending(client, inv):
    device = enrolled_client(client, station_code="PRINT")
    busy, free = _printer(device, "Zebra A"), _printer(device, "Zebra B")
    held = _job(busy, "U-00001")
    taken = _job(free, "U-00002")
    body = client.post(COLLECT, json={"skip_printer_ids": [busy.id]}).get_json()
    assert [j["id"] for j in body["jobs"]] == [taken.id]
    assert db.session.get(InvPrintJob, held.id).status == INV_JOB_QUEUED


def test_collect_ignores_skip_values_that_are_not_ints(client, inv):
    device = enrolled_client(client, station_code="PRINT")
    job = _job(_printer(device))
    for junk in ("x", {"a": 1}, [True, "1", None, 1.5, [2]], 7):
        body = client.post(COLLECT, json={"skip_printer_ids": junk}).get_json()
        assert body["ok"] is True
        client.post(REPORT, json={"job_id": job.id, "ok": True})
        db.session.get(InvPrintJob, job.id).status = INV_JOB_QUEUED
        db.session.commit()
    assert client.post(COLLECT, json=[1, 2]).get_json()["ok"] is True


def test_the_stale_sweep_covers_a_skipped_printer(client, inv):
    device = enrolled_client(client, station_code="PRINT")
    printer = _printer(device)
    stale = _job(printer, "U-00001", INV_JOB_CLAIMED)
    stale.claimed_at = datetime.utcnow() - timedelta(minutes=5)
    db.session.commit()
    client.post(COLLECT, json={"skip_printer_ids": [printer.id]})
    assert db.session.get(InvPrintJob, stale.id).status == INV_JOB_FAILED


def test_report_refuses_bad_input_with_409_and_changes_nothing(client, inv):
    device = enrolled_client(client, station_code="PRINT")
    job = _job(_printer(device), status=INV_JOB_CLAIMED)
    bodies = [[1], "x", {}, {"job_id": True, "ok": True}, {"job_id": 0, "ok": True},
              {"job_id": 2**63, "ok": True}, {"job_id": str(job.id), "ok": True},
              {"job_id": job.id + 1000, "ok": True}]
    for body in bodies:
        response = client.post(REPORT, json=body)
        assert response.status_code == 409
        assert response.get_json()["ok"] is False
    assert client.post(REPORT, data="null", **JSON).status_code == 409
    assert db.session.get(InvPrintJob, job.id).status == INV_JOB_CLAIMED


def test_report_counts_ok_only_when_it_is_true(client, inv):
    device = enrolled_client(client, station_code="PRINT")
    job = _job(_printer(device), status=INV_JOB_CLAIMED)
    assert client.post(REPORT, json={"job_id": job.id, "ok": "false"}).status_code == 200
    assert db.session.get(InvPrintJob, job.id).status == INV_JOB_FAILED


def test_station_polling_does_not_renew_a_user_session(client, inv):
    enrolled_client(client, station_code="PRINT")
    login(client, "test:invadmin")
    response = client.post(COLLECT, json={})
    assert response.status_code == 200
    assert not [c for c in response.headers.getlist("Set-Cookie") if c.startswith("session=")]


def test_an_ordinary_page_still_renews_the_session(client, inv):
    login(client, "test:invadmin")
    response = client.get("/inv/admin/kinds")
    assert [c for c in response.headers.getlist("Set-Cookie") if c.startswith("session=")]


def test_collect_needs_a_json_content_type(client, inv):
    device = enrolled_client(client, station_code="PRINT")
    job = _job(_printer(device))
    assert client.post(COLLECT, data={"a": "b"}).status_code == 415
    assert db.session.get(InvPrintJob, job.id).status == INV_JOB_QUEUED


def test_collect_works_with_form_tokens_on(app, client, inv):
    enrolled_client(client, station_code="PRINT")
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert client.post(COLLECT, json={}).status_code == 200
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_a_failed_collect_answers_json(client, inv, monkeypatch):
    enrolled_client(client, station_code="PRINT")

    def boom(device):
        raise RuntimeError("db down")
    monkeypatch.setattr("app.routes.inventory.print_station.collect_jobs", boom)
    response = client.post(COLLECT, json={})
    assert response.status_code == 500
    assert response.get_json()["ok"] is False


def test_only_this_page_may_reach_browser_print_besides_the_batch_page(client, inv):
    enrolled_client(client, station_code="PRINT")
    csp = client.get("/inv/scan/print/station").headers["Content-Security-Policy"]
    assert "connect-src 'self' http://localhost:9100" in csp
    assert "9101" not in csp
    login(client, "test:invadmin")
    client.delete_cookie("inv_device", path="/inv/scan/")
    assert "connect-src" not in client.get("/inv/admin/kinds").headers["Content-Security-Policy"]


def test_a_print_device_lands_on_the_station_page(client, inv):
    enrolled_client(client, station_code="PRINT")
    response = client.get("/inv/scan/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/inv/scan/print/station")
