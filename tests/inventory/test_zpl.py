import pytest

from app.inventory.zpl import LabelSpec, render_batch, render_label


def test_layout_is_in_inches_so_both_printers_match():
    spec = LabelSpec(code="C-0001", title="C-0001", subtitle="Pallet")
    assert "^PW675" in render_label(spec, 300)
    assert "^PW457" in render_label(spec, 203)
    assert "^BY3," in render_label(spec, 300)
    assert "^BY2," in render_label(spec, 203)


def test_caret_and_tilde_in_names_cannot_break_the_label():
    zpl = render_label(LabelSpec(code="C-0001", title="C-0001", subtitle="Bin ^XZ~JR"), 300)
    assert zpl.count("^XA") == 1 and zpl.count("^XZ") == 1
    assert "~JR" not in zpl


def test_unsupported_dpi_is_refused_and_batches_join():
    with pytest.raises(ValueError):
        render_label(LabelSpec("C-0001", "C-0001", None), 600)
    batch = render_batch([LabelSpec("C-0001", "C-0001", None),
                          LabelSpec("C-0002", "C-0002", None)], 300)
    assert batch.count("^XA") == 2
