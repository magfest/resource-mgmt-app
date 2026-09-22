"""
Parse a venue table pasted from a spreadsheet.

Pure functions. No Flask, no database, no writes. The parser says what a
paste means and what is wrong with it; the caller decides what to create.

A generated code, from `generate_code` or through `parse_rows`, carries its
kind: `-P-` for a room, `-S-` for a slice, inserted after the prefix. These
codes get pasted into Slack and read with no app next to them to explain
them, so marking only the ambiguous ones would make an unmarked one look
mistyped. The marker applies to generated codes only; nothing enforces it,
and a code the operator types, including every pop-up space created on the
event page, is honored exactly as typed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

COLUMNS = ("Name", "Code", "Dimensions", "Area", "Parent code")

# A pasted sheet's header often gives each column a phrase, not the bare
# column name ("DIMENSIONS (LXWXH)", "Area (sq.ft)"), and does not always
# put a recognizable label in the first cell. A single matching cell is too
# loose: real spaces are named "Pre-function Area", "Registration Area",
# "Load-in Area", and a bare substring match on one cell would swallow the
# first one pasted. Require at least two cells to look like a label before
# treating the row as a header. Cost, narrower than the one-cell rule: a
# first pasted row with two label-shaped cells, something like "Area 51 |
# CODE1", is still lost and the operator retypes it. That is the better
# trade against an unrecognized header arriving ticked, which creates a
# space unless the operator unticks it.
HEADER_CELL_LABELS = ("name", "code", "dimension", "area", "parent")
HEADER_MIN_MATCHING_CELLS = 2

# Space.code is String(32) and Space.name is String(128). SQLite enforces
# neither; Postgres raises. Both live here so the paste parser and the
# add/edit form bound them from one definition.
MAX_CODE_LENGTH = 32
MAX_NAME_LENGTH = 128

# Space.area_sqft is a plain Integer. SQLite stores anything outside
# Postgres's int4 range (-2147483648 to 2147483647); Postgres raises. A
# negative area is also nonsensical, so it is rejected outright rather than
# floored. Lives here, not in catalog.py, so the paste parser and the
# add/edit form enforce the same bound without a second copy of it.
MAX_AREA_SQFT = 2147483647


def _normalize_name(name: str) -> str:
    """Fold a name for comparison: collapse whitespace runs, casefold.

    Lives here, not in catalog.py, so the paste parser and the add/edit
    form compare names the same way without a second copy of the rule.
    """
    return " ".join(name.split()).casefold()


# generate_code's kind parameter takes the literal values of
# app.models.SPACE_KIND_ROOM ("ROOM") and SPACE_KIND_SLICE ("SLICE"), not an
# import of them; this module stays free of Flask and the database, and a
# caller's own SPACE_KIND_ROOM/SPACE_KIND_SLICE compares equal by value.
_KIND_MARKER = {"ROOM": "P", "SLICE": "S"}


def generate_code(name: str, kind: str) -> str:
    """Build a code from a space name, marked with its kind.

    PREFIX-P or PREFIX-P-SUFFIX for a room, PREFIX-S or PREFIX-S-SUFFIX for
    a slice; see the module docstring for why. Imperfect on purpose. The
    preview shows every generated code and lets a person fix it, so a rule
    that handles most of a venue beats one that tries to handle all of it
    and produces nonsense at the edges. Can return an empty string, for a
    name with no ASCII letter or digit in its first token; the caller must
    reject that rather than write it, and it never carries a bare kind
    marker on its own.
    """
    tokens = name.split()
    if not tokens:
        return ""

    prefix = re.sub(r"[^A-Za-z0-9]", "", tokens[0])[:3].upper()
    if not prefix:
        return ""
    marker = _KIND_MARKER[kind]

    if len(tokens) == 1:
        return f"{prefix}-{marker}"

    suffix = re.sub(r"[^A-Za-z0-9]", "", tokens[-1]).upper()
    # A long last word is a word, not a designator. "Maryland Ballroom"
    # becomes MAR-P rather than MAR-P-BALLROOM.
    if not suffix or len(suffix) > 4:
        return f"{prefix}-{marker}"
    return f"{prefix}-{marker}-{suffix}"


def is_slice_coded(code: str) -> bool:
    """True when a code's marker segment reads as a slice.

    Looks only at the segment right after the prefix: PREFIX-S or
    PREFIX-S-SUFFIX. The marker is generator-only and unenforced, so a
    hand-typed code such as CHE-S for "Chesapeake South" also reads this
    way; a caller using this for a warning must treat it as advisory.
    Case-insensitive, since a typed code may not be upper-cased yet.
    """
    parts = code.split("-")
    return len(parts) >= 2 and parts[1].strip().upper() == "S"


def implied_room_code(code: str) -> str | None:
    """Return the room code a slice-coded code implies, or None.

    MDB-S-1 and MDB-S both imply MDB-P: the suffix is dropped along with
    the marker swap, matching generate_code's own shape for a room with
    no suffix. Returns None when `code` is not slice-coded.
    """
    if not is_slice_coded(code):
        return None
    prefix = code.split("-", 1)[0].strip().upper()
    return f"{prefix}-P"


@dataclass
class ParsedRow:
    name: str
    code: str
    dimensions: str | None
    # The validated value, safe to write to Space.area_sqft.
    area_sqft: int | None
    # What the operator typed, redisplayed on a rejection. Kept apart from
    # area_sqft because that one is written to an Integer column: SQLite
    # would store "1,200" there and Postgres would raise.
    area_text: str
    parent_code: str | None
    generated_code: bool
    error: str | None
    # Whether the row is slated for creation. parse_rows sets this from
    # its own error (a fresh preview proposes every clean row); the
    # confirm route sets it from the create_<i> checkbox actually
    # submitted, so a rejected confirm's re-render does not flip an
    # unticked row back on.
    checked: bool = True


def _split(line: str) -> list[str]:
    # A spreadsheet paste is tab separated. A hand-typed line is more often
    # comma separated.
    parts = line.split("\t") if "\t" in line else line.split(",")
    return [p.strip() for p in parts]


def _name_error(name: str) -> str | None:
    """Check a name's presence and length."""
    if not name:
        return "This row has no name"
    if len(name) > MAX_NAME_LENGTH:
        return f"Name is longer than {MAX_NAME_LENGTH} characters"
    return None


