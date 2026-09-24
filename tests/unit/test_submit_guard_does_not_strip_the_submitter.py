"""The double-submit guard must not disable buttons synchronously.

This asserts on template source, which is weak, because the repository has
no JavaScript test runner. It is here anyway because the failure it guards
is severe and silent: a disabled control is not submitted, so disabling the
clicked button before the browser builds the form data drops its name and
value. The TechOps request form then reaches the server with no `action`,
takes the draft branch, and reports "Draft updated." on a submit. That
defect reached the repository owner's hands in September 2026.

Only three templates in the app use a named submit button and all three are
the TechOps request form, which is why this sat harmless in base.html for
as long as it did.
"""
import pathlib
import re

BASE = pathlib.Path("app/templates/layout/base.html")


def _guard_body() -> str:
    """The submit listener's body, up to the re-enable timer."""
    src = BASE.read_text()
    start = src.index("document.addEventListener('submit'")
    return src[start:src.index("}, 5000);", start)]


def test_the_disable_is_deferred_not_synchronous():
    body = _guard_body()
    disable = body.index("btn.disabled = true")
    defer = body.index("setTimeout")
    assert defer < disable, (
        "the guard disables submit buttons before the browser builds the "
        "form data, which strips the clicked button's name and value"
    )


def test_the_flag_not_the_disabling_is_what_blocks_a_second_submit():
    """If the flag ever goes, deferring the disable would leave nothing
    preventing a double submit."""
    body = _guard_body()
    assert "form.dataset.submitted" in body
    assert re.search(r"if \(form\.dataset\.submitted\)", body)
