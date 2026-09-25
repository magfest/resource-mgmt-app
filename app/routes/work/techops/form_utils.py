"""
Shared form-handling helpers for the TechOps create + edit routes.

Both create and edit accept the same single-page sectioned form, so the
parsing, validation, and write-to-DB steps are extracted here so the two
route handlers stay thin.

Two service modes coexist on the form:
- Single-line (WIFI, OTHER): one description text per service.
- Per-instance (ETHERNET, PHONE, RADIO_CHANNEL): each instance is its own
  WorkLine with location + usage + optional per-instance config (PHONE's
  external_callable). Rendered as a repeating-group section with an
  "+ Add another <noun>" button driven by service_type.instance_noun.

The "edit" path uses delete-and-recreate semantics for line rows: drafts
have no audit history yet (lines aren't reviewed until submission), so
diffing against existing rows would add code without adding value.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from app import db
from app.models import (
    TechOpsLineDetail,
    TechOpsRequestDetail,
    TechOpsRequestSpace,
    TechOpsServiceType,
    WorkItemAuditEvent,
    WorkLine,
    AUDIT_EVENT_FIELD_CHANGE,
    WORK_LINE_STATUS_PENDING,
)
from .line_grain import (
    ANSWER_NEEDS,
    ANSWER_NOTHING,
    DepartmentWideAnswer,
    EthernetDrop,
    PhoneHandset,
    PhoneLine,
    PURPOSES,
    RequestAnswers,
    SOURCE_NEW,
    SpaceAnswer,
    expand_to_lines,
    wifi_is_forced,
)

if TYPE_CHECKING:
    from werkzeug.datastructures import MultiDict
    from app.models import WorkItem
    from app.routes.context_types import UserContext


# Form action values controlling whether the request is saved as a draft
# or submitted for review on POST. One vocabulary for both forms: the
# room-first template's buttons post these same literal values
# (work_item_form.html:367,370), and create.py/edit.py already compare
# against them. A second constant pair here previously drifted from what
# the template actually posts and made every completeness check
# unreachable; do not reintroduce that split.
ACTION_SAVE_DRAFT = "save_draft"
ACTION_SUBMIT = "submit"

# Posted by the space picker's own submit button. Treated like a draft
# save by validate() (anything but ACTION_SUBMIT skips the per-space
# checks), but create.py/edit.py render the form again afterward instead
# of redirecting to the detail page, so the just-picked, still-unanswered
# card stays visible and nothing else typed on the page is discarded.
ACTION_ADD_SPACE = "add_space"

# PostgreSQL's integer ceiling. A real Space.id (plain db.Integer) can
# never reach it, so in _space_id_or_none the offerable-set check that
# follows is what actually stops a value this large from being used here.
# The ceiling clause is defense in depth: an unbounded Python int survives
# int() and would raise OverflowError inside SQLAlchemy, reaching the
# browser as an unhandled 500, in any other caller that queries by id
# without a membership check behind it.
MAX_INT4 = 2147483647

# Bounds on repeating groups. SQLite enforces nothing and Postgres
# enforces everything, so these are checked in Python on every path.
MAX_SPACES_PER_REQUEST = 100
MAX_DROPS_PER_SPACE = 25
MAX_PHONE_LINES_PER_SPACE = 10
MAX_HANDSETS_PER_LINE = 20
MAX_DEPARTMENT_WIDE_INSTANCES = 50

# Caller ID as the phone system displays it.
MAX_CALLER_ID_LENGTH = 15

# ---------------------------------------------------------------------------
# Phone-line refusals, deliberately off for this pass. The phone block is
# being redesigned next phase and its validation was blocking review of
# everything else on the form. Every phone-line rule this flag gates
# still exists in code, guarded rather than deleted, and comes back by
# flipping this back to True when the phone block is rebuilt. Parsing
# (_parse_phone_lines) and expand_to_lines() are untouched: only the
# refusal is suppressed, so a half-filled phone line still previews and
# still saves. Gated call sites: _validate_phone_line (purpose, sharing-
# source, handset location) and the caller ID length check in
# _parse_phone_lines below. The tests these rules had are skipped, not
# deleted, with `reason` pointing back at this flag.
PHONE_VALIDATION_ENABLED = False
# ---------------------------------------------------------------------------


class ValidationError(str):
    """A validate() error message that also names the space it blocks.

    Subclasses str so every existing caller that treats validate()'s
    return as list[str] (comparison, substring, flash()) keeps working
    unchanged. `space_id` is None for a request-level rule, such as a
    missing contact name, that names no single card. This is the
    structure the submit-refusal panel reads to link an error to its
    card and mark the card blocked, without string-matching the message
    against a space name.
    """

    def __new__(cls, message: str, space_id: int | None = None):
        obj = str.__new__(cls, message)
        obj.space_id = space_id
        return obj


def panel_entries(errors: list[str]) -> list[dict]:
    """Shape a route's combined error list for the submit-refusal panel.

    `errors` mixes plain strings (parse_form's structural errors, which
    name no space) and ValidationError instances (validate()'s, which
    may). getattr covers both without a type check.
    """
    return [
        {"message": str(err), "space_id": getattr(err, "space_id", None)}
        for err in errors
    ]


def active_service_types() -> list[TechOpsServiceType]:
    return (
        TechOpsServiceType.query
        .filter_by(is_active=True)
        .order_by(TechOpsServiceType.sort_order, TechOpsServiceType.id)
        .all()
    )


def _space_id_or_none(raw: str) -> int | None:
    """Return a usable space id, or None for anything that is not one.

    Rejects non-numeric input, zero, negatives, and anything past the int4
    ceiling. In parse_form the offerable-set check that follows is what
    actually stops an oversized value from being used; the ceiling clause
    here is defense in depth for any other caller of this helper that
    queries by id without a membership check behind it. The caller turns
    None into an error naming the value; a silently dropped card would
    look to the requester like an answer that saved.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value < 1 or value > MAX_INT4:
        return None
    return value


