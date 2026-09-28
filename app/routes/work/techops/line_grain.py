"""Turns a request's answers into the lines TechOps will install.

This is the only implementation of the grain rules. The form's order
preview, the draft save, and the submit path all call expand_to_lines();
the order sheet will too. A second implementation anywhere, including in
JavaScript, is a defect: the requester would count lines the server does
not create, and nothing would fail.

Plain Python. No Flask, no database, no application context. The eleven
invariants below are checked against this file. Invariants 1, 2, 3, 9, 10
and 11 govern WiFi, ethernet and the department-wide services; 4 through 8
govern phone numbers and handsets.
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

# What happens to a call that arrives. NONE is an explicit "this number
# carries no voice", which an unanswered dropdown ("") is not.
VOICE_DELIVERY_DESK_PHONE = "DESK_PHONE"
VOICE_DELIVERY_FORWARD = "FORWARD"
VOICE_DELIVERY_VOICEMAIL = "VOICEMAIL"
VOICE_DELIVERY_NONE = "NONE"
VOICE_DELIVERIES = (
    VOICE_DELIVERY_DESK_PHONE, VOICE_DELIVERY_FORWARD,
    VOICE_DELIVERY_VOICEMAIL, VOICE_DELIVERY_NONE,
)

NO_ANSWER_VOICEMAIL = "VOICEMAIL"
NO_ANSWER_FORWARD = "FORWARD"
NO_ANSWER_RING = "RING"
NO_ANSWERS = (NO_ANSWER_VOICEMAIL, NO_ANSWER_FORWARD, NO_ANSWER_RING)

# Keys recorded in config["open_questions"]. These are questions the phone
# team still has to ask, not validation results: a request carrying them is
# acceptable and submits.
QUESTION_CAPABILITIES = "capabilities"
QUESTION_TEXT_SLACK = "text_slack_channel"
QUESTION_VOICEMAIL_SLACK = "voicemail_slack_channel"
QUESTION_FORWARD_TARGET = "forward_target"
QUESTION_VOICE_DELIVERY = "voice_delivery"
QUESTION_NO_ANSWER = "no_answer"
QUESTION_HANDSETS = "handsets"
QUESTION_LOCATION = "location"

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
    # 1-based position within its own space. Half of the sharing key a
    # handset in another space uses to name the number it rings.
    index: int
    # SOURCE_NEW, or "<space_id>:<line_index>" naming the line it shares.
    source: str
    # Reachability from outside. Calling between rooms needs neither.
    dial_in: bool = False
    dial_out: bool = False
    texts: bool = False
    # One of VOICE_DELIVERIES, or "" while the question is unanswered.
    voice_delivery: str = ""
    # One of NO_ANSWERS, or "" when unanswered or never shown.
    no_answer: str = ""
    usage: str = ""
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


def asks_no_answer(line: PhoneLine) -> bool:
    """The no-answer question is shown for a ringing delivery only.

    Direct-to-voicemail already says what happens, and a number with no
    voice has nothing to answer. This is the same rule the form applies in
    _space_card.html; both read it from here so they cannot drift.
    """
    return line.voice_delivery in (VOICE_DELIVERY_DESK_PHONE,
                                   VOICE_DELIVERY_FORWARD)


def has_voice(line: PhoneLine) -> bool:
    """A delivery choice is what says the number carries voice at all.

    An empty string is an unanswered question, not a decline; both leave the
    number without voice for now, and the unanswered case is recorded as a
    question rather than refused.
    """
    return bool(line.voice_delivery) and line.voice_delivery != VOICE_DELIVERY_NONE


def derive_purpose(line: PhoneLine) -> str | None:
    """Compute the stored purpose from what the requester asked the number to do.

    Purpose is not a question on the form. Three capability answers carry
    more than the enum, and asking both is what made the old dropdown read
    as deciding nothing.
    """
    voice = has_voice(line)
    if voice and line.texts:
        return PURPOSE_BOTH
    if voice:
        return PURPOSE_VOICE
    if line.texts:
        return PURPOSE_TEXT
    return None


def derive_internal_only(line: PhoneLine) -> bool:
    # This is not "no calls". It is a number that carries voice and reaches
    # nothing outside, which is a room phone that dials other rooms.
    return has_voice(line) and not line.dial_in and not line.dial_out


def phone_open_questions(line: PhoneLine) -> list[str]:
    """Name every field the form showed this line and left blank.

    A requester who leaves the Hotliner channel blank has not declined
    texts; the channel often has to be created first. Refusing the submit
    treated a pending decision as a withdrawn request.

    A sharing line is not passed here. It owns no number, so it has no
    capabilities, delivery, or channels of its own; its only question is
    where its handsets sit, recorded on the handset lines themselves.
    """
    questions: list[str] = []
    voice = has_voice(line)

    if not voice and not line.texts and line.voice_delivery:
        return [QUESTION_CAPABILITIES]

    if line.texts and not line.text_slack_channel:
        questions.append(QUESTION_TEXT_SLACK)
    # The delivery select is always on screen for a line that owns its
    # number, so a blank is a question. Without this a requester who ticks
    # texts and stops has a text-only number with nothing flagged.
    if not line.voice_delivery:
        questions.append(QUESTION_VOICE_DELIVERY)
        if not line.texts:
            questions.insert(0, QUESTION_CAPABILITIES)
        return questions
    if not voice:
        return questions

    if line.voice_delivery == VOICE_DELIVERY_VOICEMAIL:
        if not line.voicemail_slack_channel:
            questions.append(QUESTION_VOICEMAIL_SLACK)
        return questions

    if line.voice_delivery == VOICE_DELIVERY_FORWARD and not line.forward_target:
        questions.append(QUESTION_FORWARD_TARGET)
    if line.voice_delivery == VOICE_DELIVERY_DESK_PHONE and not line.handsets:
        questions.append(QUESTION_HANDSETS)

    # The no-answer question is hidden for direct-to-voicemail and no-voice,
    # so it is only unanswered here when it was actually on screen.
    if not line.no_answer:
        questions.append(QUESTION_NO_ANSWER)
    elif line.no_answer == NO_ANSWER_VOICEMAIL and not line.voicemail_slack_channel:
        questions.append(QUESTION_VOICEMAIL_SLACK)
    elif (line.no_answer == NO_ANSWER_FORWARD and not line.forward_target
          and QUESTION_FORWARD_TARGET not in questions):
        questions.append(QUESTION_FORWARD_TARGET)
    return questions


def _phone_config(line: PhoneLine) -> dict:
    # Invariant 8. dial_in and dial_out are written here rather than read
    # from the form, so a crafted POST cannot set outside calling on a
    # number whose voice was never asked for.
    voice = has_voice(line)
    # A value left behind by a question that stopped being asked is not an
    # answer. Switching delivery to "no voice" hides the no-answer question
    # and both voice channels, so their previous values describe a form the
    # requester is no longer looking at.
    no_answer = line.no_answer if asks_no_answer(line) else ""
    wants_voicemail = voice and (
        line.voice_delivery == VOICE_DELIVERY_VOICEMAIL
        or no_answer == NO_ANSWER_VOICEMAIL)
    wants_forward = voice and (
        line.voice_delivery == VOICE_DELIVERY_FORWARD
        or no_answer == NO_ANSWER_FORWARD)
    config = {
        "voicemail_slack_channel": line.voicemail_slack_channel if wants_voicemail else "",
        "text_slack_channel": line.text_slack_channel if line.texts else "",
        "forward_target": line.forward_target if wants_forward else "",
        "voice_delivery": line.voice_delivery or None,
        "no_answer": no_answer or None,
        "dial_in": line.dial_in and line.voice_delivery != VOICE_DELIVERY_NONE,
        "dial_out": line.dial_out and line.voice_delivery != VOICE_DELIVERY_NONE,
    }
    questions = phone_open_questions(line)
    if questions:
        config["open_questions"] = questions
    return config


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
            # Explicitly no voice, not merely derived text-only. An owner
            # who has not answered the voice question yet may still end up
            # with a desk phone, and dropping the sharer's handset here
            # would lose that space from the request with no trace.
            owner_cfg = (lines[owner].config or {}) if owner is not None else {}
            if owner_cfg.get("voice_delivery") == VOICE_DELIVERY_NONE:
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
    # into its fields. An unanswered space persists, so this state reaches
    # a real save rather than only an in-progress form; without this guard,
    # filling in a field before
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
                purpose=derive_purpose(phone),
                internal_only=derive_internal_only(phone),
                config=_phone_config(phone),
            ))
        # Handsets are emitted even for a sharing line and even for a
        # text-only line; invariant 7 drops them in expand_to_lines, once
        # the owner's purpose (inherited for a sharing line) is known.
        ring_key = own_key if owns else phone.source
        # A delivery that places no phone emits no handsets, however many
        # locations are still sitting in the posted form. The inputs stay
        # in the DOM once hidden, and an emitted row would survive every
        # later edit without ever being visible.
        places_handsets = (
            phone.voice_delivery == VOICE_DELIVERY_DESK_PHONE if owns else True)
        for handset in (phone.handsets if places_handsets else ()):
            handset_keys[len(lines)] = ring_key
            lines.append(PlannedLine(
                service_code=SERVICE_DESK_PHONE,
                space_id=space.space_id,
                location=handset.location,
                # Usage is not copied from the parent. A reviewer follows
                # the parent reference; two copies of one sentence drift.
                usage=None,
            ))

        # A desk phone with nowhere stated, and a shared line with no
        # handset at all, both produced nothing before; the shared case
        # lost the space from the request with no trace. Emit the handset
        # the answer implies and carry the question on it.
        wants_a_handset = (
            phone.voice_delivery == VOICE_DELIVERY_DESK_PHONE if owns else True
        )
        if wants_a_handset and not phone.handsets:
            handset_keys[len(lines)] = ring_key
            lines.append(PlannedLine(
                service_code=SERVICE_DESK_PHONE,
                space_id=space.space_id,
                location=None,
                usage=None,
                config={"open_questions": [QUESTION_LOCATION]},
            ))

    return lines, handset_keys, number_positions
