"""Supplies on hand page: the count sheet a lead reads on Friday of warehouse week."""
from app.inventory.csv_export import csv_response
from app.inventory.printing import local_iso, local_stamp, local_today
from app.inventory.stock_report import supplies_on_hand
from flask import request

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_operator


def _place_words(places) -> str:
    return "; ".join(f"{name}: {'about ' if approx else ''}{qty}" for name, qty, approx in places)


@inventory_bp.get("/admin/supplies")
@require_inventory_operator
def supplies():
    rows = supplies_on_hand()
    if request.args.get("format") == "csv":
        return csv_response(
            f"supplies-{local_today():%Y-%m-%d}.csv",
            ["Item", "Unit", "Total", "Exact", "Last counted", "Where"],
            [[r["name"], r["unit"], r["total"], "no" if r["approximate"] else "yes",
              local_iso(r["last_counted"]) if r["last_counted"] else "",
              _place_words(r["places"])] for r in rows])
    for r in rows:
        r["counted_text"] = local_stamp(r["last_counted"]) if r["last_counted"] else ""
        r["where"] = _place_words(r["places"])
    return render_inventory_page("inventory/supplies.html", rows=rows)