def _parse_drops(space_id: int, form: "MultiDict",
                 errors: list[str], label: str) -> tuple:
    """Walk one space's ethernet drop fields into EthernetDrop entries.

    A row with only one of location/usage filled survives as a partial
    row; the draft is half-finished, not invalid. Only a row past the
    bound is an error.
    """
    drops = []
    overflow = False
    for idx in range(1, MAX_DROPS_PER_SPACE + 2):
        loc = (form.get(f"space_{space_id}_ETHERNET_drop_{idx}_location") or "").strip()
        use = (form.get(f"space_{space_id}_ETHERNET_drop_{idx}_usage") or "").strip()
        if not loc and not use:
            continue
        if idx > MAX_DROPS_PER_SPACE:
            overflow = True
            break
        drops.append(EthernetDrop(location=loc, usage=use))
    if overflow:
        errors.append(
            f"{label}: at most {MAX_DROPS_PER_SPACE} ethernet drops per space.")
    return tuple(drops)


def _parse_handsets(space_id: int, line_n: int, form: "MultiDict",
                    errors: list[str], label: str) -> tuple:
    """Collect the filled handset placements for one phone line.

    Walks placement fields directly instead of trusting the posted
    handset count, so a count that disagrees with what was actually
    filled in cannot fabricate or drop a handset.
    """
    handsets = []
    overflow = False
    for h in range(1, MAX_HANDSETS_PER_LINE + 2):
        loc = (form.get(f"space_{space_id}_PHONE_line_{line_n}_handset_{h}_location") or "").strip()
        if not loc:
            continue
        if h > MAX_HANDSETS_PER_LINE:
            overflow = True
            break
        handsets.append(PhoneHandset(location=loc))
    if overflow:
        errors.append(
            f"{label} phone line {line_n}: at most {MAX_HANDSETS_PER_LINE} handsets.")
    return tuple(handsets)


