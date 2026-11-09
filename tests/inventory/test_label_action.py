"""Print and reprint a label from a CT60 scan."""
from datetime import date, datetime

import pytest

from app import db
from app.inventory.devices import enroll_device
from app.inventory.ledger import Actor, add_identifier, replace_number, retire_identifier
from app.inventory.printing import (
    last_job_for, local_today, reprint, reprint_options, request_label,
)
from app.inventory.shifts import start_shift
from app.models import (
    InvPrinter, InvPrintJob, InvPrintRoute, InvUnit, InvUnitIdentifier,
)
from app.models.constants import (
    INV_JOB_CANCELLED, INV_JOB_CONTAINER, INV_JOB_ITEM, INV_JOB_OWNED_UNIT,
    INV_JOB_SOURCE_SCAN,
)
from tests.inventory.helpers import enrolled_client, make_container

BASE = "/inv/scan/api"
OLD = date(2026, 11, 2)
_n = [0]


def _rid():
    _n[0] += 1
    return f"lbl-{_n[0]}"


def _printer(job_types=(INV_JOB_OWNED_UNIT, INV_JOB_CONTAINER, INV_JOB_ITEM)):
    station, _ = enroll_device("Print PC", "test:invadmin", station_code="PRINT")
    station.last_seen_at = datetime.utcnow()
    printer = InvPrinter(name="Perm", dpi=203, width_in=2, height_in=3,
                         device_id=station.id, device_name="Perm")
    db.session.add(printer)
    db.session.flush()
    for job_type in job_types:
        db.session.add(InvPrintRoute(job_type=job_type, printer_id=printer.id))
    db.session.commit()
    return printer


@pytest.fixture
def lab(client, inv):
    _printer()
    device = enrolled_client(client, station_code="LOOKUP")
    start_shift(device, "test:operator")
    db.session.commit()
    return inv


def _receive(client, inv, **kw):
    body = {"client_request_id": _rid(), "item_id": inv.laptop.id}
    body.update(kw)
    answer = client.post(f"{BASE}/receive", json=body).get_json()
    assert answer["ok"] is True, answer
    return answer


def _age(unit_id, printed=OLD):
    """Make the unit's first label look old."""
    job = InvPrintJob.query.filter_by(unit_id=unit_id).order_by(InvPrintJob.id).first()
    job.printed_date = printed
    db.session.commit()
    return job


def _print(client, code, mode, **kw):
    body = {"client_request_id": _rid(), "code": code, "mode": mode}
    body.update(kw)
    return client.post(f"{BASE}/label/print", json=body).get_json()


def _options(client, code, **kw):
    return client.post(f"{BASE}/label/options", json={"code": code, **kw}).get_json()


def test_quick_reprint_copies_the_text_and_keeps_the_original_date(client, lab):
    r = _receive(client, lab, tag="FEST00001", serial="S1")
    first = _age(r["unit_id"])
    answer = _print(client, "S1", "ORIGINAL_DATE")
    assert answer["ok"] is True and answer["code"] == "FEST00001"
    assert answer["station_seen"] is True and "waiting" in answer
    job = db.session.get(InvPrintJob, answer["job_id"])
    assert job.id != first.id and job.fields == first.fields
    assert job.code == "FEST00001" and job.printed_date == OLD
    assert job.unit_id == r["unit_id"] and job.source == "SCAN"


def test_quick_reprint_can_use_todays_date(client, lab):
    r = _receive(client, lab, tag="FEST00001", serial="S1")
    first = _age(r["unit_id"])
    answer = _print(client, "FEST00001", "TODAY")
    job = db.session.get(InvPrintJob, answer["job_id"])
    assert job.printed_date == local_today() and job.fields == first.fields
    assert job.code == "FEST00001"


def test_details_changed_since_the_last_job_is_reported(client, lab):
    r = _receive(client, lab, tag="FEST00001", serial="S1")
    unit = db.session.get(InvUnit, r["unit_id"])
    assert reprint_options(unit)["details_changed"] is False
    retire_identifier(next(i for i in unit.identifiers if i.kind == "SERIAL"))
    add_identifier(unit, "SERIAL", "S2")
    db.session.commit()
    opts = _options(client, "FEST00001")
    assert opts["ok"] is True and opts["has_history"] is True
    assert opts["details_changed"] is True and opts["quick_reprint"] is True
    answer = _print(client, "FEST00001", "CURRENT_DETAILS")
    job = db.session.get(InvPrintJob, answer["job_id"])
    assert ["Serial", "S2"] in job.fields["rows"] and job.printed_date == local_today()


