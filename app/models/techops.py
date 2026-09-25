"""
TechOps-specific models: service types, line details, request details.

These models support the TechOps services workflow (network/phone/radio
channel requests). TechOps uses category routing — each TechOpsServiceType
carries a default approval group, snapshotted onto the line at submit time.
"""
from __future__ import annotations

from datetime import datetime

from app import db


class TechOpsServiceType(db.Model):
    """Catalog of TechOps service types (WIFI, ETHERNET, BANDWIDTH, PHONE, RADIO_CHANNEL, OTHER)."""
    __tablename__ = "techops_service_types"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)

    default_approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_techops_service_types_default_approval_group_id"),
        nullable=False,
        index=True,
    )

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=True, default=None)

    # When set, the New Request form renders a quantity input under this
    # service with this label as its caption (e.g. "Number of drops"). When
    # NULL the quantity field is hidden — meaningful for services where qty
    # has no requester-side semantics (WiFi coverage, bandwidth, generic
    # consultation).
    #
    # Note: as of the per-instance refactor, quantity_label is unused for
    # services that now have instance_noun set (ETHERNET/PHONE/RADIO_CHANNEL).
    # Kept on the schema for historical rows; safe to drop in a later cleanup.
    quantity_label = db.Column(db.String(64), nullable=True)

    # When set, this service is "per-instance": each WorkLine represents one
    # distinct instance (one ethernet drop, one phone, one radio channel)
    # with its own location + usage. The form renders a repeating-group
    # section with "+ Add another <instance_noun>" instead of a single
    # description box. When NULL the service is "single-line" (one
    # description per request, e.g. WiFi coverage).
    instance_noun = db.Column(db.String(32), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    default_approval_group = db.relationship("ApprovalGroup", foreign_keys=[default_approval_group_id])


class TechOpsLineDetail(db.Model):
    """TechOps-specific line details: one row per WorkLine in a TechOps request."""
    __tablename__ = "techops_line_details"

    work_line_id = db.Column(
        db.Integer,
        db.ForeignKey("work_lines.id", name="fk_techops_line_details_work_line_id"),
        primary_key=True,
    )

    service_type_id = db.Column(
        db.Integer,
        db.ForeignKey("techops_service_types.id", name="fk_techops_line_details_service_type_id"),
        nullable=False,
        index=True,
    )

    # Used by single-line services (WIFI, OTHER) — one combined text field.
    description = db.Column(db.Text, nullable=True)

    # Used by per-instance services (ETHERNET, PHONE, RADIO_CHANNEL).
    # `location` holds physical location for ETHERNET/PHONE, or the channel
    # name (preferred or assigned) for RADIO_CHANNEL — overloaded by service
    # type at the form-label layer. `usage` holds the per-instance use case.
    location = db.Column(db.Text, nullable=True)
    usage = db.Column(db.Text, nullable=True)

    # Null = boolean/yes-no semantics (most TechOps services are inherently 1)
    # As of the per-instance refactor, per-instance services use one row per
    # instance instead of bundling via quantity. Kept on schema for any
    # legacy rows; safe to drop in a later cleanup.
    quantity = db.Column(db.Integer, nullable=True)

    # Service-specific extras (e.g. {"external_callable": true} for PHONE)
    config = db.Column(db.JSON, nullable=True)

    # The room this line is installed in. NULL for department-wide lines
    # (radio, consultation) and for rows that predate the room-first form.
    space_id = db.Column(
        db.Integer,
        db.ForeignKey("spaces.id", name="fk_techops_line_details_space_id"),
        nullable=True,
        index=True,
    )

    # A DESK_PHONE points at the PHONE_NUMBER it rings. Through work_lines,
    # not through this table, because the reviewer resolves it to a line
    # number.
    parent_line_id = db.Column(
        db.Integer,
        db.ForeignKey("work_lines.id", name="fk_techops_line_details_parent_line_id"),
        nullable=True,
        index=True,
    )

    # VOICE, TEXT, or BOTH. A column rather than config JSON because it
    # gates the whole phone branch.
    purpose = db.Column(db.String(8), nullable=True)

    # No outside calls either direction. Changes provisioning.
    internal_only = db.Column(db.Boolean, nullable=False, default=False)

    # Snapshot of routing at submission/review time
    routed_approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_techops_line_details_routed_approval_group_id"),
        nullable=True,
        index=True,
    )

    # parent_line_id is a second FK to work_lines, so both relationships
    # must pin their own foreign_keys or SQLAlchemy can't tell them apart.
    work_line = db.relationship(
        "WorkLine", foreign_keys=[work_line_id],
        backref=db.backref("techops_detail", uselist=False, cascade="all, delete-orphan"),
    )
    service_type = db.relationship("TechOpsServiceType")
    routed_approval_group = db.relationship("ApprovalGroup", foreign_keys=[routed_approval_group_id])
    space = db.relationship("Space", foreign_keys=[space_id])
    parent_line = db.relationship("WorkLine", foreign_keys=[parent_line_id])

    __table_args__ = (
        db.Index("ix_techops_line_details_approval_routing", "routed_approval_group_id", "service_type_id"),
    )