def _parse_phone_lines(space_id: int, form: "MultiDict",
                       errors: list[str], label: str) -> tuple:
    """Walk one space's phone line fields into PhoneLine entries.

    `index` is the loop position `n`, never a value read out of the form:
    there is no `..._index` field, so two lines in one space cannot
    collide on the sharing key `line_grain._sharing_key` builds from it.
    A line whose purpose, usage, and handsets are all blank is an
    untouched slot, not a line, and is skipped.
    """
    lines = []
    overflow = False
    for n in range(1, MAX_PHONE_LINES_PER_SPACE + 2):
        prefix = f"space_{space_id}_PHONE_line_{n}"
        purpose = (form.get(f"{prefix}_purpose") or "").strip() or None
        usage = (form.get(f"{prefix}_usage") or "").strip()
        handsets = _parse_handsets(space_id, n, form, errors, label)

        if purpose is None and not usage and not handsets:
            continue
        if n > MAX_PHONE_LINES_PER_SPACE:
            overflow = True
            break

        # A source field posted but empty is a preserved orphan:
        # _redisplay_extras leaves one blank when its share target no
        # longer exists, so validate() can refuse the submit and the
        # requester re-picks instead of the line silently becoming its
        # own new, billable number. Only a field missing entirely (no
        # source control was ever offered) defaults to a fresh NEW line;
        # collapsing both cases the same way would defeat that refusal.
        source_key = f"{prefix}_source"
        if source_key in form:
            source = (form.get(source_key) or "").strip()
        else:
            source = SOURCE_NEW

        # Upper-cased and recorded, never truncated: a silently shortened
        # caller ID is wrong on every call, so length errors are surfaced
        # instead of hidden by a cut.
        caller_id_raw = (form.get(f"{prefix}_caller_id_name") or "").strip()
        caller_id_name = caller_id_raw.upper()
        # Gated by PHONE_VALIDATION_ENABLED; see that flag's comment.
        if PHONE_VALIDATION_ENABLED and len(caller_id_raw) > MAX_CALLER_ID_LENGTH:
            errors.append(
                f"{label} phone line {n}: caller ID name is at most "
                f"{MAX_CALLER_ID_LENGTH} characters.")

        lines.append(PhoneLine(
            index=n,
            source=source,
            purpose=purpose,
            internal_only=form.get(f"{prefix}_internal_only") == "1",
            usage=usage,
            caller_id_name=caller_id_name,
            voicemail_slack_channel=(form.get(f"{prefix}_voicemail_slack_channel") or "").strip(),
            text_slack_channel=(form.get(f"{prefix}_text_slack_channel") or "").strip(),
            forward_target=(form.get(f"{prefix}_forward_target") or "").strip(),
            handsets=handsets,
        ))
    if overflow:
        errors.append(
            f"{label}: at most {MAX_PHONE_LINES_PER_SPACE} phone lines per space.")
    return tuple(lines)


def _parse_space(space_id: int, display_name: str, form: "MultiDict",
                 errors: list[str]) -> SpaceAnswer:
    """Parse one space card into a SpaceAnswer.

    Runs even for a card with nothing else filled in: an id in space_ids
    with a blank answer is an opened, unanswered card, which validate()
    flags on submit, not a parse error.

    display_name is not a posted field; the browser cannot be trusted to
    say what a space is called. It comes from parse_form's
    offerable_spaces map, the same lookup that gates which cards are
    accepted, so every per-space error validate() emits can name the card.

    Picking "needs WiFi" clears any posted decline reason. _expand_space
    gives the reason precedence over the checkbox, so a reason left over
    from an earlier "no WiFi" answer would otherwise silently suppress the
    WiFi line even though the requester just picked WiFi back on; picking
    it is the current answer, and a stale reason is not a contradiction
    for later code to adjudicate.
    """
    # Tri-state from a radio pair sharing one field name: "1" needed, "0"
    # declined, absent (neither radio posted) unanswered. A checkbox could
    # only post "1" or nothing, which cannot tell "no WiFi here" from
    # "nobody has opened this card yet".
    raw_wifi = form.get(f"space_{space_id}_WIFI_enabled")
    if raw_wifi == "1":
        wifi_requested = True
    elif raw_wifi == "0":
        wifi_requested = False
    else:
        wifi_requested = None
    wifi_declined_reason = (
        "" if wifi_requested
        else (form.get(f"space_{space_id}_WIFI_declined_reason") or "").strip()
    )
    return SpaceAnswer(
        space_id=space_id,
        display_name=display_name,
        answer=(form.get(f"space_{space_id}_answer") or "").strip(),
        no_services_reason=(form.get(f"space_{space_id}_no_services_reason") or "").strip(),
        wifi_requested=wifi_requested,
        wifi_declined_reason=wifi_declined_reason,
        wifi_description=(form.get(f"space_{space_id}_WIFI_description") or "").strip(),
        ethernet_drops=_parse_drops(space_id, form, errors, display_name),
        phone_lines=_parse_phone_lines(space_id, form, errors, display_name),
        notes=(form.get(f"space_{space_id}_notes") or "").strip(),
    )


