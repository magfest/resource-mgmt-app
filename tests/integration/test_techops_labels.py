"""Screen and spreadsheet read TechOps field labels from one module."""
import pytest

from app import db
from app.models import TechOpsLineDetail, TechOpsServiceType
from app.routes.work.techops import labels
from app.routes.work.techops.line_grain import (
    NETWORK_KINDS, NETWORK_TRAFFIC, PRINTER_CONNECTIVITY, PRINTER_VOLUMES,
    PURPOSES,
)

from tests.integration.techops_review_helpers import login
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    BASE, ITEM_ID, techops_review,
)


def test_every_code_has_a_label():
    assert set(labels.NETWORK_KIND) == set(NETWORK_KINDS)
    assert set(labels.NETWORK_TRAFFIC) == set(NETWORK_TRAFFIC)
    assert set(labels.PHONE_PURPOSE) == set(PURPOSES)
    assert set(labels.PRINTER_CONNECTIVITY) == set(PRINTER_CONNECTIVITY)
    assert set(labels.PRINTER_VOLUME) == set(PRINTER_VOLUMES)


@pytest.mark.parametrize("volume", sorted(labels.PRINTER_VOLUME))
def test_quick_review_reads_printer_labels(client, techops_review, volume):
    detail = db.session.get(TechOpsLineDetail, techops_review["line"].id)
    detail.service_type_id = TechOpsServiceType.query.filter_by(code="PRINTER").one().id
    detail.config = {"connectivity": "USB", "volume": volume}
    db.session.commit()
    login(client, "test:reviewer")

    html = client.get(f"{BASE}/item/{ITEM_ID}/quick-review").get_data(as_text=True)

    assert labels.PRINTER_CONNECTIVITY["USB"] in html
    assert labels.PRINTER_VOLUME[volume] in html
