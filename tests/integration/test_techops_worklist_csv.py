"""The worklist CSV: columns, full text, contacts and the formula boundary."""
import csv
import io

from app import db
from app.models import TechOpsLineDetail, TechOpsRequestDetail, TechOpsServiceType
from app.routes.work.techops import labels
from app.routes.work.techops.worklist_csv import CSV_HEADERS

from tests.integration.techops_review_helpers import login
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    ITEM_ID, REVIEW_URL, techops_review,
)

CSV_URL = "/admin/techops/worklist.csv"


def _download(client, query=""):
    resp = client.get(CSV_URL + query)
    text = resp.get_data(as_text=True).lstrip("\ufeff")
    rows = list(csv.reader(io.StringIO(text)))
    return resp, rows


def _first_record(client):
    _, rows = _download(client)
    return dict(zip(rows[0], rows[1]))


def _detail(techops_review):
    return db.session.get(TechOpsLineDetail, techops_review["line"].id)


def test_headers_identity_and_contact(client, techops_review):
    db.session.add(TechOpsRequestDetail(
        work_item_id=techops_review["item"].id, primary_contact_name="Pat Lee",
        primary_contact_email="pat@test.local"))
    db.session.commit()
    login(client, "test:reviewer")

    resp, rows = _download(client)
    record = dict(zip(rows[0], rows[1]))

    assert resp.mimetype == "text/csv"
    assert 'filename="techops-worklist-SMF2027-' in resp.headers["Content-Disposition"]
    assert rows[0] == CSV_HEADERS
    assert (record["Request"], record["Line"], record["Status"], record["Room"]) \
        == (ITEM_ID, "1", "Pending", "Department-wide")
    assert record["Link"].endswith(REVIEW_URL)
    assert (record["Contact name"], record["Contact email"]) == ("Pat Lee", "pat@test.local")


def test_a_request_without_contact_details_writes_blank_cells(client, techops_review):
    login(client, "test:reviewer")

    record = _first_record(client)

    assert (record["Contact name"], record["Contact email"]) == ("", "")


def test_formula_cells_are_prefixed_and_text_is_not_cut(client, techops_review):
    detail = _detail(techops_review)
    long_text = "x" * 300
    detail.description = '=HYPERLINK("http://example.test","x")'
    detail.usage = "\t+1 tab first"
    detail.location = long_text
    db.session.commit()
    login(client, "test:reviewer")

    record = _first_record(client)

    assert record["Description"].startswith("'=")
    assert record["What it's for"].startswith("'\t")
    assert record["Location / channel"] == long_text


def test_printer_cells_use_the_shared_labels(client, techops_review):
    detail = _detail(techops_review)
    detail.service_type_id = TechOpsServiceType.query.filter_by(code="PRINTER").one().id
    detail.config = {"connectivity": "USB", "volume": "OVER_500"}
    db.session.commit()
    login(client, "test:reviewer")

    record = _first_record(client)

    assert record["Printer connection"] == labels.PRINTER_CONNECTIVITY["USB"]
    assert record["Printer volume"] == labels.PRINTER_VOLUME["OVER_500"]
    assert record["Phone purpose"] == ""


def test_csv_is_refused_to_a_requester(client, techops_review):
    login(client, "test:author")

    assert client.get(CSV_URL).status_code == 403