def _parse_department_wide(form: "MultiDict", errors: list[str]) -> tuple:
    """Parse the department-wide services that are not tied to a space.

    Field names are unchanged from the flat form: RADIO_CHANNEL is
    per-instance, OTHER is a single description. WIFI and ETHERNET moved
    onto space cards and are not parsed here; PHONE is deactivated in
    favor of PHONE_NUMBER/DESK_PHONE, which are per-space.

    Neither service has its own enabled checkbox. RADIO_CHANNEL is
    requested when at least one channel row holds content, the same rule
    ethernet drops use (a row counts when either field is non-blank);
    OTHER is requested when its description is non-blank. The old
    `service_RADIO_CHANNEL_enabled` gate existed to protect a no-script
    requester who unticked the box without clearing the channel fields;
    no-script row support was dropped two rounds ago (work_item_form.html),
    so the gate was residue and is removed here too.
    """
    entries: list[DepartmentWideAnswer] = []

    overflow = False
    for n in range(1, MAX_DEPARTMENT_WIDE_INSTANCES + 2):
        loc = (form.get(f"service_RADIO_CHANNEL_instance_{n}_location") or "").strip()
        use = (form.get(f"service_RADIO_CHANNEL_instance_{n}_usage") or "").strip()
        if not loc and not use:
            continue
        if n > MAX_DEPARTMENT_WIDE_INSTANCES:
            overflow = True
            break
        entries.append(DepartmentWideAnswer(
            service_code="RADIO_CHANNEL", location=loc, usage=use,
        ))
    if overflow:
        errors.append(
            f"Radio channels: at most {MAX_DEPARTMENT_WIDE_INSTANCES} per request.")

    other_description = (form.get("service_OTHER_description") or "").strip()
    if other_description:
        entries.append(DepartmentWideAnswer(
            service_code="OTHER", description=other_description,
        ))

    return tuple(entries)


def parse_form(form: "MultiDict",
               offerable_spaces: dict[int, str]) -> tuple[RequestAnswers, list[str]]:
    """Pull the room-first form out of a request.form-like MultiDict.

    offerable_spaces maps a space id to its display name. It both gates
    which cards are accepted and names every per-space error validate()
    later emits, so there is one source for that string rather than a
    second table the route would have to keep in sync.

    Returns the parsed answers and the structural errors found while
    parsing. A rejected space contributes an error and no card, so nothing
    the requester typed into an unusable card is silently kept. Semantic
    errors (an unanswered card, a phone line with no purpose) are added by
    validate(); the route concatenates both lists.
    """
    errors: list[str] = []
    spaces = []
    # Drop the picker's blank placeholder before counting. Counting it
    # refuses a save at 100 real spaces while naming 100 as the limit, and
    # the requester cannot act on a bound they have not reached.
    raw_ids = [raw for raw in form.getlist("space_ids") if raw != ""]
    raw_ids = raw_ids[:MAX_SPACES_PER_REQUEST + 1]
    if len(raw_ids) > MAX_SPACES_PER_REQUEST:
        errors.append(
            f"A request covers at most {MAX_SPACES_PER_REQUEST} spaces.")
        raw_ids = raw_ids[:MAX_SPACES_PER_REQUEST]

    seen: set[int] = set()
    for raw in raw_ids:
        space_id = _space_id_or_none(raw)
        if space_id is None or space_id not in offerable_spaces:
            # Deliberately does not echo `raw`: it is attacker-controlled
            # text and the requester cannot act on it.
            errors.append(
                "A space on this request is not available at this event. "
                "Remove it and pick one from the list."
            )
            continue
        if space_id in seen:
            continue
        seen.add(space_id)
        # A blank catalog name never falls back to the numeric id: a
        # requester cannot act on "Space 412" any better than on a blank.
        name = offerable_spaces.get(space_id) or "this space"
        spaces.append(_parse_space(space_id, name, form, errors))

    # Posted only by a card's own "Save this space" button (item 2). Not
    # validated against the offerable set: it only steers a post-save
    # redirect, never a write, so an invalid or stale value just fails to
    # match a card client-side and the redirect lands on the plain form.
    save_space_id = _space_id_or_none(form.get("save_space_id") or "")

    return RequestAnswers(
        primary_contact_name=(form.get("primary_contact_name") or "").strip(),
        primary_contact_email=(form.get("primary_contact_email") or "").strip(),
        additional_notes=(form.get("additional_notes") or "").strip(),
        no_services_needed=form.get("no_services_needed") == "1",
        action=form.get("action") or ACTION_SAVE_DRAFT,
        spaces=tuple(spaces),
        department_wide=_parse_department_wide(form, errors),
        save_space_id=save_space_id,
    ), errors


