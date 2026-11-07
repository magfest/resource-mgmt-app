"""ZPL for FestOps labels on 2 in x 3 in direct-thermal stock.

The roll is 2 in wide across the printhead and 3 in long. Fields use rotation R,
so a label reads 3 in wide by 2 in tall once turned. ZPL positions are printer
dots, so layout here is in inches and converted per print station: GX430t is
300 dpi, the GX420 series is 203 dpi. Rotated fields run along the Y axis; the
top of the turned label sits at the highest X.
"""
from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_DPI = (203, 300)
SUPPORTED_SIZES = {(2.0, 3.0)}

# Narrow bar width in dots, about 0.01 in at each resolution.
_MODULE_DOTS = {203: 2, 300: 3}
# Code 128 in subset B takes 11 modules a character plus 35 for start, check,
# stop and the final bar. Twenty characters at 3 dots a module is 765 dots; with
# a 10-module quiet zone each side that fits the 840 printable dots at 300 dpi.
# At 203 dpi, 2-dot modules need 550 of 569.
MAX_CODE_LENGTH = 20
_QUIET_MODULES = 10

# A row value past this many characters prints smaller so it still fits one line.
ROW_FULL_CHARS = 22
_NUMBER_FULL_CHARS = 14
_FOOTER_FULL_CHARS = 40
_MAX_FIELD_CHARS = 64

# Layout in inches, measured from the top of the turned label.
_MARGIN = 0.1
_BAR_H = 0.33
_NUMBER_H = 0.2
_ROW_H = 0.15
_ROW_PITCH = 0.2
_FOOTER_H = 0.12
_RULE_H = 0.02
_GAP = 0.05


@dataclass(frozen=True)
class LabelSpec:
    code: str
    rows: tuple[tuple[str, str], ...]
    footer: str | None


def _dots(inches: float, dpi: int) -> int:
    return round(inches * dpi)


def _field(text: str) -> str:
    """Replace ^, ~ and control characters with spaces; cut to 64 characters.

    ZPL reads ^ and ~ as commands even inside field data.
    """
    for ch in ("^", "~", "\r", "\n", "\t"):
        text = text.replace(ch, " ")
    return text[:_MAX_FIELD_CHARS]


def _barcode_data(code: str) -> str:
    """Return the code for the barcode, refusing any value the barcode
    would encode differently.
    """
    if (
        not code
        or len(code) > MAX_CODE_LENGTH
        or any(not 33 <= ord(ch) <= 126 for ch in code)
        or any(ch in code for ch in "^~>")
    ):
        raise ValueError(f"Label code {code!r} cannot be printed as a barcode.")
    return code


def _fitted(height: int, text: str, full_chars: int) -> int:
    """Return the font height that keeps `text` as wide as `full_chars` characters."""
    if len(text) <= full_chars:
        return height
    return max(1, height * full_chars // len(text))


def render_label(spec: LabelSpec, dpi: int, width_in: float = 2.0,
                 height_in: float = 3.0) -> str:
    if dpi not in SUPPORTED_DPI:
        raise ValueError(f"Unsupported dpi {dpi}; expected one of {SUPPORTED_DPI}")
    if (float(width_in), float(height_in)) not in SUPPORTED_SIZES:
        raise ValueError(f"Unsupported label size {width_in} x {height_in} in.")
    code = _barcode_data(spec.code)
    pw, ll = _dots(width_in, dpi), _dots(height_in, dpi)
    margin = _dots(_MARGIN, dpi)
    span = ll - 2 * margin
    module = _MODULE_DOTS[dpi]

    def x_for(top_in: float, band_in: float) -> int:
        # The turned label's top is the highest X; a band's origin is its lower edge.
        return pw - _dots(top_in + band_in, dpi)

    lines = ["^XA", "^CI28", f"^PW{pw}", f"^LL{ll}", "^LH0,0"]

    bar_h = _dots(_BAR_H, dpi)
    bar_width = (11 * len(code) + 35) * module
    bar_y = (ll - bar_width) // 2
    lines.append(f"^FO{x_for(_MARGIN, _BAR_H)},{bar_y}^BY{module},3,{bar_h}"
                 f"^BCR,{bar_h},N,N,N^FD{code}^FS")

    number_top = _MARGIN + _BAR_H + _GAP
    number = _field(code)
    number_h = _fitted(_dots(_NUMBER_H, dpi), number, _NUMBER_FULL_CHARS)
    lines.append(f"^FO{x_for(number_top, _NUMBER_H)},{margin}^A0R,{number_h},{number_h}"
                 f"^FB{span},1,0,C^FD{number}^FS")

    row_h = _dots(_ROW_H, dpi)
    row_top = number_top + _NUMBER_H + _GAP
    for index, (label, value) in enumerate(spec.rows):
        x = x_for(row_top + index * _ROW_PITCH, _ROW_H)
        value = _field(value)
        value_h = _fitted(row_h, value, ROW_FULL_CHARS)
        lines.append(f"^FO{x},{margin}^A0R,{row_h},{row_h}^FD{_field(label)}^FS")
        lines.append(f"^FO{x},{margin}^A0R,{value_h},{value_h}"
                     f"^FB{span},1,0,R^FD{value}^FS")

    if spec.footer:
        footer_top = float(width_in) - _MARGIN - _FOOTER_H
        rule_top = footer_top - _GAP - _RULE_H
        rule = _dots(_RULE_H, dpi)
        footer = _field(spec.footer)
        footer_h = _fitted(_dots(_FOOTER_H, dpi), footer, _FOOTER_FULL_CHARS)
        lines.append(f"^FO{x_for(rule_top, _RULE_H)},{margin}^GB{rule},{span},{rule}^FS")
        lines.append(f"^FO{x_for(footer_top, _FOOTER_H)},{margin}^A0R,{footer_h},{footer_h}"
                     f"^FB{span},1,0,C^FD{footer}^FS")
    lines.append("^XZ")
    return "\n".join(lines)


def render_batch(specs: list[LabelSpec], dpi: int, width_in: float = 2.0,
                 height_in: float = 3.0) -> str:
    return "\n".join(render_label(spec, dpi, width_in, height_in) for spec in specs)
