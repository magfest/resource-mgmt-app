"""TechOps review tables opt into the phone card layout.

The layout itself is CSS in layout/base.html and is checked by eye at 375px.
These pin the markup it depends on, so a later edit cannot silently drop it.
"""
import re

from app.models import ROLE_WORKTYPE_ADMIN

from tests.integration.techops_review_helpers import add_user_with_role, login
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    BASE, DETAIL_URL, ITEM_ID, techops_review,
)


def _stacked(html, label):
    return re.search(rf'<td[^>]*class="[^"]*cell-stack[^"]*"[^>]*data-label="{label}"', html) or \
        re.search(rf'<td[^>]*data-label="{label}"[^>]*class="[^"]*cell-stack', html)


def test_request_page_lines_table_becomes_cards(client, techops_review):
    login(client, "test:reviewer")

    html = client.get(DETAIL_URL).get_data(as_text=True)

    # The lines table specifically: its first header cell is "#".
    assert re.search(r'<table class="responsive-cards">\s*<thead>\s*<tr>\s*<th[^>]*>#</th>', html)
    assert 'data-label="Service"' in html
    assert _stacked(html, "Details")


def test_quick_review_table_becomes_cards(client, techops_review):
    login(client, "test:reviewer")

    html = client.get(f"{BASE}/item/{ITEM_ID}/quick-review").get_data(as_text=True)

    assert re.search(r'<table[^>]*class="responsive-cards"', html)
    assert _stacked(html, "Details")
    assert _stacked(html, "Actions")


def test_all_requests_progress_cell_is_stacked(client, techops_review):
    add_user_with_role("test:techopsadmin", ROLE_WORKTYPE_ADMIN)
    login(client, "test:techopsadmin")

    html = client.get("/admin/techops/requests/").get_data(as_text=True)

    assert _stacked(html, "Progress")


def test_audit_table_becomes_cards(client, techops_review):
    # The audit tab renders for work-type admins; the macro is shared by
    # every work type's request page.
    add_user_with_role("test:techopsadmin", ROLE_WORKTYPE_ADMIN)
    login(client, "test:techopsadmin")

    html = client.get(DETAIL_URL).get_data(as_text=True)

    assert re.search(r'<table class="responsive-cards">\s*<thead>\s*<tr>\s*<th[^>]*>When</th>', html)
    assert 'data-label="Event"' in html
    assert _stacked(html, "Details")