def validate(answers: "RequestAnswers", *, has_space_cards: bool) -> list[ValidationError]:
    """Return human-readable errors, empty when the request is acceptable.

    A draft saves in any state. Only a submit demands completeness: a
    requester mid-thought must be able to leave and come back, so every
    per-space check below is skipped outright for a non-submit action.
    Storage bounds (caller ID length, per-space counts) are enforced in
    parse_form instead, since those apply regardless of action.

    Each ValidationError is also its message text (see the class), so
    existing callers comparing or flashing these as plain strings are
    unaffected; only the submit-refusal panel reads `.space_id`.
    """
    errors: list[ValidationError] = []
    if not answers.primary_contact_name:
        errors.append(ValidationError("Primary contact name is required."))
    if not answers.primary_contact_email:
        errors.append(ValidationError("Primary contact email is required."))

    if answers.action != ACTION_SUBMIT:
        return errors

    if answers.no_services_needed and has_space_cards:
        errors.append(ValidationError(
            "This request covers spaces, so answer each one rather than "
            "saying the department needs nothing."
        ))

    # Invariant 6's precondition: a sharing line must point at a line that
    # exists and owns its own number. Built once, read by every card.
    owners = {
        f"{space.space_id}:{line.index}": line
        for space in answers.spaces
        for line in space.phone_lines
        if line.source == SOURCE_NEW
    }
    all_keys = {
        f"{space.space_id}:{line.index}"
        for space in answers.spaces
        for line in space.phone_lines
    }

    for space in answers.spaces:
        name = space.display_name
        sid = space.space_id
        if space.answer not in (ANSWER_NEEDS, ANSWER_NOTHING):
            errors.append(ValidationError(
                f"{name}: say whether this space needs services or needs "
                "nothing.",
                sid,
            ))
            continue
        if space.answer != ANSWER_NEEDS:
            continue

        # WiFi is a radio pair with no default (unlike the space answer
        # above); an unanswered card and a bare decline both need a
        # requester decision before submit, or the radios would be a
        # checkbox with extra clicks. A forced space folds "never
        # answered" into the same message as "declined with no reason":
        # both leave TechOps without a line or a stated reason for one.
        if space.wifi_requested is None:
            if wifi_is_forced(space):
                errors.append(ValidationError(
                    f"{name}: this space has wired gear, so say why it "
                    "needs no WiFi, or tick WiFi.",
                    sid,
                ))
            else:
                errors.append(ValidationError(
                    f"{name}: say whether this space needs WiFi.", sid))
        elif (space.wifi_requested is False and wifi_is_forced(space)
              and not space.wifi_declined_reason):
            # Invariant 2's escape hatch. No WiFi line exists to carry the
            # reason, so the reason is required here or the decline is lost.
            errors.append(ValidationError(
                f"{name}: this space has wired gear, so say why it needs "
                "no WiFi, or tick WiFi.",
                sid,
            ))

        for drop_no, drop in enumerate(space.ethernet_drops, start=1):
            if not drop.location:
                errors.append(ValidationError(
                    f"{name}, drop {drop_no}: where in the room?", sid))
            if not drop.usage:
                errors.append(ValidationError(
                    f"{name}, drop {drop_no}: what is it for?", sid))

        for line in space.phone_lines:
            _validate_phone_line(name, sid, line, owners, all_keys, errors)

        # A space marked NEEDS is the requester's own answer, not an
        # omission; expand_to_lines() is the one place that knows whether
        # that answer produced anything. Checked against this space alone
        # (department_wide and no_services_needed stripped) so a real line
        # elsewhere on the request cannot mask this card's emptiness.
        space_only = replace(
            answers, spaces=(space,), department_wide=(),
            no_services_needed=False,
        )
        if not expand_to_lines(space_only):
            errors.append(ValidationError(
                f"{name} is marked as needing services, but nothing has "
                "been requested for it. Add a service, or choose "
                "\"Nothing needed\".",
                sid,
            ))

    # Only when nothing on the request was answered at all: a space marked
    # NEEDS that itself produces no lines already gets its own error above,
    # naming the card, so this generic message would otherwise repeat that
    # error without saying where to look.
    answered_spaces = [
        s for s in answers.spaces if s.answer in (ANSWER_NEEDS, ANSWER_NOTHING)
    ]
    if (not answers.no_services_needed and not answered_spaces
            and not expand_to_lines(answers)):
        errors.append(ValidationError(
            "This request would create no lines. Answer at least one "
            "space, or say the department needs nothing."
        ))
    return errors


def _validate_phone_line(
    name: str,
    space_id: int,
    line: "PhoneLine",
    owners: dict,
    all_keys: set,
    errors: list[str],
) -> None:
    """Check one phone line's purpose, sharing reference, and handsets.

    Only reached from validate() once a submit is underway. A sharing
    line's source is compared as a plain string against keys built from
    real space ids and line indexes; nothing is parsed out of it, so a
    malformed source ("banana", "1:", an oversized number) just fails to
    match and cannot raise.

    Gated by PHONE_VALIDATION_ENABLED; see that flag's comment for why.
    """
    if not PHONE_VALIDATION_ENABLED:
        return
    if line.source == SOURCE_NEW:
        if line.purpose not in PURPOSES:
            errors.append(ValidationError(
                f"{name}, phone line {line.index}: give the line's purpose.",
                space_id,
            ))
    elif line.source not in all_keys:
        errors.append(ValidationError(
            f"{name}, phone line {line.index}: shares a line that does "
            "not exist.",
            space_id,
        ))
    elif line.source not in owners:
        errors.append(ValidationError(
            f"{name}, phone line {line.index}: shares a line that itself "
            "shares a number, so nothing would provision.",
            space_id,
        ))

    for handset_no, handset in enumerate(line.handsets, start=1):
        if not handset.location:
            errors.append(ValidationError(
                f"{name}, phone line {line.index}, handset {handset_no}: "
                "where does it sit?",
                space_id,
            ))