def validate_row(name: str, code: str, area_sqft: int | None,
                 parent_code: str | None, *, existing_codes: set[str],
                 existing_names: set[str], seen_codes: set[str],
                 seen_names: set[str], seen_room_codes: set[str]) -> str | None:
    """Apply every per-row rule to one already-structured row.

    Both parse_rows and catalog_paste_confirm call this, so a rule fixed
    here fixes both. `seen_codes`, `seen_names` and `seen_room_codes`
    accumulate across a batch and are mutated here on success, so a
    later row sees an earlier one. Parent legality is checked separately
    by `validate_parent`, once every row's code is known.
    """
    error = _name_error(name)

    if error is None and area_sqft is not None:
        if area_sqft > MAX_AREA_SQFT:
            error = f"Area must be {MAX_AREA_SQFT:,} square feet or fewer"
        elif area_sqft < 0:
            error = "Area must be zero or greater"

    if error is None and not code:
        error = "This row has no code"
    elif error is None and len(code) > MAX_CODE_LENGTH:
        error = f"Code '{code}' is longer than {MAX_CODE_LENGTH} characters"

    normalized_name = _normalize_name(name) if name else ""
    if error is None and normalized_name in seen_names:
        error = f"Name '{name}' is already used earlier in this paste"
    elif error is None and normalized_name in existing_names:
        error = f"A space named '{name}' already exists at this venue"

    if error is None and code in seen_codes:
        error = f"Code {code} is already used earlier in this paste"
    elif error is None and code in existing_codes:
        error = f"Code {code} already exists at this venue"

    if error is None:
        seen_codes.add(code)
        seen_names.add(normalized_name)
        if not parent_code:
            seen_room_codes.add(code)

    return error


def validate_parent(code: str, parent_code: str | None,
                    all_codes: set[str], known_parents: set[str]) -> str | None:
    """Check one row's parent code against the batch's resolved codes.

    Runs after every row's own code is known, so a parent may be pasted
    after its children; see parse_rows' second pass. `all_codes` is
    existing plus this batch's codes, and `known_parents` is legal
    parent codes plus this batch's own room codes. Returns None for a
    row with no parent code; that makes a room, not an error.
    """
    if not parent_code:
        return None
    if parent_code == code:
        return "A space cannot be its own parent"
    if parent_code not in all_codes:
        return f"No space has parent code {parent_code}"
    if parent_code not in known_parents:
        return (f"Parent code {parent_code} is not a room this venue keeps "
                "between events")
    return None