def test_a_changed_number_is_reported_and_the_old_code_is_not_offered(client, lab):
    r = _receive(client, lab, tag="FEST00001", serial="S1")
    unit = db.session.get(InvUnit, r["unit_id"])
    replace_number(unit, "MAGFEST_TAG", "FEST00002", Actor(user_id="test:admin"))
    db.session.commit()
    opts = _options(client, "S1")
    assert opts["details_changed"] is True and opts["quick_reprint"] is False
    before = InvPrintJob.query.count()
    for mode in ("ORIGINAL_DATE", "TODAY"):
        refused = _print(client, "S1", mode)
        assert refused["ok"] is False
    assert InvPrintJob.query.count() == before
    answer = _print(client, "S1", "CURRENT_DETAILS")
    assert answer["ok"] is True and answer["code"] == "FEST00002"
    job = db.session.get(InvPrintJob, answer["job_id"])
    assert job.code == "FEST00002"


def test_a_unit_with_no_history_prints_from_current_data(client, lab):
    from app.inventory.ledger import add_unit
    unit = add_unit(lab.laptop, None, Actor(user_id="test:admin"),
                    [("MAGFEST_TAG", "FEST00009"), ("SERIAL", "S9")])
    db.session.commit()
    opts = _options(client, "FEST00009")
    assert opts["has_history"] is False and opts["quick_reprint"] is False
    assert last_job_for(unit=unit) is None
    assert _print(client, "FEST00009", "ORIGINAL_DATE")["ok"] is False
    answer = _print(client, "FEST00009", "CURRENT_DETAILS")
    job = db.session.get(InvPrintJob, answer["job_id"])
    assert answer["ok"] is True and job.unit_id == unit.id
    assert ["Serial", "S9"] in job.fields["rows"] and job.printed_date == local_today()


def test_cancelled_and_blank_jobs_are_not_history(client, lab):
    r = _receive(client, lab, tag="FEST00001", serial="S1")
    unit = db.session.get(InvUnit, r["unit_id"])
    later = _print(client, "FEST00001", "TODAY")
    db.session.get(InvPrintJob, later["job_id"]).status = INV_JOB_CANCELLED
    db.session.commit()
    assert last_job_for(unit=unit).id == r["job_id"]
    first = db.session.get(InvPrintJob, r["job_id"])
    first.status = INV_JOB_CANCELLED
    db.session.commit()
    assert last_job_for(unit=unit) is None
    request_label(INV_JOB_OWNED_UNIT, "FEST00001", {"rows": [], "footer": None},
                  Actor(user_id="test:admin"), INV_JOB_SOURCE_SCAN, unit=unit)
    assert last_job_for(unit=unit) is None
    assert reprint_options(unit)["has_history"] is False


def test_original_date_survives_a_later_today_reprint(client, lab):
    r = _receive(client, lab, tag="FEST00001", serial="S1")
    _age(r["unit_id"])
    assert _print(client, "FEST00001", "TODAY")["ok"] is True
    unit = db.session.get(InvUnit, r["unit_id"])
    assert reprint_options(unit)["original_date"] == OLD.isoformat()
    again = _print(client, "FEST00001", "ORIGINAL_DATE")
    assert db.session.get(InvPrintJob, again["job_id"]).printed_date == OLD


def test_original_date_ignores_cancelled_and_other_number_jobs(client, lab):
    r = _receive(client, lab, tag="FEST00001", serial="S1")
    first = _age(r["unit_id"])
    first.status = INV_JOB_CANCELLED
    db.session.commit()
    unit = db.session.get(InvUnit, r["unit_id"])
    assert reprint_options(unit)["has_history"] is False
    later = _print(client, "FEST00001", "CURRENT_DETAILS")
    assert reprint_options(unit)["original_date"] == local_today().isoformat()
    assert later["ok"] is True