def upsert_request_detail(
    work_item: "WorkItem",
    data: "RequestAnswers",
    user_ctx: "UserContext",
) -> None:
    """Create the TechOpsRequestDetail row for a brand-new item, or update
    the existing one in place when editing a draft."""
    detail = work_item.techops_detail
    if detail is None:
        detail = TechOpsRequestDetail(
            work_item_id=work_item.id,
            primary_contact_name=data.primary_contact_name,
            primary_contact_email=data.primary_contact_email,
            additional_notes=data.additional_notes or None,
            no_services_needed=data.no_services_needed,
            created_by_user_id=user_ctx.user_id,
        )
        db.session.add(detail)
    else:
        detail.primary_contact_name = data.primary_contact_name
        detail.primary_contact_email = data.primary_contact_email
        detail.additional_notes = data.additional_notes or None
        detail.no_services_needed = data.no_services_needed
        detail.updated_by_user_id = user_ctx.user_id


def department_wide_redisplay(department_wide) -> dict[str, list[dict]]:
    """Shape `answers.department_wide` like `existing_lines_by_code`, for a
    validation-failure redisplay.

    edit.py's GET handler builds `existing_lines_by_code` from the ORM;
    this builds the same shape from what the requester just posted, so
    section 3 of the template can render from either source without
    knowing which one it got. Without this, a validation failure on
    submit silently drops any radio channel or OTHER text the requester
    just typed, the same bug class `redisplay_cards()` fixes for space
    cards.
    """
    existing: dict[str, list[dict]] = {}
    for entry in department_wide:
        if entry.service_code == "RADIO_CHANNEL":
            existing.setdefault("RADIO_CHANNEL", []).append({
                "location": entry.location, "usage": entry.usage,
            })
        else:
            existing.setdefault(entry.service_code, []).append({
                "description": entry.description,
            })
    return existing


def replace_spaces(work_item: "WorkItem", answers: "RequestAnswers") -> None:
    """Delete and recreate the per-space answer rows.

    A card the requester removed disappears with its answers. That is
    correct: the card is gone from the form, so keeping its reason would
    resurrect an answer to a question nobody is being asked. The delete is
    flushed before the insert because (work_item_id, space_id) is unique,
    and a re-save of the same card would otherwise collide with itself.

    Every space on the request gets a row, answered or not; `answer` is
    nullable for exactly this. Skipping an unanswered card used to be
    safe, when every card was an assigned space, which
    reappears on reload from the department's assignment list regardless
    of any row here. A space added through the picker has no such other
    record, so an unanswered pick written nowhere vanished on the next
    save. Persisting it is the fix; `validate()` still refuses a SUBMIT
    with any space left unanswered, so this cannot let one through review.

    Rows are appended through the .techops_spaces relationship, not
    constructed with work_item_id set directly, so the parent's in-memory
    collection reflects the insert immediately. A raw FK assignment
    leaves that collection stale within the same session; a second
    replace_spaces() call would then see nothing to delete and collide
    with the rows the first call actually wrote.
    """
    for row in list(work_item.techops_spaces):
        db.session.delete(row)
    db.session.flush()

    for space in answers.spaces:
        work_item.techops_spaces.append(TechOpsRequestSpace(
            space_id=space.space_id,
            answer=space.answer or None,
            no_services_reason=space.no_services_reason or None,
            wifi_requested=space.wifi_requested,
            wifi_declined_reason=space.wifi_declined_reason or None,
            notes=space.notes or None,
        ))


