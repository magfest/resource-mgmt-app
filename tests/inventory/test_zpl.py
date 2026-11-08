import re

import pytest

from app.inventory import zpl

from app.inventory.zpl import MAX_CODE_LENGTH, LabelSpec, render_batch, render_label

SERIAL_22 = "PF3XK9R2MJ0K8LQ3N7Y2AB"


def _value_height(zpl, value):
    return int(re.search(rf"\^A0R,(\d+),\d+\^FB\d+,1,0,R\^FD{re.escape(value)}\^FS", zpl).group(1))


def test_label_is_3_wide_by_2_tall_on_a_2_in_roll():
    z = render_label(LabelSpec("FEST00001", (("Type", "Laptop"),), "Return to MAGFest FestOps"), 300)
    assert "^PW600" in z and "^LL900" in z
    assert "^PW406" in render_label(LabelSpec("U-00001", (), None), 203)
    assert "^LL609" in render_label(LabelSpec("U-00001", (), None), 203)
    assert "^BY3," in z and "^BY2," in render_label(LabelSpec("U-00001", (), None), 203)


def test_the_fullest_label_stays_inside_the_printable_area():
    spec = LabelSpec("R-00038", (("Type", "Laptop"), ("Details", "55 in, 4k, HDMI"),
                                 ("Vendor tag", "Vendor A: A0038"),
                                 ("Serial", SERIAL_22), ("Printed", "2026-11-07")),
                     "Rented unit. Return to MAGFest FestOps", "MAG-PNTR-007")
    for dpi, pw, ll in ((300, 600, 900), (203, 406, 609)):
        for line in render_label(spec, dpi).splitlines():
            origin = re.match(r"\^FO(\d+),(\d+)", line)
            if not origin:
                continue
            x, y = int(origin.group(1)), int(origin.group(2))
            assert 0 <= x < pw and 0 <= y < ll, line


def test_the_barcode_is_centred_along_the_length():
    z = render_label(LabelSpec("R-00038", (), None), 300)
    y = int(re.search(r"\^FO\d+,(\d+)\^BY", z).group(1))
    assert y == (900 - (11 * 7 + 35) * 3) // 2


def test_a_22_character_serial_prints_whole_at_the_normal_size():
    z = render_label(LabelSpec("R-00038", (("Serial", SERIAL_22),), None), 300)
    assert "^FD" + SERIAL_22 + "^FS" in z
    assert _value_height(z, SERIAL_22) == 45


@pytest.mark.parametrize("dpi", [300, 203])
def test_a_longer_serial_is_smaller_and_never_cut(dpi):
    long_serial = "PF3XK9R2MJ0K8LQ3N7Y2ABCDEFGH12"
    assert len(long_serial) == 30
    normal = render_label(LabelSpec("R-00038", (("Serial", SERIAL_22),), None), dpi)
    long = render_label(LabelSpec("R-00038", (("Serial", long_serial),), None), dpi)
    assert "^FD" + long_serial + "^FS" in long
    assert _value_height(long, long_serial) < _value_height(normal, SERIAL_22)


def test_a_row_without_a_footer_prints_no_rule():
    assert "^GB" not in render_label(LabelSpec("C-0001", (("Kind", "Pallet"),), None), 300)
    assert "^GB" in render_label(LabelSpec("U-00001", (), "Return"), 300)


def test_caret_and_tilde_in_names_cannot_break_the_label():
    zpl = render_label(LabelSpec("C-0001", (("Name", "Bin ^XZ~JR"),), "Foot ^XZ"), 300)
    assert zpl.count("^XA") == 1 and zpl.count("^XZ") == 1
    assert "~JR" not in zpl


def test_unsupported_dpi_and_size_are_refused_and_batches_join():
    spec = LabelSpec("C-0001", (), None)
    with pytest.raises(ValueError):
        render_label(spec, 600)
    with pytest.raises(ValueError):
        render_label(spec, 300, 2.25, 1.25)
    assert render_batch([spec, LabelSpec("C-0002", (), None)], 300).count("^XA") == 2


@pytest.mark.parametrize("code", ["", "C-" + "1" * 19, "A>5B", "A^B", "A~B", "CAFÉ-1"])
def test_codes_the_barcode_would_change_are_refused(code):
    with pytest.raises(ValueError):
        render_label(LabelSpec(code, (), None), 300)


