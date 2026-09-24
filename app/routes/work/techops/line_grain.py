"""Turns a request's answers into the lines TechOps will install.

This is the only implementation of the grain rules. The form's order
preview, the draft save, and the submit path all call expand_to_lines();
the order sheet will too. A second implementation anywhere, including in
JavaScript, is a defect: the requester would count lines the server does
not create, and nothing would fail.

Plain Python. No Flask, no database, no application context. The eleven
invariants in the spec's Line grain section are checked against this file.
Task 3 implements invariants 1, 2, 3, 9, 10, and 11; Task 4 adds phone
handling for invariants 4 through 8.
"""
from __future__ import annotations

from dataclasses import dataclass

SERVICE_WIFI = "WIFI"
SERVICE_ETHERNET = "ETHERNET"
SERVICE_PHONE_NUMBER = "PHONE_NUMBER"
SERVICE_DESK_PHONE = "DESK_PHONE"
SERVICE_NO_SERVICES = "NO_SERVICES"

ANSWER_NEEDS = "NEEDS"
ANSWER_NOTHING = "NOTHING"

PURPOSE_VOICE = "VOICE"
PURPOSE_TEXT = "TEXT"
PURPOSE_BOTH = "BOTH"
PURPOSES = (PURPOSE_VOICE, PURPOSE_TEXT, PURPOSE_BOTH)

SOURCE_NEW = "NEW"

# The department-wide "no TechOps services needed" affirmation. Same
# service code a per-space "nothing needed" card produces; expand_to_lines
# emits one of these, space_id NULL, when RequestAnswers.no_services_needed
# is set, so the affirmation goes through the same per-line review as
# everything else and the order preview shows it before submit.
NO_SERVICES_AFFIRMATION_DESCRIPTION = (
    "Department affirmed no TechOps services are needed for this event. "
    "TechOps to verify before this is finalized."
)


@dataclass(frozen=True)
class EthernetDrop:
    location: str
    usage: str


@dataclass(frozen=True)
class PhoneHandset:
    location: str


@dataclass(frozen=True)
class PhoneLine:
    # 1-based position within its own space. Half of the sharing key Task 4 reads.
    index: int
    # SOURCE_NEW, or "<space_id>:<line_index>" naming the line it shares.
    source: str
    purpose: str | None
    internal_only: bool
    usage: str = ""
    caller_id_name: str = ""
    voicemail_slack_channel: str = ""
    text_slack_channel: str = ""
    forward_target: str = ""
    handsets: tuple[PhoneHandset, ...] = ()