def test_an_older_job_with_rows_counts_when_the_newest_is_blank(client, lab):
    r = _receive(client, lab, tag="FEST00001", serial="S1")
    unit = db.session.get(InvUnit, r["unit_id"])
    for _ in range(2):
        request_label(INV_JOB_OWNED_UNIT, "FEST00001", {"rows": [], "footer": None},
                      Actor(user_id="test:admin"), INV_JOB_SOURCE_SCAN, unit=unit)
    assert last_job_for(unit=unit).id == r["job_id"]


def test_an_unknown_tag_creates_the_unit_and_prints(client, lab):
    unknown = _options(client, "FEST00077")
    assert unknown["ok"] is True and unknown["kind"] == "UNKNOWN"
    assert unknown["can_create_tag"] is True
    body = {"client_request_id": _rid(), "tag": "FEST00077", "item_id": lab.laptop.id,
            "serial": "SN77"}
    answer = client.post(f"{BASE}/label/new-tag", json=body).get_json()
    assert answer["ok"] is True and answer["code"] == "FEST00077"
    unit = db.session.get(InvUnit, answer["unit_id"])
    assert unit.item_id == lab.laptop.id
    job = InvPrintJob.query.one()
    assert job.code == "FEST00077" and ["Serial", "SN77"] in job.fields["rows"]
    assert client.post(f"{BASE}/label/new-tag", json={**body, "client_request_id": _rid()}
                       ).get_json()["ok"] is False
    assert InvUnit.query.count() == 1


def test_new_tag_refuses_a_code_that_is_not_unknown_or_not_printable(client, lab):
    _receive(client, lab, tag="FEST00001", serial="S1")
    for tag in ("S1", "FEST00001", "U-00001", "bad^tag"):
        body = {"client_request_id": _rid(), "tag": tag, "item_id": lab.laptop.id}
        assert client.post(f"{BASE}/label/new-tag", json=body).get_json()["ok"] is False
    assert InvUnit.query.count() == 1
    assert _options(client, "bad^tag")["ok"] is False


def test_a_missing_route_refuses_and_creates_nothing(client, inv):
    _printer(job_types=(INV_JOB_CONTAINER,))
    device = enrolled_client(client, station_code="LOOKUP")
    start_shift(device, "test:operator")
    db.session.commit()
    body = {"client_request_id": _rid(), "tag": "FEST00005", "item_id": inv.laptop.id}
    answer = client.post(f"{BASE}/label/new-tag", json=body).get_json()
    assert answer["ok"] is False and "No printer" in answer["message"]
    assert InvUnit.query.count() == 0 and InvPrintJob.query.count() == 0


def test_new_tag_refuses_a_sign_in_card_as_the_serial(client, lab):
    body = {"client_request_id": _rid(), "tag": "FEST00078", "item_id": lab.laptop.id,
            "serial": "OP-ABCDEFGHJKMN"}
    answer = client.post(f"{BASE}/label/new-tag", json=body).get_json()
    assert answer == {"ok": False, "message": "That is a sign-in card."}
    assert InvUnit.query.count() == 0 and InvPrintJob.query.count() == 0
    assert InvUnitIdentifier.query.count() == 0


def test_an_unused_label_is_not_a_tag(client, lab):
    from app.inventory import labels
    code = labels.allocate("U", 1)[0]
    db.session.commit()
    answer = _options(client, code)
    assert answer["ok"] is False
    assert answer["message"] == "This label has nothing attached to it yet."
    body = {"client_request_id": _rid(), "tag": code, "item_id": lab.laptop.id}
    assert client.post(f"{BASE}/label/new-tag", json=body).get_json()["ok"] is False
    assert InvUnit.query.count() == 0


def test_a_container_reprints_with_no_date(client, lab):
    box = make_container(lab, "C-0001", name="Dock bin")
    db.session.commit()
    from app.inventory import labels
    labels.allocate("C", 1)
    db.session.commit()
    opts = _options(client, "C-0001")
    assert opts["ok"] is True and opts["kind"] == "CONTAINER"
    answer = _print(client, "C-0001", "CURRENT_DETAILS")
    job = db.session.get(InvPrintJob, answer["job_id"])
    assert answer["ok"] is True and job.printed_date is None
    assert job.container_id == box.id and job.job_type == INV_JOB_CONTAINER
    assert ["Name", "Dock bin"] in job.fields["rows"]
    again = _print(client, "C-0001", "CURRENT_DETAILS")
    assert again["ok"] is True and InvPrintJob.query.count() == 2