def test_a_twenty_character_code_prints_unchanged_and_twenty_one_is_refused():
    code = "C-" + "1" * 18
    assert len(code) == MAX_CODE_LENGTH
    for dpi in (300, 203):
        assert "^FD" + code + "^FS" in render_label(LabelSpec(code, (), None), dpi)
    with pytest.raises(ValueError):
        render_label(LabelSpec(code + "1", (), None), 300)


def test_line_breaks_in_names_become_spaces():
    zpl = render_label(LabelSpec("C-0001", (("Name", "Bin\nA"),), None), 300)
    assert "^FDBin A^FS" in zpl


def _heights(zpl):
    return [int(m) for m in re.findall(r"\^A0R,(\d+),\d+", zpl)]


def test_title_prints_large_between_number_and_rows():
    z = render_label(LabelSpec("R-00038", (("Type", "Laptop"),), None, "MAG-PNTR-007"), 300)
    title = re.search(r"\^FO(\d+),\d+\^A0R,(\d+),\d+\^FB\d+,1,0,C\^FDMAG-PNTR-007\^FS", z)
    assert title and int(title.group(2)) > 45
    number = re.search(r"\^FO(\d+),\d+\^A0R,\d+,\d+\^FB\d+,1,0,C\^FDR-00038\^FS", z)
    row = re.search(r"\^FO(\d+),\d+\^A0R,45,45\^FDType\^FS", z)
    assert int(number.group(1)) > int(title.group(1)) > int(row.group(1))


def test_label_without_title_renders_as_before():
    z = render_label(LabelSpec("U-00001", (("Type", "Laptop"),), "Return"), 300)
    assert z == "\n".join([
        "^XA", "^CI28", "^PW600", "^LL900", "^LH0,0",
        "^FO471,282^BY3,3,99^BCR,99,N,N,N^FDU-00001^FS",
        "^FO396,30^A0R,60,60^FB840,1,0,C^FDU-00001^FS",
        "^FO336,30^A0R,45,45^FDType^FS",
        "^FO336,30^A0R,45,45^FB840,1,0,R^FDLaptop^FS",
        "^FO81,30^GB6,840,6^FS",
        "^FO30,30^A0R,36,36^FB840,1,0,C^FDReturn^FS",
        "^XZ"])


def test_a_22_character_serial_still_prints_whole_with_a_title_and_details():
    z = render_label(LabelSpec("R-00038", (("Details", "55 in, 4k"), ("Serial", SERIAL_22)),
                               "Return", "MAG-PNTR-007"), 300)
    assert "^FD" + SERIAL_22 + "^FS" in z
    assert _value_height(z, SERIAL_22) == 45


def test_a_long_title_is_smaller_and_never_overprints():
    z = render_label(LabelSpec("R-00038", (), None, "A" * 40), 300)
    assert min(_heights(z)) < 66


# Top of the footer rule in inches, from the layout constants.
RULE_TOP = 2.0 - zpl._MARGIN - zpl._FOOTER_H - zpl._GAP - zpl._RULE_H


def test_rows_that_would_cross_the_footer_rule_are_dropped_from_the_end():
    rows = tuple((f"L{i}", f"V{i}") for i in range(8))
    titled = render_label(LabelSpec("R-00038", rows, "Return", "Name"), 300)
    assert "^FDL2^FS" in titled and "^FDL3^FS" not in titled
    no_footer = render_label(LabelSpec("R-00038", rows, None, "Name"), 300)
    assert "^FDL3^FS" in no_footer and "^FDL4^FS" not in no_footer
    for line in titled.splitlines():
        if line.startswith("^FO") and "^GB" not in line:
            x = int(re.match(r"\^FO(\d+)", line).group(1))
            assert x >= 600 - round(RULE_TOP * 300) or "Return" in line


def test_titled_label_keeps_serial_and_printed_and_drops_type_then_vendor_tag():
    rows = (("Type", "Laptop"), ("Vendor tag", "Vendor A: A0038"),
            ("Serial", SERIAL_22), ("Printed", "2026-11-07"))
    z = render_label(LabelSpec("R-00038", rows, "Return", "MAG-TV-009"), 300)
    assert "^FDType^FS" not in z and "^FDVendor tag^FS" in z
    assert "^FDSerial^FS" in z and "^FDPrinted^FS" in z
    assert _value_height(z, SERIAL_22) == 45
    five = rows + (("Extra", "x"),)
    z = render_label(LabelSpec("R-00038", five, "Return", "MAG-TV-009"), 300)
    assert "^FDVendor tag^FS" not in z and "^FDSerial^FS" in z and "^FDPrinted^FS" in z
