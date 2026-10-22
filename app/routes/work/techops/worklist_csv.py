"""The TechOps worklist as a CSV for Google Sheets.

Security boundary: requesters type the text fields, and Sheets runs a cell
that starts with a formula character. csv_cell() prefixes every such cell
with an apostrophe; route every value through it, including new columns.
"""
from __future__ import annotations

import csv
import io

from . import labels
from .line_grain import (
    SERVICE_DESK_PHONE, SERVICE_ETHERNET, SERVICE_PAYMENTS, SERVICE_PHONE_NUMBER,
    SERVICE_PRINTER,
)

CSV_HEADERS = [
    "Event", "Request", "Line", "Link",
    "Department", "Room", "Contact name", "Contact email",
    "Service", "Team", "Status",
    "Location / channel", "What it's for", "Description",
    "Phone purpose", "Internal only", "Rings line",
    "Printer connection", "Printer volume",
    "Wired kinds", "Rough drop count",
    "Checkout points", "Accepts cash",
    "Open questions",
]

# OWASP's CSV injection list: the four formula starters plus tab and CR.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


def _yes_no(value) -> str:
    return "Yes" if value else "No"


def csv_record(row, link: str) -> list[str]:
    detail = row.detail
    code = detail.service_type.code
    config = detail.config or {}
    contact = row.work_item.techops_detail
    questions = config.get("open_questions") or []
    values = {
        "Event": row.work_item.portfolio.event_cycle.code,
        "Request": row.work_item.public_id,
        "Line": row.line.line_number,
        "Link": link,
        "Department": row.department.name,
        "Room": row.room_name,
        "Contact name": contact.primary_contact_name if contact else "",
        "Contact email": contact.primary_contact_email if contact else "",
        "Service": detail.service_type.name,
        "Team": row.team.name,
        "Status": row.status_label,
        "Location / channel": detail.location,
        "What it's for": detail.usage,
        "Description": detail.description,
        "Open questions": len(questions) if questions else "",
    }
    if code == SERVICE_PHONE_NUMBER:
        values["Phone purpose"] = labels.PHONE_PURPOSE.get(detail.purpose, "")
        values["Internal only"] = _yes_no(detail.internal_only)
    if code == SERVICE_DESK_PHONE and detail.parent_line is not None:
        values["Rings line"] = detail.parent_line.line_number
    if code == SERVICE_PRINTER:
        values["Printer connection"] = labels.PRINTER_CONNECTIVITY.get(config.get("connectivity"), "")
        values["Printer volume"] = labels.PRINTER_VOLUME.get(config.get("volume"), "")
    if code == SERVICE_ETHERNET:
        values["Wired kinds"] = ", ".join(
            labels.NETWORK_KIND.get(kind, kind) for kind in config.get("kinds") or [])
        values["Rough drop count"] = config.get("rough_count")
    if code == SERVICE_PAYMENTS:
        values["Checkout points"] = detail.quantity
        values["Accepts cash"] = _yes_no(config.get("accepts_cash"))
    return [csv_cell(values.get(header)) for header in CSV_HEADERS]


def render_csv(rows, link_for) -> str:
    """Write rows in their given order; link_for(row) returns the absolute line URL."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_HEADERS)
    for row in rows:
        writer.writerow(csv_record(row, link_for(row)))
    return out.getvalue()