@dataclass(frozen=True)
class SpaceAnswer:
    space_id: int
    display_name: str
    answer: str
    no_services_reason: str = ""
    # Tri-state, not a plain bool: True (needed), False (declined), or None
    # (the WiFi radios were never touched). A checkbox cannot carry this;
    # "unticked" would mean both "no WiFi here" and "nobody has looked at
    # this card" with nothing to tell them apart.
    wifi_requested: bool | None = None
    wifi_declined_reason: str = ""
    wifi_description: str = ""
    ethernet_drops: tuple[EthernetDrop, ...] = ()
    phone_lines: tuple[PhoneLine, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class DepartmentWideAnswer:
    service_code: str
    location: str = ""
    usage: str = ""
    description: str = ""


@dataclass(frozen=True)
class RequestAnswers:
    primary_contact_name: str
    primary_contact_email: str
    additional_notes: str
    no_services_needed: bool
    action: str
    spaces: tuple[SpaceAnswer, ...] = ()
    department_wide: tuple[DepartmentWideAnswer, ...] = ()
    # Which card's own "Save this space" button posted this save, or None
    # for the bottom Save Draft / Submit buttons. Carried through to the
    # post-save redirect (?open=<id>) so the requester lands back on the
    # card they were editing instead of the top of the form.
    save_space_id: int | None = None


@dataclass
class PlannedLine:
    service_code: str
    space_id: int | None = None
    location: str | None = None
    usage: str | None = None
    description: str | None = None
    config: dict | None = None
    purpose: str | None = None
    internal_only: bool = False
    # Position of this line's parent within the returned list, not a
    # database id. The preview needs the reference before any row exists;
    # replace_lines() converts it to parent_line_id after the flush that
    # assigns primary keys.
    parent_index: int | None = None


def wifi_is_forced(space: SpaceAnswer) -> bool:
    """Invariant 2: gear in the room means coverage for it.

    True when a decline needs a stated reason to be accepted. Does not
    decide whether a WiFi line exists; that is a plain read of
    `wifi_requested` in `_expand_space`.
    """
    return bool(space.ethernet_drops or space.phone_lines)


def _sharing_key(space_id: int, line_index: int) -> str:
    """Identify a phone line across spaces the way `PhoneLine.source` does.

    A line that owns its number is keyed by its own space and index; a
    line that shares reads this same string back out of `source`.
    """
    return f"{space_id}:{line_index}"


def _owns_a_number(line: PhoneLine) -> bool:
    """Invariant 6: a line sharing another space's number creates none."""
    return line.source == SOURCE_NEW


def _phone_config(line: PhoneLine) -> dict:
    # Invariant 8. dial_in and dial_out are written here rather than read
    # from the form, so a crafted POST cannot set them on an internal-only
    # line. Pass 2 adds voice_delivery and no_answer.
    return {
        "caller_id_name": line.caller_id_name,
        "voicemail_slack_channel": line.voicemail_slack_channel,
        "text_slack_channel": line.text_slack_channel,
        "forward_target": line.forward_target,
        "dial_in": False if line.internal_only else True,
        "dial_out": False if line.internal_only else True,
    }


def expand_to_lines(answers: RequestAnswers) -> list[PlannedLine]:
    """Return the lines this request will create, in line-number order.

    Spaces first, in the order the form rendered them, then
    department-wide services. Invariant 11: line numbers come from this
    order, not from space id or any later regrouping.

    A handset may ring a number owned by a space expanded earlier or
    later than its own, so `_expand_space` cannot resolve `parent_index`
    by itself. It hands back each space's handset-to-sharing-key and
    key-to-number-position maps; this function offsets both by where that
    space landed in the combined list, drops handsets whose number turned
    out text-only (invariant 7), and only then assigns `parent_index`,
    against the list positions that survive the drop.
    """
    lines: list[PlannedLine] = []
    # Index of a DESK_PHONE line -> the sharing key it rings.
    pending_parent: dict[int, str] = {}
    # Sharing key -> index of the PHONE_NUMBER line that owns it.
    number_index_by_key: dict[str, int] = {}

    for space in answers.spaces:
        space_lines, handset_keys, number_positions = _expand_space(space)
        base = len(lines)
        for offset, key in handset_keys.items():
            pending_parent[base + offset] = key
        for key, position in number_positions.items():
            number_index_by_key[key] = base + position
        lines.extend(space_lines)

    for entry in answers.department_wide:
        lines.append(PlannedLine(
            service_code=entry.service_code,
            space_id=None,
            location=entry.location or None,
            usage=entry.usage or None,
            description=entry.description or None,
        ))

    # The whole-department affirmation. validate() refuses to combine this
    # with real space cards at submit time, so this and a space's own
    # lines above are never both accepted into one request; a draft in
    # progress can still hold both transiently.
    if answers.no_services_needed:
        lines.append(PlannedLine(
            service_code=SERVICE_NO_SERVICES,
            space_id=None,
            description=NO_SERVICES_AFFIRMATION_DESCRIPTION,
        ))

    # Invariant 7, applied after inheritance: a sharing line has no
    # purpose of its own, so a handset is only known to ring a text-only
    # number once its owner's PHONE_NUMBER line has been emitted. Reading
    # the owner's actual `purpose` here, rather than threading a second
    # purpose map through the first pass, is the one source of truth and
    # cannot drift from what the owner's own line says.
    keep: list[tuple[int, PlannedLine]] = []
    for index, line in enumerate(lines):
        if line.service_code == SERVICE_DESK_PHONE:
            key = pending_parent.get(index)
            owner = number_index_by_key.get(key) if key is not None else None
            if owner is not None and lines[owner].purpose == PURPOSE_TEXT:
                continue
        keep.append((index, line))

    # parent_index is assigned against the list positions above, after
    # the drop: a dropped handset shifts every later line's position, and
    # an index taken before the drop would point at the wrong number.
    old_to_new = {old: new for new, (old, _) in enumerate(keep)}
    for old_index, line in keep:
        if line.service_code != SERVICE_DESK_PHONE:
            continue
        key = pending_parent.get(old_index)
        owner = number_index_by_key.get(key) if key is not None else None
        # A source naming a line that does not exist leaves owner None;
        # validate() rejects that state later, so expansion just returns
        # it rather than raising.
        line.parent_index = old_to_new.get(owner) if owner is not None else None

    return [line for _, line in keep]


def _expand_space(
    space: SpaceAnswer,
) -> tuple[list[PlannedLine], dict[int, str], dict[str, int]]:
    """Expand one space's answers, deferring phone parent resolution.

    Returns the space's lines plus two maps local to that list: handset
    position -> sharing key it rings, and sharing key -> position of the
    PHONE_NUMBER line that owns it. `expand_to_lines` offsets both into
    the combined list and resolves them once every space has run.
    """
    # A card with no answer yet produces no lines, whatever has been typed
    # into its fields. Persisting a picked-but-unanswered space (Task 10)
    # means this state can now reach a real save rather than only an
    # in-progress form; without this guard, filling in a field before
    # choosing "Needs services" or "Nothing needed" would create real
    # WorkLines the requester never committed to.
    if space.answer not in (ANSWER_NEEDS, ANSWER_NOTHING):
        return [], {}, {}

    # Invariant 9: a space that needs nothing produces that one line and
    # nothing else, whatever else was left filled in on a card the
    # requester then switched to "nothing needed".
    if space.answer == ANSWER_NOTHING:
        return [PlannedLine(
            service_code=SERVICE_NO_SERVICES,
            space_id=space.space_id,
            description=space.no_services_reason or None,
        )], {}, {}

    lines: list[PlannedLine] = []

    # Invariant 2. A line exists only for an explicit "needs WiFi" answer;
    # a decline and an unanswered card both produce nothing, whatever gear
    # is in the room. validate() is what makes an unanswered forced space
    # unsubmittable, not a silent default here.
    if space.wifi_requested is True:
        lines.append(PlannedLine(
            service_code=SERVICE_WIFI,
            space_id=space.space_id,
            description=space.wifi_description or None,
        ))

    # Invariant 3.
    for drop in space.ethernet_drops:
        lines.append(PlannedLine(
            service_code=SERVICE_ETHERNET,
            space_id=space.space_id,
            location=drop.location,
            usage=drop.usage,
        ))

    handset_keys: dict[int, str] = {}
    number_positions: dict[str, int] = {}
    for phone in space.phone_lines:
        own_key = _sharing_key(space.space_id, phone.index)
        owns = _owns_a_number(phone)
        if owns:
            number_positions[own_key] = len(lines)
            lines.append(PlannedLine(
                service_code=SERVICE_PHONE_NUMBER,
                space_id=space.space_id,
                usage=phone.usage or None,
                purpose=phone.purpose,
                internal_only=phone.internal_only,
                config=_phone_config(phone),
            ))
        # Handsets are emitted even for a sharing line and even for a
        # text-only line; invariant 7 drops them in expand_to_lines, once
        # the owner's purpose (inherited for a sharing line) is known.
        ring_key = own_key if owns else phone.source
        for handset in phone.handsets:
            handset_keys[len(lines)] = ring_key
            lines.append(PlannedLine(
                service_code=SERVICE_DESK_PHONE,
                space_id=space.space_id,
                location=handset.location,
                # Usage is not copied from the parent. A reviewer follows
                # the parent reference; two copies of one sentence drift.
                usage=None,
            ))

    return lines, handset_keys, number_positions
