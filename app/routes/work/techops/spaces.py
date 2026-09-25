"""Which spaces a TechOps request may show, and what each card knows.

The form never queries spaces directly. Every path that needs the set goes
through offerable_spaces(), so the rule the route enforces and the rule
parse_form enforces cannot drift apart.

Department assignment is not a precondition. A department may add any
space at the event's venue; the card carries a note rather than a block,
because a department that cannot file at all before a deadline is a worse
failure than a room claimed in error.
"""
from __future__ import annotations

from sqlalchemy.orm import joinedload

from app import db
from app.models import (
    Department,
    Space,
    SpaceAssignment,
    TechOpsLineDetail,
    TechOpsServiceType,
    WorkLine,
)
from app.routes.spaces.queries import spaces_for_department
from .line_grain import (
    EthernetDrop,
    PhoneHandset,
    PhoneLine,
    SOURCE_NEW,
    SpaceAnswer,
    wifi_is_forced,
)

# Codes whose catalog description backs a space card's collapsed-section
# summary (item 1: a closed <details> must explain itself). PHONE_NUMBER
# stands in for the whole "Phone System" section; DESK_PHONE's own
# description covers only the handset, not the line.
_COLLAPSIBLE_SERVICE_CODES = ("ETHERNET", "PHONE_NUMBER")


def collapsible_descriptions() -> dict[str, str]:
    """Seeded descriptions for a space card's collapsed ETHERNET/PHONE
    sections, keyed by service code.

    One query, reused across every card on the page rather than run once
    per card. Source is TechOpsServiceType.description (seeded in
    app/seeds/bootstrap.py:seed_techops_service_types), not template copy,
    so the sentence has one place to fix.
    """
    rows = (
        db.session.query(TechOpsServiceType.code, TechOpsServiceType.description)
        .filter(TechOpsServiceType.code.in_(_COLLAPSIBLE_SERVICE_CODES))
        .all()
    )
    return dict(rows)


def offerable_spaces(event_cycle) -> list[Space]:
    """Active spaces at this event's venue, permanent or this event's.

    EventCycle.venue_id is nullable and nothing in app/seeds populates it.
    An event with no venue offers nothing here; the route is responsible
    for telling the requester to contact the Hotels request channel
    instead of rendering an empty picker with no explanation.
    """
    if event_cycle.venue_id is None:
        return []
    return (
        db.session.query(Space)
        .filter(
            Space.venue_id == event_cycle.venue_id,
            Space.is_active.is_(True),
            db.or_(Space.event_cycle_id.is_(None),
                   Space.event_cycle_id == event_cycle.id),
        )
        .order_by(Space.sort_order, Space.code)
        .all()
    )


def space_display_names(venue_spaces: list[Space], cards: list[dict]) -> dict[int, str]:
    """Map every space id a request may legitimately name to the name it
    should be shown under.

    The venue's offerable catalog, overlaid with this request's own cards:
    space_cards() keeps a space the request already holds even after it
    stops being offerable (archived, venue swap), aliased for this event
    where space_cards() resolved a fold. parse_form's gate, the order
    preview, and the picker/redisplay logic all need this same map; three
    call sites building it by hand (create.py, edit.py, and the order
    preview) drifted from each other once already, so every caller passes
    its own already-computed venue_spaces/cards through this instead.
    """
    names = {s.id: s.name for s in venue_spaces}
    names.update({c["space"].id: c["display_name"] for c in cards})
    return names