class TechOpsRequestDetail(db.Model):
    """TechOps-specific request-level details: one row per WorkItem in a TechOps request."""
    __tablename__ = "techops_request_details"

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_techops_request_details_work_item_id"),
        primary_key=True,
    )

    # Asked per-request because submitter is not always the right point of contact
    primary_contact_name = db.Column(db.String(256), nullable=False)
    primary_contact_email = db.Column(db.String(256), nullable=False)

    # True when the requester affirmed their department needs no TechOps services
    # this event. Submit synthesizes a NO_SERVICES line with space_id NULL so the
    # affirmation goes through normal review (admins verify the department
    # actually thought it through).
    no_services_needed = db.Column(db.Boolean, nullable=False, default=False)

    # Set by a reviewer when a shared space has been cleared with the other
    # department. Recorded here in pass 1; the approval block that reads it
    # lands with the order sheet.
    shared_space_confirmed = db.Column(db.Boolean, nullable=False, default=False)

    additional_notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    work_item = db.relationship("WorkItem", backref=db.backref("techops_detail", uselist=False, cascade="all, delete-orphan"))


class TechOpsRequestSpace(db.Model):
    """One space card on a TechOps request.

    Lines hold the work. This row holds the answers: which cards were
    opened, why WiFi was declined, why a space needs nothing. Without it a
    request's spaces exist only as a side effect of its lines, so a draft
    with three spaces marked and one filled in loses the other two.
    """
    __tablename__ = "techops_request_spaces"

    id = db.Column(db.Integer, primary_key=True)

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_techops_request_spaces_work_item_id"),
        nullable=False,
        index=True,
    )
    space_id = db.Column(
        db.Integer,
        db.ForeignKey("spaces.id", name="fk_techops_request_spaces_space_id"),
        nullable=False,
        index=True,
    )

    # NEEDS, NOTHING, or NULL for a card the requester has opened but not
    # yet answered. A space added through the picker has no other record
    # of being on the request, unlike an assigned space, which reappears
    # from the department's assignment list on its own. So an unanswered
    # picked card has to persist to survive a
    # reload. validate() still refuses a SUBMIT with any space this way.
    answer = db.Column(db.String(8), nullable=True)

    no_services_reason = db.Column(db.Text, nullable=True)

    # True (needed), False (declined), or NULL (never answered). Reading
    # this back is the fix for the lost-work bug: before this column
    # existed, "declined" was inferred from a non-empty
    # wifi_declined_reason, so a decline typed with no reason left no
    # evidence anywhere and reloaded as unanswered. space_cards() and
    # redisplay_cards() read this column directly; neither infers the
    # answer from line presence or from wifi_declined_reason anymore.
    wifi_requested = db.Column(db.Boolean, nullable=True)

    # Required when the requester declines WiFi on a space that invariant 2
    # forces a reason for. No WiFi line exists to carry it.
    wifi_declined_reason = db.Column(db.Text, nullable=True)

    # The per-space special request. Lives here once rather than being
    # copied onto every line in the space.
    notes = db.Column(db.Text, nullable=True)

    work_item = db.relationship(
        "WorkItem",
        backref=db.backref("techops_spaces", cascade="all, delete-orphan"),
    )
    space = db.relationship("Space")

    __table_args__ = (
        db.UniqueConstraint("work_item_id", "space_id",
                            name="uq_techops_request_spaces_item_space"),
    )
