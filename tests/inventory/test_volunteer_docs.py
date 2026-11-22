"""Keep the volunteer how-tos matched to the words on the scanner screens."""
import re

import pytest

from tests.inventory.test_retired_words import RETIRED, ROOT

DOCS = ROOT / "docs/inventory/volunteer"
JS = [ROOT / "app/static/inventory" / n
      for n in ("station_count.js", "station_move.js", "scan.js")]
LITERAL = re.compile(r"'((?:[^'\\\n]|\\.)*)'|\"((?:[^\"\\\n]|\\.)*)\"")
LITERALS = {a or b for f in JS for a, b in LITERAL.findall(f.read_text())}
# Buttons whose label is a fixed prefix plus a code, name or number.
PREFIXED = {"Packs of", "Save and start", "Keep counting", "Submit count", "Undo"}


def on_a_screen(word):
    if word in LITERALS:
        return True
    return word in PREFIXED and any(s.startswith(word + " ") for s in LITERALS)


@pytest.mark.parametrize("name", ["count-a-pallet.md", "stack-a-pallet.md"])
def test_a_how_to_uses_only_words_from_the_screens(name):
    text = (DOCS / name).read_text()
    assert len(text.splitlines()) <= 60
    assert not RETIRED.search(text)
    for bold in re.findall(r"\*\*(.+?)\*\*", text):
        assert on_a_screen(bold), f"{bold!r} is not a whole label on a scanner screen"
