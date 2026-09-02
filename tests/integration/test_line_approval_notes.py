"""Integration tests: the "Approval Notes" column on a finalized budget request.

Departments could not tell which lines carried reviewer reasoning without
opening every line. Once an item is FINALIZED the approval groups have
finished, so the Review Group column gives up its slot to the decision note.
Before FINALIZED the trade runs the other way; both columns never show at once.
"""
import re
from datetime import datetime

from app import db
from app.models import (
    WorkItem, WorkLine, BudgetLineDetail, WorkLineReview, ApprovalGroup,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_FINALIZED, WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_APPROVED, WORK_LINE_STATUS_PENDING,
    REVIEW_STAGE_ADMIN_FINAL, REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
)

DETAIL_URL = "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1"

AG_NOTE = "Approval group thinks two units is plenty for this room."
ADMIN_NOTE = "Trimmed to four units to match the usage we saw last year."
LONG_NOTE = (
    "The requested quantity exceeds what the warehouse can store between "
    "events, so this line is approved at a reduced amount and the balance "
    "should be requested again closer to load-in when the final floor plan "
    "is settled and the storage footprint is known."
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _make_item(data, status=WORK_ITEM_STATUS_FINALIZED, lines=1):
    """Create a work item with `lines` budget lines and no reviews yet."""
    line_status = (
        WORK_LINE_STATUS_APPROVED
        if status == WORK_ITEM_STATUS_FINALIZED
        else WORK_LINE_STATUS_PENDING
    )
    stage = (
        REVIEW_STAGE_ADMIN_FINAL
        if status == WORK_ITEM_STATUS_FINALIZED
        else REVIEW_STAGE_APPROVAL_GROUP
    )

    work_item = WorkItem(
        portfolio_id=data["portfolio"].id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=status,
        public_id="TST2026-TESTDEPT-BUD-1",
        created_by_user_id=data["admin"].id,
    )
    if status == WORK_ITEM_STATUS_FINALIZED:
        work_item.finalized_at = datetime.utcnow()
        work_item.finalized_by_user_id = data["admin"].id
    db.session.add(work_item)
    db.session.flush()

    created = []
    for num in range(1, lines + 1):
        line = WorkLine(
            work_item_id=work_item.id, line_number=num,
            status=line_status, current_review_stage=stage,
            approved_amount_cents=(
                450_00 if line_status == WORK_LINE_STATUS_APPROVED else None
            ),
        )
        db.session.add(line)
        db.session.flush()
        db.session.add(BudgetLineDetail(
            work_line_id=line.id,
            expense_account_id=data["expense_account"].id,
            spend_type_id=data["spend_type"].id,
            quantity=1, unit_price_cents=450_00,
            routed_approval_group_id=data["approval_group"].id,
        ))
        created.append(line)

    db.session.commit()
    return work_item, created


def _add_review(data, line, stage, note, group=None):
    db.session.add(WorkLineReview(
        work_line_id=line.id,
        stage=stage,
        approval_group_id=group.id if group else None,
        status=REVIEW_STATUS_APPROVED,
        approved_amount_cents=450_00,
        note=note,
        decided_at=datetime.utcnow(),
        decided_by_user_id=data["admin"].id,
        created_by_user_id=data["admin"].id,
    ))
    db.session.commit()


CELL = re.compile(r"<t[dh]\b([^>]*)>", re.I)
COLSPAN = re.compile(r'colspan="(\d+)"', re.I)


def _row_width(row_html: str) -> int:
    """Effective column count of one <tr>, honouring colspan."""
    width = 0
    for attrs in CELL.findall(row_html):
        match = COLSPAN.search(attrs)
        width += int(match.group(1)) if match else 1
    return width


def _lines_table(html: str) -> str:
    start = html.index("Budget Lines</h3>")
    table = html.index("<table", start)
    return html[table:html.index("</table>", table)]


def _section_rows(table_html: str, section: str) -> list[str]:
    block = re.search(rf"<{section}>(.*?)</{section}>", table_html, re.S | re.I)
    return re.findall(r"<tr[^>]*>(.*?)</tr>", block.group(1), re.S | re.I)


def _assert_table_aligns(html: str):
    table = _lines_table(html)
    header_width = _row_width(_section_rows(table, "thead")[0])
    for row in _section_rows(table, "tbody") + _section_rows(table, "tfoot"):
        assert _row_width(row) == header_width, (
            f"row width {_row_width(row)} != header {header_width}: {row[:200]}"
        )


# ------------------------------------------------------------------
# Table structure
# ------------------------------------------------------------------

def test_finalized_item_swaps_review_group_for_approval_notes(
    app, client, seed_workflow_data
):
    _make_item(seed_workflow_data, status=WORK_ITEM_STATUS_FINALIZED)
    _login(client, "test:admin")

    html = client.get(DETAIL_URL).get_data(as_text=True)

    assert "Approval Notes" in html
    assert "Review Group" not in html


def test_submitted_item_keeps_review_group_and_hides_notes(
    app, client, seed_workflow_data
):
    _make_item(seed_workflow_data, status=WORK_ITEM_STATUS_SUBMITTED)
    _login(client, "test:admin")

    html = client.get(DETAIL_URL).get_data(as_text=True)

    assert "Review Group" in html
    assert "Approval Notes" not in html


# ------------------------------------------------------------------
# Which note is shown
# ------------------------------------------------------------------

def test_admin_final_note_wins_over_approval_group_note(
    app, client, seed_workflow_data
):
    """The admin decision sets the approved amount, so it owns the column."""
    data = seed_workflow_data
    _, lines = _make_item(data)
    _add_review(data, lines[0], REVIEW_STAGE_APPROVAL_GROUP, AG_NOTE,
                group=data["approval_group"])
    _add_review(data, lines[0], REVIEW_STAGE_ADMIN_FINAL, ADMIN_NOTE)
    _login(client, "test:admin")

    table = _lines_table(client.get(DETAIL_URL).get_data(as_text=True))

    assert ADMIN_NOTE in table
    assert AG_NOTE not in table
    assert "Budget Admin" in table


def test_approval_group_note_shown_when_admin_left_none(
    app, client, seed_workflow_data
):
    data = seed_workflow_data
    _, lines = _make_item(data)
    _add_review(data, lines[0], REVIEW_STAGE_APPROVAL_GROUP, AG_NOTE,
                group=data["approval_group"])
    _add_review(data, lines[0], REVIEW_STAGE_ADMIN_FINAL, None)
    _login(client, "test:admin")

    table = _lines_table(client.get(DETAIL_URL).get_data(as_text=True))

    assert AG_NOTE in table
    # Sourced to the group that wrote it, not to the admin.
    assert "TECH" in table
    assert "Budget Admin" not in table


def test_line_without_any_review_shows_no_note(app, client, seed_workflow_data):
    _make_item(seed_workflow_data)
    _login(client, "test:admin")

    html = client.get(DETAIL_URL).get_data(as_text=True)

    assert "Approval Notes" in html
    assert "Budget Admin" not in _lines_table(html)


def test_long_note_is_truncated_with_a_read_more_link(
    app, client, seed_workflow_data
):
    data = seed_workflow_data
    _, lines = _make_item(data)
    _add_review(data, lines[0], REVIEW_STAGE_ADMIN_FINAL, LONG_NOTE)
    _login(client, "test:admin")

    table = _lines_table(client.get(DETAIL_URL).get_data(as_text=True))

    assert "Read more" in table
    assert LONG_NOTE not in table           # full text stays on the line page
    assert LONG_NOTE[:60] in table          # but the opening is readable


def test_short_note_renders_whole_with_no_read_more(
    app, client, seed_workflow_data
):
    data = seed_workflow_data
    _, lines = _make_item(data)
    _add_review(data, lines[0], REVIEW_STAGE_ADMIN_FINAL, ADMIN_NOTE)
    _login(client, "test:admin")

    table = _lines_table(client.get(DETAIL_URL).get_data(as_text=True))

    assert ADMIN_NOTE in table
    assert "Read more" not in table


# ------------------------------------------------------------------
# Column alignment
# ------------------------------------------------------------------

def test_finalized_table_columns_align(app, client, seed_workflow_data):
    """The footer colspans shift when the notes column replaces Review Group."""
    data = seed_workflow_data
    _, lines = _make_item(data, lines=2)
    _add_review(data, lines[0], REVIEW_STAGE_ADMIN_FINAL, ADMIN_NOTE)
    _login(client, "test:admin")

    _assert_table_aligns(client.get(DETAIL_URL).get_data(as_text=True))


def test_submitted_table_columns_align(app, client, seed_workflow_data):
    data = seed_workflow_data
    hotel = ApprovalGroup(
        work_type_id=data["work_type"].id,
        code="HOTEL", name="Hotel Team", is_active=True,
    )
    db.session.add(hotel)
    db.session.commit()
    _make_item(data, status=WORK_ITEM_STATUS_SUBMITTED, lines=2)
    _login(client, "test:admin")

    _assert_table_aligns(client.get(DETAIL_URL).get_data(as_text=True))


def test_note_markup_is_escaped(app, client, seed_workflow_data):
    """Notes are free text typed by reviewers, not trusted HTML."""
    data = seed_workflow_data
    _, lines = _make_item(data)
    _add_review(data, lines[0], REVIEW_STAGE_ADMIN_FINAL,
                "Cut per <b>policy</b> & the storage cap.")
    _login(client, "test:admin")

    table = _lines_table(client.get(DETAIL_URL).get_data(as_text=True))

    assert "<b>policy</b>" not in table
    assert "&lt;b&gt;policy&lt;/b&gt;" in table
    assert "&amp; the storage cap." in table