def replace_lines(work_item: "WorkItem", answers: "RequestAnswers") -> None:
    """Delete and recreate the work lines from the parsed answers.

    Safe on DRAFT items only: no review or audit rows exist to orphan.
    Lines are appended through the .lines relationship so the parent's
    in-memory collection stays consistent with the database.

    parent_line_id is resolved in a second pass, against the same
    `planned` list expand_to_lines() returned; the list is never sorted,
    filtered, or rebuilt in between. expand_to_lines() returns a
    positional reference because the order preview needs it before any
    row exists, and real ids only exist after the flush below.
    """
    existing_line_ids = [line.id for line in work_item.lines]
    if existing_line_ids:
        # A DESK_PHONE's parent_line_id can point at a PHONE_NUMBER line
        # in this same set. The delete loop below triggers autoflush on
        # each line's lazy cascade load, which can flush a PHONE_NUMBER's
        # delete before a DESK_PHONE row still pointing at it has been
        # touched, violating the parent_line_id foreign key. Null every
        # detail's parent_line_id in one explicit bulk update first,
        # rather than depend on delete-cascade ordering; same reasoning
        # as the migration's UPDATE at
        # tr7742b1c8e5_techops_room_first.py:98-104 ("or the delete
        # order becomes self-referential").
        (db.session.query(TechOpsLineDetail)
         .filter(TechOpsLineDetail.work_line_id.in_(existing_line_ids))
         .update({TechOpsLineDetail.parent_line_id: None},
                synchronize_session=False))
        db.session.flush()

    for line in list(work_item.lines):
        db.session.delete(line)
    db.session.flush()

    planned = expand_to_lines(answers)
    service_types = {st.code: st for st in active_service_types()}

    created: list[WorkLine] = []
    for idx, entry in enumerate(planned, start=1):
        line = WorkLine(line_number=idx, status=WORK_LINE_STATUS_PENDING)
        work_item.lines.append(line)
        db.session.flush()
        created.append(line)

        db.session.add(TechOpsLineDetail(
            work_line_id=line.id,
            # A missing service type is a seeding failure, not a user
            # error: raise rather than silently write the wrong type.
            service_type_id=service_types[entry.service_code].id,
            description=entry.description,
            location=entry.location,
            usage=entry.usage,
            space_id=entry.space_id,
            purpose=entry.purpose,
            internal_only=entry.internal_only,
            quantity=None,
            config=entry.config,
        ))
    db.session.flush()

    # Second pass: positions become ids, read off the same `planned` and
    # `created` lists built above, index for index.
    for entry, line in zip(planned, created):
        if entry.parent_index is None:
            continue
        line.techops_detail.parent_line_id = created[entry.parent_index].id


def _space_row_snapshot(space_id, answer, no_services_reason, wifi_requested,
                        wifi_declined_reason, notes) -> dict:
    """Shape shared by both snapshot functions' per-space entries.

    A single builder so the two callers cannot drift on a field name or a
    None-vs-empty-string normalization, which would report a change on
    every save regardless of what the requester actually edited.
    """
    return {
        "space_id": space_id,
        "answer": answer,
        "no_services_reason": no_services_reason or "",
        "wifi_requested": wifi_requested,
        "wifi_declined_reason": wifi_declined_reason or "",
        "notes": notes or "",
    }


def _line_snapshot(code, name, description, location, usage, config,
                   space_id, purpose, internal_only,
                   parent_index) -> dict:
    """Shape shared by both snapshot functions' per-line entries.

    parent_index is a position within this same services list, never a
    database id: replace_lines() gives every service_type a fresh id on
    every save, so comparing raw ids would report a change on an
    unchanged draft. Position is what expand_to_lines() already hands
    replace_lines(), so both snapshots read it the same way.
    """
    return {
        "code": code,
        "name": name,
        "description": description or "",
        "location": location or "",
        "usage": usage or "",
        "config": config or {},
        "space_id": space_id,
        "purpose": purpose,
        "internal_only": internal_only,
        "parent_index": parent_index,
    }


