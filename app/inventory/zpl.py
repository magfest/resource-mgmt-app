"""ZPL for FestOps labels on 2.25 in × 1.25 in Uline direct-thermal stock.

ZPL positions are printer dots, so a layout written in dots prints at the
wrong size on a printer of the other resolution. Layout here is in inches and
converted per print station: GX430t is 300 dpi, the GX420 series is 203 dpi.
"""
from __future__ import annotations

from dataclasses import dataclass

LABEL_WIDTH_IN = 2.25
LABEL_HEIGHT_IN = 1.25
SUPPORTED_DPI = (203, 300)

# Narrow bar width in dots, about 0.01 in at each resolution. Code 128 for an
# 8-character code then spans about 1.1 in, inside the 2.05 in printable width.
_MODULE_DOTS = {203: 2, 300: 3}


@dataclass(frozen=True)
class LabelSpec:
    code: str
    title: str
    subtitle: str | None


def _dots(inches: float, dpi: int) -> int:
    return round(inches * dpi)


def _field(text: str) -> str:
    """Strip ^ and ~, which ZPL reads as commands even inside field data."""
    return text.replace("^", " ").replace("~", " ")[:40]


def render_label(spec: LabelSpec, dpi: int) -> str:
    if dpi not in SUPPORTED_DPI:
        raise ValueError(f"Unsupported dpi {dpi}; expected one of {SUPPORTED_DPI}")
    margin = _dots(0.1, dpi)
    bar_h = _dots(0.5, dpi)
    title_h = _dots(0.22, dpi)
    sub_h = _dots(0.14, dpi)
    title_y = margin + bar_h + _dots(0.04, dpi)
    lines = [
        "^XA", "^CI28",
        f"^PW{_dots(LABEL_WIDTH_IN, dpi)}", f"^LL{_dots(LABEL_HEIGHT_IN, dpi)}", "^LH0,0",
        f"^FO{margin},{margin}^BY{_MODULE_DOTS[dpi]},3,{bar_h}"
        f"^BCN,{bar_h},N,N,N^FD{_field(spec.code)}^FS",
        f"^FO{margin},{title_y}^A0N,{title_h},{title_h}^FD{_field(spec.title)}^FS",
    ]
    if spec.subtitle:
        sub_y = title_y + title_h + _dots(0.02, dpi)
        lines.append(f"^FO{margin},{sub_y}^A0N,{sub_h},{sub_h}^FD{_field(spec.subtitle)}^FS")
    lines.append("^XZ")
    return "\n".join(lines)


def render_batch(specs: list[LabelSpec], dpi: int) -> str:
    return "\n".join(render_label(spec, dpi) for spec in specs)