def shared_with(space_ids, event_cycle_id: int, department_id: int) -> dict[int, list[str]]:
    """Other departments holding any of these spaces at this event.

    One query: SpaceAssignment rows for these spaces at this event whose
    department is not the caller's, joined to Department for the name. A
    space nobody else holds is absent from the result rather than mapped
    to an empty list.
    """
    space_ids = list(space_ids)
    if not space_ids:
        return {}
    rows = (
        db.session.query(SpaceAssignment.space_id, Department.name)
        .join(Department, Department.id == SpaceAssignment.department_id)
        .filter(
            SpaceAssignment.space_id.in_(space_ids),
            SpaceAssignment.event_cycle_id == event_cycle_id,
            SpaceAssignment.department_id != department_id,
        )
        .order_by(Department.name)
        .all()
    )
    result: dict[int, list[str]] = {}
    for space_id, name in rows:
        result.setdefault(space_id, []).append(name)
    return result


def _redisplay_extras(work_item) -> dict[int, dict]:
    """Rebuild the drop and phone-line answers a card needs, per space, plus
    the WiFi description a WIFI line carries.

    TechOpsRequestSpace carries the WiFi answer itself and both reasons;
    this covers what lives on WIFI, ETHERNET, PHONE_NUMBER, and DESK_PHONE
    lines instead. A WIFI line's presence is no longer read as the answer
    (space_cards() reads TechOpsRequestSpace.wifi_requested directly); this
    function still needs the line to recover its free-text description,
    which has nowhere else to live. A handset's own space_id can differ from the
    PHONE_NUMBER line it rings; that is what a shared line looks like on
    disk, and it is rebuilt here as a PhoneLine with no number of its own,
    sourced at the owner's space and position.

    A handset can also point at no line at all: `parent_line_id` is NULL
    when the space it shared from dropped that line on a later save, or
    (defensively; delete-and-recreate should make this unreachable today)
    names a line that no longer exists. Either way the original source
    string was never stored, so the share cannot be resolved. It is kept
    as its own line with a blank source and the handset's location
    preserved, not dropped: `validate()` already refuses a blank source on
    submit, so the requester is asked to re-pick rather than losing the
    line silently on the next save.

    Phone lines are emitted in stored row order rather than PHONE_NUMBER
    rows first: `expand_to_lines()` writes a phone entry's own number row
    immediately before its handset rows, so a single ordered pass over
    each space's rows recovers the original form-slot order.
    """
    if work_item is None or work_item.id is None:
        return {}

    details = (
        db.session.query(TechOpsLineDetail)
        .join(WorkLine, WorkLine.id == TechOpsLineDetail.work_line_id)
        .options(joinedload(TechOpsLineDetail.service_type))
        .filter(
            WorkLine.work_item_id == work_item.id,
            TechOpsLineDetail.space_id.isnot(None),
        )
        .order_by(WorkLine.line_number)
        .all()
    )
    if not details:
        return {}

    by_space: dict[int, list[TechOpsLineDetail]] = {}
    for detail in details:
        by_space.setdefault(detail.space_id, []).append(detail)

    # A PHONE_NUMBER line's position within its own space, keyed by its
    # work_line_id so a handset elsewhere can name the line it rings.
    owner_position: dict[int, tuple[int, int]] = {}
    for space_id, rows in by_space.items():
        position = 0
        for detail in rows:
            if detail.service_type.code == "PHONE_NUMBER":
                position += 1
                owner_position[detail.work_line_id] = (space_id, position)

    extras: dict[int, dict] = {}
    for space_id, rows in by_space.items():
        wifi_line = next(
            (d for d in rows if d.service_type.code == "WIFI"), None)
        drops = tuple(
            EthernetDrop(location=d.location or "", usage=d.usage or "")
            for d in rows if d.service_type.code == "ETHERNET"
        )

        # Handsets physically in this space, grouped by the line they ring:
        # one this same space owns, one owned elsewhere, or unresolved
        # (parent_line_id NULL, or naming a line that no longer exists —
        # the two are handled identically since neither can be traced back
        # to a source string). `None` is a valid dict key here and groups
        # every unresolved handset in this space together.
        local_handsets: dict[int, list[PhoneHandset]] = {}
        foreign_handsets: dict[int, list[PhoneHandset]] = {}
        orphan_handsets: dict[int | None, list[PhoneHandset]] = {}
        for detail in rows:
            if detail.service_type.code != "DESK_PHONE":
                continue
            handset = PhoneHandset(location=detail.location or "")
            owner = (owner_position.get(detail.parent_line_id)
                     if detail.parent_line_id is not None else None)
            if owner is not None and owner[0] == space_id:
                local_handsets.setdefault(detail.parent_line_id, []).append(handset)
            elif owner is not None:
                foreign_handsets.setdefault(detail.parent_line_id, []).append(handset)
            else:
                orphan_handsets.setdefault(detail.parent_line_id, []).append(handset)

        # One pass over this space's rows in stored order, emitting each
        # logical phone line the first time one of its rows is seen. A
        # frozen PhoneLine can't be reindexed after construction, so the
        # running counter is applied at append time instead of in a later
        # sort. A PHONE_NUMBER row is always its own line's first (and
        # only) row; a sharing or orphan line has no PHONE_NUMBER row, so
        # its first DESK_PHONE row is what fixes its position.
        phone_lines: list[PhoneLine] = []
        emitted: set = set()
        next_index = 1
        for detail in rows:
            code = detail.service_type.code
            if code == "PHONE_NUMBER":
                cfg = detail.config or {}
                phone_lines.append(PhoneLine(
                    index=next_index,
                    source=SOURCE_NEW,
                    purpose=detail.purpose,
                    internal_only=detail.internal_only,
                    usage=detail.usage or "",
                    caller_id_name=cfg.get("caller_id_name", ""),
                    voicemail_slack_channel=cfg.get("voicemail_slack_channel", ""),
                    text_slack_channel=cfg.get("text_slack_channel", ""),
                    forward_target=cfg.get("forward_target", ""),
                    handsets=tuple(local_handsets.get(detail.work_line_id, ())),
                ))
                next_index += 1
                continue

            if code != "DESK_PHONE":
                continue
            owner = (owner_position.get(detail.parent_line_id)
                     if detail.parent_line_id is not None else None)
            if owner is not None and owner[0] == space_id:
                continue  # already emitted above, with its own PHONE_NUMBER row
            if owner is not None:
                key, source = detail.parent_line_id, f"{owner[0]}:{owner[1]}"
                bucket = foreign_handsets
            else:
                # The original source string is not stored anywhere, so
                # the share's intent cannot be recovered here. A blank
                # source keeps the handset visible with its location
                # intact and fails validate()'s submit check until the
                # requester re-picks a line to share.
                key, source = detail.parent_line_id, ""
                bucket = orphan_handsets
            if key in emitted:
                continue
            emitted.add(key)
            phone_lines.append(PhoneLine(
                index=next_index, source=source, purpose=None,
                internal_only=False, handsets=tuple(bucket[key]),
            ))
            next_index += 1

        extras[space_id] = {
            "wifi_description": (wifi_line.description or "") if wifi_line else "",
            "drops": drops,
            "phone_lines": tuple(phone_lines),
        }
    return extras