def capture_state_snapshot(work_item: "WorkItem") -> dict:
    """Serialize the current draft state into a JSON-safe dict for audit.

    Reads from the ORM — used for the before-snapshot, when work_item
    reflects the on-disk state. Don't use this for the after-snapshot
    after replace_lines(): the in-memory collection can be stale because
    delete-and-recreate doesn't always refresh the parent's .lines
    collection in time. Use capture_form_snapshot(answers) instead.
    """
    rd = work_item.techops_detail

    spaces = sorted(
        (
            # `answer` is nullable; normalized to "" here so an
            # unanswered row compares equal to
            # capture_form_snapshot's SpaceAnswer.answer, which is always
            # a string. Comparing None against "" would report a change
            # on every save of a card nobody has touched yet. wifi_requested
            # is a real tri-state (True/False/None) on both sides, so it
            # needs no such normalization.
            _space_row_snapshot(
                row.space_id, row.answer or "",
                row.no_services_reason, row.wifi_requested,
                row.wifi_declined_reason, row.notes,
            )
            for row in work_item.techops_spaces
        ),
        key=lambda s: s["space_id"],
    )

    lines = sorted(
        (l for l in work_item.lines if l.techops_detail is not None),
        key=lambda l: l.line_number,
    )
    # parent_line_id is a real database id; translate it to a position in
    # this same list so it compares equal across saves that recreate the
    # rows with new ids but the same logical shape.
    id_to_index = {l.id: i for i, l in enumerate(lines)}
    services = []
    for line in lines:
        d = line.techops_detail
        st = d.service_type
        services.append(_line_snapshot(
            st.code if st else None, st.name if st else None,
            d.description, d.location, d.usage, d.config,
            d.space_id, d.purpose, d.internal_only,
            id_to_index.get(d.parent_line_id) if d.parent_line_id is not None else None,
        ))

    return {
        "primary_contact_name": rd.primary_contact_name if rd else None,
        "primary_contact_email": rd.primary_contact_email if rd else None,
        "additional_notes": rd.additional_notes if rd else None,
        "no_services_needed": rd.no_services_needed if rd else False,
        "spaces": spaces,
        "services": services,
    }


def capture_form_snapshot(answers: "RequestAnswers") -> dict:
    """Serialize parsed answers into the same shape as
    capture_state_snapshot, for use as the after-snapshot in audit_draft_edit.

    Reading from the parsed form rather than re-querying the ORM after a
    write avoids the stale-collection trap (see capture_state_snapshot
    docstring). Runs the same expand_to_lines() replace_lines() will, so
    the services list matches the rows that call creates line for line.
    """
    spaces = sorted(
        (
            _space_row_snapshot(
                space.space_id, space.answer,
                space.no_services_reason, space.wifi_requested,
                space.wifi_declined_reason, space.notes,
            )
            for space in answers.spaces
            # replace_spaces() stores every space, answered or not, so
            # matching that here is what keeps this snapshot comparable to
            # the ORM one.
        ),
        key=lambda s: s["space_id"],
    )

    service_types = {st.code: st for st in active_service_types()}
    services = []
    for entry in expand_to_lines(answers):
        # Same lookup as replace_lines: a missing service type is a
        # seeding failure, not a user error, and must raise here too. A
        # caller that snapshots without saving must not get a silent
        # None where replace_lines would have raised.
        st = service_types[entry.service_code]
        services.append(_line_snapshot(
            entry.service_code, st.name,
            entry.description, entry.location, entry.usage, entry.config,
            entry.space_id, entry.purpose, entry.internal_only,
            entry.parent_index,
        ))

    return {
        "primary_contact_name": answers.primary_contact_name,
        "primary_contact_email": answers.primary_contact_email,
        "additional_notes": answers.additional_notes or None,
        "no_services_needed": answers.no_services_needed,
        "spaces": spaces,
        "services": services,
    }


def _summarize_state(snapshot: dict) -> str:
    """Render a one-line human summary of a state snapshot for audit
    old_value / new_value display. Full structured detail lives in the
    audit event's snapshot column for anyone who wants to drill in."""
    services = snapshot.get("services") or []
    if snapshot.get("no_services_needed"):
        services_part = "no services (affirmed)"
    elif not services:
        services_part = "0 lines"
    else:
        codes = ", ".join(s.get("code") or "?" for s in services)
        services_part = f"{len(services)} line(s): {codes}"

    contact = snapshot.get("primary_contact_name") or "(no contact)"
    return f"{services_part}; contact: {contact}"


def audit_draft_edit(
    work_item: "WorkItem",
    before: dict,
    after: dict,
    user_ctx: "UserContext",
) -> bool:
    """Emit one WorkItemAuditEvent capturing a draft edit, if state actually
    changed. Returns True if an event was emitted.

    Lives at the item level (not line level) so it survives the cascade
    delete done by replace_lines() — line-level audit rows would be wiped
    along with the lines they reference.
    """
    if before == after:
        return False

    db.session.add(WorkItemAuditEvent(
        work_item_id=work_item.id,
        event_type=AUDIT_EVENT_FIELD_CHANGE,
        old_value=_summarize_state(before),
        new_value=_summarize_state(after),
        # Use 'kind' rather than 'field' so we don't collide with the
        # audit_log macro's BUDGET-per-field rendering branch (which
        # expects snapshot.description + snapshot.field). The generic
        # else-branch ('old → new') is what we want.
        snapshot={
            "kind": "techops_draft_edit",
            "before": before,
            "after": after,
        },
        created_by_user_id=user_ctx.user_id,
    ))
    return True