def test_an_item_prints_from_current_data_with_no_date(client, lab):
    lab.pens.barcode = "I-0001"
    db.session.commit()
    answer = _print(client, "I-0001", "CURRENT_DETAILS")
    job = db.session.get(InvPrintJob, answer["job_id"])
    assert answer["ok"] is True and job.job_type == INV_JOB_ITEM
    assert job.printed_date is None and job.code == "I-0001"


def test_an_inactive_container_still_prints(client, lab):
    box = make_container(lab, "C-0002")
    box.is_active = False
    db.session.commit()
    assert _options(client, "C-0002")["inactive"] is True
    assert _print(client, "C-0002", "CURRENT_DETAILS")["ok"] is True


def test_an_ambiguous_code_needs_a_matching_unit_id(client, lab):
    a = _receive(client, lab, tag="FEST00001", serial="DUP")
    b = _receive(client, lab, tag="FEST00002", serial="DUP", item_id=lab.wap.id)
    plain = _options(client, "DUP")
    assert plain["ok"] is False
    wrong = _options(client, "DUP", unit_id=a["unit_id"] + b["unit_id"] + 100)
    assert wrong["ok"] is False
    right = _options(client, "DUP", unit_id=b["unit_id"])
    assert right["ok"] is True and right["code"] == "FEST00002"
    answer = _print(client, "DUP", "CURRENT_DETAILS", unit_id=b["unit_id"])
    assert answer["ok"] is True and answer["unit_id"] == b["unit_id"]
    assert _print(client, "DUP", "CURRENT_DETAILS", unit_id=-1)["ok"] is False


def test_a_unit_id_that_names_another_unit_is_refused(client, lab):
    a = _receive(client, lab, tag="FEST00001")
    b = _receive(client, lab, tag="FEST00002")
    assert _options(client, "FEST00001", unit_id=b["unit_id"])["ok"] is False
    assert _options(client, "FEST00001", unit_id=a["unit_id"])["ok"] is True


def test_a_repeated_request_id_makes_one_job(client, lab):
    _receive(client, lab, tag="FEST00001", serial="S1")
    body = {"client_request_id": "once", "code": "FEST00001", "mode": "TODAY"}
    a = client.post(f"{BASE}/label/print", json=body).get_json()
    b = client.post(f"{BASE}/label/print", json=body).get_json()
    assert a == b and InvPrintJob.query.count() == 2


def test_an_unknown_mode_is_refused(client, lab):
    _receive(client, lab, tag="FEST00001")
    assert _print(client, "FEST00001", "YESTERDAY")["ok"] is False
    assert _print(client, "FEST00001", None)["ok"] is False
    assert InvPrintJob.query.count() == 1


def test_a_missing_route_refuses_a_reprint(client, lab):
    _receive(client, lab, tag="FEST00001")
    InvPrintRoute.query.delete()
    db.session.commit()
    answer = _print(client, "FEST00001", "TODAY")
    assert answer["ok"] is False and "No printer" in answer["message"]
    assert InvPrintJob.query.count() == 1


def test_a_retired_unit_is_not_printed(client, lab):
    unit = db.session.get(InvUnit, _receive(client, lab, tag="FEST00001")["unit_id"])
    from app.models.constants import INV_CONDITION_RETIRED
    unit.condition = INV_CONDITION_RETIRED
    db.session.commit()
    from app.inventory.ledger import InventoryError
    with pytest.raises(InventoryError):
        reprint(unit, "CURRENT_DETAILS", Actor(user_id="x"))


def test_a_print_device_is_refused(client, inv):
    _printer()
    enrolled_client(client, station_code="PRINT")
    for path, body in (("label/options", {"code": "X"}),
                       ("label/print", {"code": "X", "mode": "TODAY", "client_request_id": "a"}),
                       ("label/new-tag", {"tag": "X", "item_id": 1, "client_request_id": "b"})):
        assert client.post(f"{BASE}/{path}", json=body).status_code == 403


def test_no_shift_asks_for_a_name_on_a_write(client, inv):
    _printer()
    enrolled_client(client, station_code="LOOKUP")
    body = {"client_request_id": _rid(), "tag": "FEST00001", "item_id": inv.laptop.id}
    answer = client.post(f"{BASE}/label/new-tag", json=body).get_json()
    assert answer["ok"] is False and answer.get("need_name") is True
    assert InvUnit.query.count() == 0