def count_filled_drops(drops: tuple) -> int:
    """Count ethernet drops with content, for a card's collapsed badge.

    The rule: a row counts toward this count when any of its inputs
    holds a non-blank value. `drops` already obeys it by construction;
    form_utils._parse_drops drops an all-blank row before it ever
    reaches a SpaceAnswer, and _redisplay_extras only reconstructs rows
    that were actually saved. work_item_form.html's script-side twin
    (filledDropRows) restates the same rule against live, unsaved DOM
    state, where that filtering has not happened yet.
    """
    return len(drops)


def count_filled_phone(phone_lines: tuple) -> tuple[int, int]:
    """Count phone lines and their handsets with content, for a card's
    collapsed badge. Same rule and reasoning as count_filled_drops.

    `phone_lines` already holds only lines form_utils._parse_phone_lines
    kept (a line with no purpose, usage, or handset is skipped there),
    and each line's own `handsets` already holds only handsets with a
    filled location (_parse_handsets). Returns (line_count, handset_count).
    """
    return len(phone_lines), sum(len(line.handsets) for line in phone_lines)


def space_cards(work_item, department_id: int, event_cycle) -> list[dict]:
    """Every space a TechOps request may show a card for, in display order.

    The base set is the department's assigned spaces plus any space this
    request already holds, whether added through the picker or assigned
    and later unassigned. Every other offerable space is a
    picker candidate, not an automatic card; picker_candidates() computes
    that set. An assigned room's entry comes from spaces_for_department()
    instead, so a folded combination shows one card already aliased for
    this event, and its covered slices are dropped from the held set
    rather than shown a second time as their own unassigned card ("one
    card per room").

    Space this request already holds a TechOpsRequestSpace row for, or
    that its lines reference, is added even if it is no longer offerable
    (venue swap, archival), flagged is_assigned False so an existing
    answer is never silently dropped out from under an in-progress draft.
    A blank-answer card writes no TechOpsRequestSpace row (replace_spaces
    skips it) but can still have WIFI/ETHERNET lines from a draft save
    where the requester filled in gear before picking an answer, so
    "held" is the union of both sources, not the row alone.

    Each card carries `space`, `display_name`, `is_assigned`,
    `shared_with`, `answer_row` (the TechOpsRequestSpace row, or None for
    an unanswered card), and the fields the card template redisplays:
    `answer`, `no_services_reason`, `wifi_requested` (True/False/None, a
    plain read of `TechOpsRequestSpace.wifi_requested`), `wifi_declined_reason`,
    `wifi_description`, `drops`, `phone_lines`, `notes`, `wifi_forced`, the
    collapsed-badge counts `ethernet_count`, `phone_line_count`, and
    `phone_handset_count` (see count_filled_drops / count_filled_phone),
    and `ethernet_description` / `phone_description` (see
    collapsible_descriptions), the catalog sentence each section's closed
    summary shows.
    """
    assigned_entries = spaces_for_department(department_id, event_cycle.id)
    assigned_ids = {entry["space"].id for entry in assigned_entries}
    # A slice or fold member an assigned entry already covers must not also
    # surface as its own independent, unassigned card.
    covered_ids = {child.id for entry in assigned_entries
                  for child in entry["covers"]}

    # An offerable space that is neither assigned nor already on this
    # request is a picker candidate (picker_candidates(), below), not
    # an automatic card. Only a space this request already holds — a
    # TechOpsRequestSpace row, or a line referencing it — is added here
    # alongside the assigned set. That keeps showing a space picked on an
    # earlier save even after it stops being offerable (archived, or moved
    # off the venue), the same as before this task.
    held_ids: set[int] = set()
    if work_item is not None:
        held_ids |= {row.space_id for row in work_item.techops_spaces}
        held_ids |= {
            line.techops_detail.space_id
            for line in work_item.lines
            if line.techops_detail is not None
            and line.techops_detail.space_id is not None
        }
    held_extra_ids = held_ids - assigned_ids - covered_ids

    held_extra_spaces = []
    if held_extra_ids:
        held_extra_spaces = (
            db.session.query(Space)
            .filter(Space.id.in_(held_extra_ids))
            .order_by(Space.sort_order, Space.code)
            .all()
        )

    entries = (
        list(assigned_entries)
        + [{"space": space, "display_name": space.name}
           for space in held_extra_spaces]
    )
    all_ids = [entry["space"].id for entry in entries]
    shared = shared_with(all_ids, event_cycle.id, department_id)
    extras = _redisplay_extras(work_item)
    answer_rows = ({row.space_id: row for row in work_item.techops_spaces}
                  if work_item is not None else {})
    no_extras = {"wifi_description": "", "drops": (), "phone_lines": ()}
    descriptions = collapsible_descriptions()

    cards = []
    for entry in entries:
        space = entry["space"]
        row = answer_rows.get(space.id)
        extra = extras.get(space.id, no_extras)
        # A plain read of the stored column, not an inference: earlier
        # versions guessed "declined" from a non-empty wifi_declined_reason
        # and "needed" from a WIFI line's presence, which left a decline
        # typed with no reason indistinguishable from an untouched card.
        wifi_requested = row.wifi_requested if row is not None else None
        answer = SpaceAnswer(
            space_id=space.id,
            display_name=entry["display_name"],
            # row.answer is nullable: a space added through the picker
            # persists with answer NULL until it is answered. A held row
            # and no row at all both normalize to "" here.
            answer=(row.answer or "") if row else "",
            no_services_reason=(row.no_services_reason or "") if row else "",
            wifi_requested=wifi_requested,
            wifi_declined_reason=(row.wifi_declined_reason or "") if row else "",
            wifi_description=extra["wifi_description"],
            ethernet_drops=extra["drops"],
            phone_lines=extra["phone_lines"],
            notes=(row.notes or "") if row else "",
        )
        phone_line_count, phone_handset_count = count_filled_phone(answer.phone_lines)
        cards.append({
            "space": space,
            "display_name": entry["display_name"],
            "is_assigned": space.id in assigned_ids,
            "shared_with": shared.get(space.id, []),
            "answer_row": row,
            "answer": answer.answer,
            "no_services_reason": answer.no_services_reason,
            "wifi_requested": answer.wifi_requested,
            "wifi_declined_reason": answer.wifi_declined_reason,
            "wifi_description": answer.wifi_description,
            "drops": answer.ethernet_drops,
            "ethernet_count": count_filled_drops(answer.ethernet_drops),
            "ethernet_description": descriptions.get("ETHERNET", ""),
            "phone_lines": answer.phone_lines,
            "phone_line_count": phone_line_count,
            "phone_handset_count": phone_handset_count,
            "phone_description": descriptions.get("PHONE_NUMBER", ""),
            "notes": answer.notes,
            "wifi_forced": wifi_is_forced(answer),
        })
    return cards


