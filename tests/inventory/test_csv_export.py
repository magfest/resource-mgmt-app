"""CSV export guards against spreadsheet formula injection."""
from app.inventory.csv_export import csv_response


def test_csv_guard_covers_tab_and_carriage_return():
    body = csv_response("x.csv", ["a"], [["\tx"], ["\rx"], ["=1"], [None]]).get_data(as_text=True)
    assert "'\tx" in body and "'\rx" in body and "'=1" in body
