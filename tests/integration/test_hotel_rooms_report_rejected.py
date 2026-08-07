"""Rejected hotel lines stay visible in the breakout but leave the summary.

The summary is a planning number. A rejected room is not a room anyone will
book, so counting it overstates the event.
"""
from app.routes.admin_final.hotel_rooms_report import (
    HotelRoomLineRow, build_hotel_summary, group_hotel_rows,
)


def _row(pay_key="MAGPAID", status="APPROVED", rooms=2, cents=20000, dept="Ops"):
    return HotelRoomLineRow(
        department_name=dept, work_item_id=1, work_item_public_id="TST-1",
        line_number=1, account_code=f"HTL_STD_{pay_key}", room_type="Standard",
        pay_type="MAGFest-paid", pay_type_key=pay_key, rooms=rooms, nights=1,
        room_nights=rooms, unit_price_cents=cents // rooms, total_cents=cents,
        line_status=status,
    )


def test_summary_excludes_rejected_rows():
    rows = [_row(status="APPROVED"), _row(status="REJECTED", rooms=5, cents=50000)]
    summary = build_hotel_summary(rows)
    assert summary["grand_rooms"] == 2
    assert summary["grand_cents"] == 20000


def test_summary_is_empty_when_every_row_is_rejected():
    summary = build_hotel_summary([_row(status="REJECTED")])
    assert summary["grand_rooms"] == 0
    assert summary["matrix_rows"] == []


def test_group_sorts_rejected_last_and_excludes_them_from_subtotals():
    rows = [
        _row(status="REJECTED", dept="Alpha"),
        _row(status="APPROVED", dept="Beta"),
        _row(status="PENDING", dept="Gamma"),
    ]
    groups = group_hotel_rows(rows)
    assert len(groups) == 1
    g = groups[0]
    # Rejected sinks to the bottom; the other two keep their original order.
    assert [r.department_name for r in g["rows"]] == ["Beta", "Gamma", "Alpha"]
    assert g["room_subtotal"] == 4        # 2 + 2, rejected excluded
    assert g["cents_subtotal"] == 40000
    assert g["rejected_count"] == 1
    assert g["rejected_cents"] == 20000