def redisplay_cards(cards: list[dict], answers,
                    offerable_by_id: dict[int, Space] | None = None) -> list[dict]:
    """Overlay a just-posted answer onto card identity, adding a card when new.

    A card's `space`, `display_name`, `is_assigned`, and `shared_with` come
    from the database and do not change based on what was typed. Its
    answer fields must come from what the requester just posted, not from
    the last saved draft, or a save would silently discard their typing.
    `_space_card.html` renders only from `card`, so this merge happens
    before the template ever sees the data (create.py's and edit.py's
    error-redisplay branches call it in place of the raw `cards` list;
    edit.py's add-space branch also calls it, though `cards` already
    carries the just-picked space by then — see below).

    `offerable_by_id` covers a posted space with no card to overlay onto.
    This used to be the normal case for a just-picked, unanswered space
    (it wrote no TechOpsRequestSpace row). Now that `answer` is nullable
    and `replace_spaces` persists every space, that only still happens on
    a validation failure, where nothing has been committed yet and
    `answers` is the only place the pick exists. Omit the argument on a
    call site that never adds a new space and a posted id with no
    matching card is silently dropped, same as before this parameter
    existed.

    `answers` is None on a plain GET, where `cards` already reflects the
    only data there is; this then returns `cards` unchanged.
    """
    if answers is None:
        return cards
    posted_by_id = {a.space_id: a for a in answers.spaces}
    known_ids: set[int] = set()
    merged = []
    descriptions = collapsible_descriptions()
    for card in cards:
        known_ids.add(card["space"].id)
        posted = posted_by_id.get(card["space"].id)
        if posted is None:
            merged.append(card)
            continue
        posted_line_count, posted_handset_count = count_filled_phone(posted.phone_lines)
        merged.append({
            **card,
            "answer": posted.answer,
            "no_services_reason": posted.no_services_reason,
            "wifi_requested": posted.wifi_requested,
            "wifi_declined_reason": posted.wifi_declined_reason,
            "wifi_description": posted.wifi_description,
            "drops": posted.ethernet_drops,
            "ethernet_count": count_filled_drops(posted.ethernet_drops),
            "phone_lines": posted.phone_lines,
            "phone_line_count": posted_line_count,
            "phone_handset_count": posted_handset_count,
            "notes": posted.notes,
            "wifi_forced": wifi_is_forced(posted),
        })

    for space_id, posted in posted_by_id.items():
        if space_id in known_ids or not offerable_by_id:
            continue
        space = offerable_by_id.get(space_id)
        if space is None:
            continue
        new_line_count, new_handset_count = count_filled_phone(posted.phone_lines)
        merged.append({
            "space": space,
            "display_name": posted.display_name,
            "is_assigned": False,
            "shared_with": [],
            "answer_row": None,
            "answer": posted.answer,
            "no_services_reason": posted.no_services_reason,
            "wifi_requested": posted.wifi_requested,
            "wifi_declined_reason": posted.wifi_declined_reason,
            "wifi_description": posted.wifi_description,
            "drops": posted.ethernet_drops,
            "ethernet_count": count_filled_drops(posted.ethernet_drops),
            "ethernet_description": descriptions.get("ETHERNET", ""),
            "phone_lines": posted.phone_lines,
            "phone_line_count": new_line_count,
            "phone_handset_count": new_handset_count,
            "phone_description": descriptions.get("PHONE_NUMBER", ""),
            "notes": posted.notes,
            "wifi_forced": wifi_is_forced(posted),
        })
    return merged