def parse_rows(text: str, existing_codes: set[str], existing_names: set[str],
               legal_parent_codes: set[str]) -> list[ParsedRow]:
    """Parse pasted text into rows, each carrying its own error or None.

    `existing_names` must already be normalized with `_normalize_name`; the
    caller draws it from permanent rows only, matching the add/edit form's
    venue-wide name rule. `existing_codes` spans every row at the venue and
    answers only "is this code taken"; `legal_parent_codes` is the narrower
    question the add form's parent picker answers, "is this code a room a
    child may attach to" (active, permanent, kind ROOM). The two must stay
    separate: a slice or an archived room has a real code that still can't
    parent another row.
    """
    rows: list[ParsedRow] = []
    seen_codes: set[str] = set()
    seen_names: set[str] = set()
    # Codes of rows in this paste that are themselves rooms (no parent code
    # of their own), so a parent pasted earlier in sheet order can resolve
    # a child even before it exists in the database.
    seen_room_codes: set[str] = set()
    header_checked = False

    for line in text.splitlines():
        if not line.strip():
            continue

        raw_cells = _split(line)

        if not header_checked:
            header_checked = True
            matching_cells = sum(
                1 for cell in raw_cells
                if any(label in cell.lower() for label in HEADER_CELL_LABELS)
            )
            if matching_cells >= HEADER_MIN_MATCHING_CELLS:
                continue

        if len(raw_cells) < 5:
            # Padding a short row guesses which of the five columns are
            # missing; that guess is exactly what let a three-column sheet
            # (Name, Dimensions, Area, no Code, no Parent code) preview
            # clean with a dimension string sitting in the code field.
            # Require all five, blank or not, and name what was found.
            found = len(raw_cells)
            rows.append(ParsedRow(
                name=raw_cells[0], code="",
                dimensions=None, area_sqft=None, area_text="",
                parent_code=None, generated_code=False,
                error=(f"This row has {found} column"
                       f"{'' if found == 1 else 's'}; all 5 are needed, "
                       "even when blank"),
                checked=False,
            ))
            continue

        # Extra spreadsheet columns (capacity, notes) are dropped, not an
        # error; a real venue sheet carries capacity figures this app
        # excludes on purpose.
        name, code, dimensions, area_raw, parent_code = raw_cells[:5]
        parent_code = parent_code.upper() or None

        # Checked directly, ahead of validate_row, only to decide whether
        # code generation below should run; the name rule itself lives in
        # _name_error and validate_row calls the same function.
        error = _name_error(name)

        area = None
        if error is None and area_raw:
            try:
                area = int(area_raw)
            except ValueError:
                error = f"Area '{area_raw}' is not a whole number"

        generated = False
        if error is None and not code:
            # Kind for the marker comes from whether this row's own parent
            # code cell is present, the same signal that decides whether
            # the row becomes a room or a slice; not from whether that
            # parent turns out to be legal, resolved later in the second
            # pass. A row that fails its own parent check still needs the
            # code its would-be kind implies.
            row_kind = "SLICE" if parent_code else "ROOM"
            code = generate_code(name, row_kind)
            generated = True
            if not code:
                error = ("No code could be generated from this name; "
                         "type one")
        code = code.upper()

        if error is None:
            error = validate_row(
                name, code, area, parent_code,
                existing_codes=existing_codes, existing_names=existing_names,
                seen_codes=seen_codes, seen_names=seen_names,
                seen_room_codes=seen_room_codes,
            )

        rows.append(ParsedRow(
            name=name, code=code,
            dimensions=dimensions or None, area_sqft=area,
            area_text=area_raw,
            parent_code=parent_code, generated_code=generated, error=error,
            checked=error is None,
        ))

    # Parent codes resolve in a second pass, so a parent may be pasted after
    # its children. People paste in sheet order, not dependency order.
    all_codes = seen_codes | existing_codes
    known_parents = legal_parent_codes | seen_room_codes
    for row in rows:
        if row.error is None and row.parent_code:
            row.error = validate_parent(row.code, row.parent_code,
                                        all_codes, known_parents)
            row.checked = row.error is None

    return rows
