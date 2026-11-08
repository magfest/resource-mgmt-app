"""CSV downloads for inventory lists."""
import csv
import io

from flask import Response

# Spreadsheet apps run a cell that starts with one of these as a formula.
_FORMULA_STARTS = ("=", "+", "-", "@")


def _safe(cell):
    text = "" if cell is None else str(cell)
    return "'" + text if text.startswith(_FORMULA_STARTS) else text


def csv_response(filename: str, header: list[str], rows: list[list]) -> Response:
    """Return the rows as a CSV attachment, with formula-looking cells neutralised."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows([_safe(c) for c in row] for row in rows)
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
