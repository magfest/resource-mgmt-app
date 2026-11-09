"""Keep the volunteer how-tos matched to the words on the scanner screens."""
import re

import pytest

from tests.inventory.test_retired_words import RETIRED, ROOT

DOCS = ROOT / "docs/inventory/volunteer"
SCREENS = "".join(
    p.read_text()
    for p in [*(ROOT / "app/static/inventory").glob("*.js"),
              *(ROOT / "app/templates/inventory/scan").glob("*.html")]
)


@pytest.mark.parametrize("name", ["count-a-pallet.md", "stack-a-pallet.md"])
def test_a_how_to_uses_only_words_from_the_screens(name):
    text = (DOCS / name).read_text()
    assert len(text.splitlines()) <= 60
    assert not RETIRED.search(text)
    for bold in re.findall(r"\*\*(.+?)\*\*", text):
        assert bold in SCREENS, f"{bold!r} is not on any scanner screen"