def review_space_facts(work_item, department_id: int, event_cycle) -> dict[int, dict]:
    """What a reviewer needs to know about every space this item's lines name.

    Calls space_cards(), the same function the requester's own pages call,
    so a reviewer reads the identical name a requester saw: a folded room
    composed by compose_combined_name(), or this event's alias, never
    Space.name read directly. space_cards() already unions every space a
    line references into its card set (via held_ids), so no line's
    space_id can be missing here.

    Keyed by space id. Each entry carries `display_name` and `is_assigned`
    (whether the item's own department currently holds that space for
    this event) straight from its card, so a room Event Ops reassigns
    next week changes this on the next render with no stored flag to update.
    """
    cards = space_cards(work_item, department_id, event_cycle)
    return {
        card["space"].id: {
            "display_name": card["display_name"],
            "is_assigned": card["is_assigned"],
        }
        for card in cards
    }


def picker_candidates(cards: list[dict], offerable: list[Space]) -> list[Space]:
    """Offerable spaces not already shown as a card, in display order.

    This is what the picker offers: every offerable_spaces() entry
    space_cards() left out because the department neither holds it nor has
    already added it to this request.
    """
    shown_ids = {card["space"].id for card in cards}
    return [space for space in offerable if space.id not in shown_ids]


def phone_line_share_options(cards: list[dict]) -> list[dict]:
    """Every phone line on this request that owns a number, across all cards.

    A sharing line's `source` names one of these by `"{space_id}:{index}"`
    (see line_grain._sharing_key). `_space_card.html` offers each space's
    own entries here to every *other* card's phone-line source control, so
    a requester can point a new line at an existing one instead of getting
    a second number for the same handset. validate() and expand_to_lines()
    still own enforcing that a source resolves and shares only a real
    number; this only lists what is choosable.

    A blank, not-yet-filled-in phone line is never a candidate: entries
    come from each card's `phone_lines`, which holds only saved or
    just-posted rows, not the template's extra blank row.
    """
    options = []
    for card in cards:
        for line in card["phone_lines"]:
            if line.source == SOURCE_NEW:
                options.append({
                    "space_id": card["space"].id,
                    "display_name": card["display_name"],
                    "index": line.index,
                })
    return options
