"""
CSV export utilities for budget reports.

Provides reusable functions for generating CSV downloads from report data.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import List, Any, Callable

from flask import Response


def make_csv_response(
    filename: str,
    headers: List[str],
    rows: List[List[Any]],
) -> Response:
    """
    Generate a CSV file download response.

    Args:
        filename: The filename for the download (without .csv extension)
        headers: List of column header strings
        rows: List of row data (each row is a list of values)

    Returns:
        Flask Response with CSV content and appropriate headers
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # Write headers
    writer.writerow(headers)

    # Write data rows
    for row in rows:
        writer.writerow(row)

    # Create response
    response = Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}.csv"',
            'Content-Type': 'text/csv; charset=utf-8',
        }
    )

    return response


def format_currency_csv(cents: int) -> str:
    """Format cents as currency for CSV (no $ symbol, just number)."""
    return f"{cents / 100:.2f}"


def generate_timestamp_filename(base_name: str, event_code: str = None, dept_code: str = None) -> str:
    """
    Generate a filename with timestamp and optional filters.

    Example: "ledger_report_SMF2027_2024-01-15"
    """
    parts = [base_name]

    if event_code:
        parts.append(event_code.upper())

    if dept_code:
        parts.append(dept_code.upper())

    parts.append(datetime.utcnow().strftime('%Y-%m-%d'))

    return '_'.join(parts)
