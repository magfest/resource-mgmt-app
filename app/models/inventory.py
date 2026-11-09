"""FestOps inventory: where stock is and how it moved.

Items are SupplyItem rows; this module adds no item table. InvMovement is the
record of truth and is append-only. InvStock is a cached balance written in
the same transaction as each movement that changes it. Covers FestOps-owned
stock only as of October 2026; a NULL owner_department_id means FestOps.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Integer

from app import db
from app.models.constants import (
    INV_CONDITION_IN_SERVICE, INV_IDENT_UNIQUE_KINDS, INV_OWNERSHIP_OWNED,
    INV_STATION_LOOKUP,
)

# SQL for the partial unique index on active identifiers.
_ACTIVE_UNIQUE_KIND = "retired_at IS NULL AND kind IN ({})".format(
    ", ".join(f"'{kind}'" for kind in INV_IDENT_UNIQUE_KINDS))

# A retired unit gives its name back, so only live units hold a name_key.
_LIVE_NAME_KEY = "name_key IS NOT NULL AND condition != 'RETIRED'"


def _bigint_pk():
    return db.Column(BigInteger().with_variant(Integer(), "sqlite"), primary_key=True)


class InvContainerKind(db.Model):
    """An admin-managed label for containers: pallet, gaylord, bin.

    Kinds enforce no nesting rules. Any kind may hold any other, because real
    warehouses put bins on shelves and totes in trucks. A kind in use is
    deactivated, never deleted.
    """
    __tablename__ = "inv_container_kinds"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(64), nullable=False)
    sort_order = db.Column(db.Integer, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True,
                          server_default=db.true())
    # A fixed spot (shelf, dock) lists as a Location; other kinds list as Containers.
    is_fixed = db.Column(db.Boolean, nullable=False, default=False,
                         server_default=db.false())
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint("sort_order BETWEEN 0 AND 10000",
                           name="ck_inv_container_kinds_sort_order"),
    )


class InvContainer(db.Model):
    """A physical holder: a location, truck, pallet, tote, or bin.

    Moving a pallet rewrites its parent_id only. Its bins follow because
    their location is the walk up the tree, so a pallet move writes one
    movement, not one per item on it.
    """
    __tablename__ = "inv_containers"

    id = db.Column(db.Integer, primary_key=True)
    label_code = db.Column(db.String(16), unique=True, nullable=False)
    kind_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_container_kinds.id", name="fk_inv_containers_kind_id"),
        nullable=False, index=True,
    )
    name = db.Column(db.String(128), nullable=True)
    parent_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_containers.id", name="fk_inv_containers_parent_id"),
        nullable=True, index=True,
    )
    # NULL means FestOps. Nothing reads this column yet; it exists so
    # department-owned containers need no table rewrite later.
    owner_department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="fk_inv_containers_owner_department_id"),
        nullable=True,
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True,
                          server_default=db.true())
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)

    kind = db.relationship("InvContainerKind")
    parent = db.relationship("InvContainer", remote_side="InvContainer.id",
                             backref="children")


class InvUnit(db.Model):
    """One serialized unit, such as a laptop or a whiteboard."""
    __tablename__ = "inv_units"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(
        db.Integer,
        db.ForeignKey("supply_items.id", name="fk_inv_units_item_id"),
        nullable=False, index=True,
    )
    condition = db.Column(db.String(16), nullable=False, default=INV_CONDITION_IN_SERVICE)
    ownership = db.Column(db.String(16), nullable=False, default=INV_OWNERSHIP_OWNED)
    vendor_name = db.Column(db.String(128), nullable=True)
    rental_reference = db.Column(db.String(128), nullable=True)
    current_container_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_containers.id", name="fk_inv_units_current_container_id"),
        nullable=True, index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    name = db.Column(db.String(64), nullable=True)
    # Lowercased name with spaces collapsed; the unique index runs on this.
    name_key = db.Column(db.String(64), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    # Free text carried over from the old tech exports. Kept apart from notes
    # so an import never overwrites what a person typed.
    imported_notes = db.Column(db.Text, nullable=True)

    item = db.relationship("SupplyItem")
    current_container = db.relationship("InvContainer")
    identifiers = db.relationship("InvUnitIdentifier", backref="unit",
                                  order_by="InvUnitIdentifier.id")

    __table_args__ = (
        db.Index(
            "uq_inv_units_name_key", "name_key",
            unique=True,
            postgresql_where=db.text(_LIVE_NAME_KEY),
            sqlite_where=db.text(_LIVE_NAME_KEY),
        ),
    )


class InvUnitIdentifier(db.Model):
    """One code that finds a unit: a FestOps label, MAGFest tag, serial, or MAC.

    value keeps the text as scanned, because vendor reconciliation reports
    use the vendor's own serials. Matching uses normalized_value. Only an
    active FESTOPS_LABEL or MAGFEST_TAG must be unique; two vendors can share
    a serial.
    """
    __tablename__ = "inv_unit_identifiers"

    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_units.id", name="fk_inv_unit_identifiers_unit_id"),
        nullable=False, index=True,
    )
    kind = db.Column(db.String(16), nullable=False)
    value = db.Column(db.String(128), nullable=False)
    normalized_value = db.Column(db.String(128), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    retired_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index(
            "uq_inv_unit_identifiers_active_tag", "kind", "normalized_value",
            unique=True,
            postgresql_where=db.text(_ACTIVE_UNIQUE_KIND),
            sqlite_where=db.text(_ACTIVE_UNIQUE_KIND),
        ),
    )


class InvUnitNote(db.Model):
    """One note on a unit. Notes are appended and never edited or deleted.

    note_type sets the audience. source says whether a person wrote it or it
    was copied from the old notes columns. created_by_user_id is NULL for
    copied notes.
    """
    __tablename__ = "inv_unit_notes"

    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_units.id", name="fk_inv_unit_notes_unit_id"),
        nullable=False, index=True,
    )
    note_type = db.Column(db.String(16), nullable=False)
    source = db.Column(db.String(16), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_by_user_id = db.Column(db.String(64), nullable=True)

    unit = db.relationship("InvUnit", backref="unit_notes")


class InvStock(db.Model):
    """Cached count of one non-serialized item in one container."""
    __tablename__ = "inv_stock"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(
        db.Integer,
        db.ForeignKey("supply_items.id", name="fk_inv_stock_item_id"),
        nullable=False, index=True,
    )
    container_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_containers.id", name="fk_inv_stock_container_id"),
        nullable=False, index=True,
    )
    quantity = db.Column(db.Integer, nullable=False, default=0, server_default=db.text("0"))
    # "About 200 pens" is stored as 200 and shown as approximate until an
    # exact count clears the flag.
    is_approximate = db.Column(db.Boolean, nullable=False, default=False,
                               server_default=db.false())
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    item = db.relationship("SupplyItem")
    container = db.relationship("InvContainer")

    __table_args__ = (
        db.UniqueConstraint("item_id", "container_id", name="uq_inv_stock_item_container"),
        db.CheckConstraint("quantity >= 0", name="ck_inv_stock_nonnegative"),
    )


class InvDevice(db.Model):
    """A shared scanner. A device is not a user and holds no roles."""
    __tablename__ = "inv_devices"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    enrolled_by_user_id = db.Column(
        db.String(64), db.ForeignKey("users.id", name="fk_inv_devices_enrolled_by"),
        nullable=False,
    )
    enrolled_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    revoked_by_user_id = db.Column(db.String(64), nullable=True)
    station_code = db.Column(db.String(16), nullable=False, default=INV_STATION_LOOKUP,
                             server_default=INV_STATION_LOOKUP)
    # Containers created at this station are placed here. NULL means top level.
    home_container_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_containers.id", name="fk_inv_devices_home_container_id"),
        nullable=True,
    )

    home_container = db.relationship("InvContainer")


class InvEnrollCode(db.Model):
    """A one-time code an admin creates so a shared device can enroll itself.

    Only the SHA-256 of the code is stored. The device that redeems it is
    created with the name, station and home container held here.
    """
    __tablename__ = "inv_enroll_codes"

    id = db.Column(db.Integer, primary_key=True)
    code_hash = db.Column(db.String(64), unique=True, nullable=False)
    name = db.Column(db.String(64), nullable=False)
    station_code = db.Column(db.String(16), nullable=False)
    home_container_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_containers.id", name="fk_inv_enroll_codes_home_container_id"),
        nullable=True,
    )
    created_by_user_id = db.Column(
        db.String(64), db.ForeignKey("users.id", name="fk_inv_enroll_codes_created_by"),
        nullable=False,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    device_id = db.Column(
        db.Integer, db.ForeignKey("inv_devices.id", name="fk_inv_enroll_codes_device_id"),
        nullable=True,
    )

    device = db.relationship("InvDevice", foreign_keys=[device_id])


class InvShift(db.Model):
    """One volunteer's shift on one shared device.

    The volunteer picks a name; no badge code is read or stored. person_name
    is personal data shown in the movement log; do not export it outside
    inventory screens and reports.
    """
    __tablename__ = "inv_shifts"

    id = _bigint_pk()
    device_id = db.Column(
        db.Integer, db.ForeignKey("inv_devices.id", name="fk_inv_shifts_device_id"),
        nullable=False, index=True,
    )
    person_name = db.Column(db.String(64), nullable=False)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_activity_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime, nullable=True)
    end_reason = db.Column(db.String(16), nullable=True)

    device = db.relationship("InvDevice")


class InvCount(db.Model):
    """One counting pass over one container."""
    __tablename__ = "inv_counts"

    id = db.Column(db.Integer, primary_key=True)
    container_id = db.Column(
        db.Integer, db.ForeignKey("inv_containers.id", name="fk_inv_counts_container_id"),
        nullable=False, index=True,
    )
    user_id = db.Column(db.String(64), nullable=True)
    shift_id = db.Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        db.ForeignKey("inv_shifts.id", name="fk_inv_counts_shift_id"),
        nullable=True,
    )
    device_id = db.Column(
        db.Integer, db.ForeignKey("inv_devices.id", name="fk_inv_counts_device_id"),
        nullable=True,
    )
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime, nullable=True)

    container = db.relationship("InvContainer")
    lines = db.relationship("InvCountLine", backref="count", order_by="InvCountLine.id")


class InvCountLine(db.Model):
    """Expected and counted quantity of one item in one count."""
    __tablename__ = "inv_count_lines"

    id = db.Column(db.Integer, primary_key=True)
    count_id = db.Column(
        db.Integer, db.ForeignKey("inv_counts.id", name="fk_inv_count_lines_count_id"),
        nullable=False, index=True,
    )
    item_id = db.Column(
        db.Integer, db.ForeignKey("supply_items.id", name="fk_inv_count_lines_item_id"),
        nullable=False,
    )
    expected_quantity = db.Column(db.Integer, nullable=False)
    counted_quantity = db.Column(db.Integer, nullable=True)
    outcome = db.Column(db.String(16), nullable=True)
    is_approximate = db.Column(db.Boolean, nullable=False, default=False,
                               server_default=db.false())
    discarded_quantity = db.Column(db.Integer, nullable=True)
    discard_reason = db.Column(db.String(16), nullable=True)

    item = db.relationship("SupplyItem")

    __table_args__ = (
        db.CheckConstraint("discarded_quantity IS NULL OR discarded_quantity > 0",
                           name="ck_inv_count_lines_discarded_positive"),
    )


class InvMovement(db.Model):
    """One change of stock or location. Never updated or deleted.

    Exactly one subject: a unit, a container, or an item with a quantity.
    Stock balance in a container is the sum of quantity where it is the
    destination, minus the sum where it is the source.
    """
    __tablename__ = "inv_movements"

    id = _bigint_pk()
    action = db.Column(db.String(16), nullable=False)
    item_id = db.Column(
        db.Integer, db.ForeignKey("supply_items.id", name="fk_inv_movements_item_id"),
        nullable=True, index=True,
    )
    unit_id = db.Column(
        db.Integer, db.ForeignKey("inv_units.id", name="fk_inv_movements_unit_id"),
        nullable=True, index=True,
    )
    subject_container_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_containers.id", name="fk_inv_movements_subject_container_id"),
        nullable=True, index=True,
    )
    quantity = db.Column(db.Integer, nullable=True)
    from_container_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_containers.id", name="fk_inv_movements_from_container_id"),
        nullable=True, index=True,
    )
    to_container_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_containers.id", name="fk_inv_movements_to_container_id"),
        nullable=True, index=True,
    )
    performed_by_user_id = db.Column(db.String(64), nullable=True)
    shift_id = db.Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        db.ForeignKey("inv_shifts.id", name="fk_inv_movements_shift_id"),
        nullable=True, index=True,
    )
    device_id = db.Column(
        db.Integer, db.ForeignKey("inv_devices.id", name="fk_inv_movements_device_id"),
        nullable=True, index=True,
    )
    count_id = db.Column(
        db.Integer, db.ForeignKey("inv_counts.id", name="fk_inv_movements_count_id"),
        nullable=True,
    )
    reason = db.Column(db.String(32), nullable=True)
    note = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    item = db.relationship("SupplyItem")
    unit = db.relationship("InvUnit")
    subject_container = db.relationship("InvContainer", foreign_keys=[subject_container_id])
    from_container = db.relationship("InvContainer", foreign_keys=[from_container_id])
    to_container = db.relationship("InvContainer", foreign_keys=[to_container_id])
    shift = db.relationship("InvShift")
    device = db.relationship("InvDevice")

    __table_args__ = (
        db.CheckConstraint("quantity IS NULL OR quantity > 0",
                           name="ck_inv_movements_quantity_positive"),
    )


class InvCheckItem(db.Model):
    """One yes/no line on the check for a model."""
    __tablename__ = "inv_check_items"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(
        db.Integer, db.ForeignKey("supply_items.id", name="fk_inv_check_items_item_id"),
        nullable=False, index=True,
    )
    position = db.Column(db.Integer, nullable=False, default=0, server_default=db.text("0"))
    prompt = db.Column(db.String(128), nullable=False)
    # FAIL: a NO fails the check. NOTE: a NO passes and records the prompt.
    missing_means = db.Column(db.String(8), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True,
                          server_default=db.true())

    item = db.relationship("SupplyItem")


class InvCheck(db.Model):
    """One check of one unit at one station in one event."""
    __tablename__ = "inv_checks"

    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(
        db.Integer, db.ForeignKey("inv_units.id", name="fk_inv_checks_unit_id"),
        nullable=False, index=True,
    )
    event_cycle_id = db.Column(
        db.Integer, db.ForeignKey("event_cycles.id", name="fk_inv_checks_event_cycle_id"),
        nullable=False,
    )
    station_code = db.Column(db.String(16), nullable=False)
    status = db.Column(db.String(16), nullable=False)
    performed_by_user_id = db.Column(db.String(64), nullable=True)
    shift_id = db.Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        db.ForeignKey("inv_shifts.id", name="fk_inv_checks_shift_id"),
        nullable=True, index=True,
    )
    device_id = db.Column(
        db.Integer, db.ForeignKey("inv_devices.id", name="fk_inv_checks_device_id"),
        nullable=True, index=True,
    )
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)
    note = db.Column(db.String(256), nullable=True)
    # The unit's condition before this check finished; undo restores it.
    previous_condition = db.Column(db.String(16), nullable=True)

    unit = db.relationship("InvUnit")
    answers = db.relationship("InvCheckAnswer", backref="check",
                              order_by="InvCheckAnswer.id")


class InvCheckAnswer(db.Model):
    """The answer to one check line."""
    __tablename__ = "inv_check_answers"

    id = db.Column(db.Integer, primary_key=True)
    check_id = db.Column(
        db.Integer, db.ForeignKey("inv_checks.id", name="fk_inv_check_answers_check_id"),
        nullable=False, index=True,
    )
    check_item_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_check_items.id", name="fk_inv_check_answers_check_item_id"),
        nullable=False,
    )
    answer = db.Column(db.String(8), nullable=False)
    note = db.Column(db.String(256), nullable=True)

    check_item = db.relationship("InvCheckItem")

    __table_args__ = (
        db.UniqueConstraint("check_id", "check_item_id", name="uq_inv_check_answers_line"),
    )


class InvPrepStatus(db.Model):
    """One step in a unit's preparation for an event. Never updated or deleted.

    The newest row for a unit and event is its prep status. No row means the
    unit is unchecked. A prep status never records location.
    """
    __tablename__ = "inv_prep_status"

    id = _bigint_pk()
    unit_id = db.Column(
        db.Integer, db.ForeignKey("inv_units.id", name="fk_inv_prep_status_unit_id"),
        nullable=False,
    )
    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_inv_prep_status_event_cycle_id"),
        nullable=False,
    )
    status = db.Column(db.String(16), nullable=False)
    check_id = db.Column(
        db.Integer, db.ForeignKey("inv_checks.id", name="fk_inv_prep_status_check_id"),
        nullable=True,
    )
    performed_by_user_id = db.Column(db.String(64), nullable=True)
    shift_id = db.Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        db.ForeignKey("inv_shifts.id", name="fk_inv_prep_status_shift_id"),
        nullable=True, index=True,
    )
    device_id = db.Column(
        db.Integer, db.ForeignKey("inv_devices.id", name="fk_inv_prep_status_device_id"),
        nullable=True, index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    unit = db.relationship("InvUnit")
    shift = db.relationship("InvShift")

    __table_args__ = (
        db.Index("ix_inv_prep_status_unit_event", "unit_id", "event_cycle_id"),
    )


class InvFlag(db.Model):
    """A "flag for a lead" raised at a station and fixed later at a laptop."""
    __tablename__ = "inv_flags"

    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(
        db.Integer, db.ForeignKey("inv_units.id", name="fk_inv_flags_unit_id"),
        nullable=True, index=True,
    )
    container_id = db.Column(
        db.Integer, db.ForeignKey("inv_containers.id", name="fk_inv_flags_container_id"),
        nullable=True, index=True,
    )
    station_code = db.Column(db.String(16), nullable=False)
    reason = db.Column(db.String(16), nullable=False)
    note = db.Column(db.String(256), nullable=True)
    performed_by_user_id = db.Column(db.String(64), nullable=True)
    shift_id = db.Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        db.ForeignKey("inv_shifts.id", name="fk_inv_flags_shift_id"),
        nullable=True, index=True,
    )
    device_id = db.Column(
        db.Integer, db.ForeignKey("inv_devices.id", name="fk_inv_flags_device_id"),
        nullable=True, index=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolved_by_user_id = db.Column(db.String(64), nullable=True)
    resolution_note = db.Column(db.String(256), nullable=True)

    unit = db.relationship("InvUnit")
    container = db.relationship("InvContainer")

    # An unknown item found in a tote is flagged on the tote, so no flag
    # names neither subject.
    __table_args__ = (
        db.CheckConstraint(
            "(unit_id IS NOT NULL AND container_id IS NULL) OR "
            "(unit_id IS NULL AND container_id IS NOT NULL)",
            name="ck_inv_flags_one_subject"),
    )


class InvStationItem(db.Model):
    """One of "the usual items" the count station offers before search."""
    __tablename__ = "inv_station_items"

    id = db.Column(db.Integer, primary_key=True)
    station_code = db.Column(db.String(16), nullable=False)
    item_id = db.Column(
        db.Integer, db.ForeignKey("supply_items.id", name="fk_inv_station_items_item_id"),
        nullable=False,
    )
    position = db.Column(db.Integer, nullable=False, default=0, server_default=db.text("0"))

    item = db.relationship("SupplyItem")

    __table_args__ = (
        db.UniqueConstraint("station_code", "item_id", name="uq_inv_station_items_item"),
    )


class InvScanRequest(db.Model):
    """The stored answer to one scanner request, so a retry writes nothing."""
    __tablename__ = "inv_scan_requests"

    id = _bigint_pk()
    client_request_id = db.Column(db.String(64), unique=True, nullable=False)
    response_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class InvLabelSequence(db.Model):
    """Next unissued number for one label prefix."""
    __tablename__ = "inv_label_sequences"

    prefix = db.Column(db.String(4), primary_key=True)
    next_value = db.Column(db.Integer, nullable=False, default=1)


class InvPrinter(db.Model):
    """A label printer served by one print station."""
    __tablename__ = "inv_printers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    dpi = db.Column(db.Integer, nullable=False)
    width_in = db.Column(db.Numeric(4, 2), nullable=False)
    height_in = db.Column(db.Numeric(4, 2), nullable=False)
    # Free text such as "Removable"; shown to the person loading labels.
    stock = db.Column(db.String(64), nullable=True)
    device_id = db.Column(
        db.Integer, db.ForeignKey("inv_devices.id", name="fk_inv_printers_device_id"),
        nullable=True, index=True,
    )
    # The name Browser Print knows the printer by.
    device_name = db.Column(db.String(128), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True,
                          server_default=db.true())

    device = db.relationship("InvDevice")


class InvPrintRoute(db.Model):
    """Which printer takes one job type. Changing it affects new jobs only."""
    __tablename__ = "inv_print_routes"

    id = db.Column(db.Integer, primary_key=True)
    job_type = db.Column(db.String(16), unique=True, nullable=False)
    printer_id = db.Column(
        db.Integer, db.ForeignKey("inv_printers.id", name="fk_inv_print_routes_printer_id"),
        nullable=False,
    )

    printer = db.relationship("InvPrinter")


class InvPrintJob(db.Model):
    """One label, as short text. Queue and print history in one table.

    Holds no image and no ZPL; the app renders ZPL when the print station
    collects the job.
    """
    __tablename__ = "inv_print_jobs"

    id = _bigint_pk()
    printer_id = db.Column(
        db.Integer, db.ForeignKey("inv_printers.id", name="fk_inv_print_jobs_printer_id"),
        nullable=False, index=True,
    )
    job_type = db.Column(db.String(16), nullable=False)
    unit_id = db.Column(
        db.Integer, db.ForeignKey("inv_units.id", name="fk_inv_print_jobs_unit_id"),
        nullable=True,
    )
    container_id = db.Column(
        db.Integer, db.ForeignKey("inv_containers.id", name="fk_inv_print_jobs_container_id"),
        nullable=True,
    )
    code = db.Column(db.String(64), nullable=False)
    fields = db.Column(db.JSON, nullable=False)
    printed_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(16), nullable=False, default="QUEUED",
                       server_default="QUEUED", index=True)
    source = db.Column(db.String(8), nullable=False)
    batch_id = db.Column(db.String(32), nullable=True, index=True)
    client_request_id = db.Column(db.String(64), unique=True, nullable=True)
    error = db.Column(db.String(200), nullable=True)
    performed_by_user_id = db.Column(db.String(64), nullable=True)
    shift_id = db.Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        db.ForeignKey("inv_shifts.id", name="fk_inv_print_jobs_shift_id"),
        nullable=True,
    )
    device_id = db.Column(
        db.Integer, db.ForeignKey("inv_devices.id", name="fk_inv_print_jobs_device_id"),
        nullable=True,
    )
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    claimed_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    printer = db.relationship("InvPrinter")
    unit = db.relationship("InvUnit")
    container = db.relationship("InvContainer")


class InvItemTotal(db.Model):
    """A typed total for one item, for stock that is counted in bulk."""
    __tablename__ = "inv_item_totals"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(
        db.Integer, db.ForeignKey("supply_items.id", name="fk_inv_item_totals_item_id"),
        nullable=False, unique=True,
    )
    quantity = db.Column(db.Integer, nullable=False)
    set_by_user_id = db.Column(db.String(64), nullable=True)
    set_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    item = db.relationship("SupplyItem")


class InvGearKind(db.Model):
    """A family of gear that shares typed fields, such as TV or printer."""
    __tablename__ = "inv_gear_kinds"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False)
    name = db.Column(db.String(64), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0, server_default=db.text("0"))
    is_active = db.Column(db.Boolean, nullable=False, default=True,
                          server_default=db.true())

    fields = db.relationship("InvField", backref="gear_kind",
                             order_by="InvField.position")


class InvField(db.Model):
    """One typed field on a gear kind. field_type is an INV_FIELD_TYPES value."""
    __tablename__ = "inv_fields"

    id = db.Column(db.Integer, primary_key=True)
    gear_kind_id = db.Column(
        db.Integer, db.ForeignKey("inv_gear_kinds.id", name="fk_inv_fields_gear_kind_id"),
        nullable=False, index=True,
    )
    code = db.Column(db.String(32), nullable=False)
    name = db.Column(db.String(64), nullable=False)
    field_type = db.Column(db.String(8), nullable=False)
    unit_label = db.Column(db.String(16), nullable=True)
    position = db.Column(db.Integer, nullable=False, default=0, server_default=db.text("0"))
    is_active = db.Column(db.Boolean, nullable=False, default=True,
                          server_default=db.true())
    # on_label puts the value in the label's spec line; a BOOL prints label_text on Yes.
    on_label = db.Column(db.Boolean, nullable=False, default=False,
                         server_default=db.false())
    label_text = db.Column(db.String(16), nullable=True)

    choices = db.relationship("InvFieldChoice", backref="field",
                              order_by="InvFieldChoice.position")

    __table_args__ = (
        db.UniqueConstraint("gear_kind_id", "code", name="uq_inv_fields_kind_code"),
    )


class InvFieldChoice(db.Model):
    """One allowed value of a CHOICE field."""
    __tablename__ = "inv_field_choices"

    id = db.Column(db.Integer, primary_key=True)
    field_id = db.Column(
        db.Integer, db.ForeignKey("inv_fields.id", name="fk_inv_field_choices_field_id"),
        nullable=False, index=True,
    )
    label = db.Column(db.String(32), nullable=False)
    # The short form printed on labels; the label prints `label` when this is null.
    label_text = db.Column(db.String(16), nullable=True)
    position = db.Column(db.Integer, nullable=False, default=0, server_default=db.text("0"))
    is_active = db.Column(db.Boolean, nullable=False, default=True,
                          server_default=db.true())

    __table_args__ = (
        db.UniqueConstraint("field_id", "label", name="uq_inv_field_choices_field_label"),
    )


class InvUnitValue(db.Model):
    """A unit's value for one field. Only the column matching the field type is set."""
    __tablename__ = "inv_unit_values"

    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(
        db.Integer, db.ForeignKey("inv_units.id", name="fk_inv_unit_values_unit_id"),
        nullable=False, index=True,
    )
    field_id = db.Column(
        db.Integer, db.ForeignKey("inv_fields.id", name="fk_inv_unit_values_field_id"),
        nullable=False,
    )
    choice_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_field_choices.id", name="fk_inv_unit_values_choice_id"),
        nullable=True,
    )
    number_value = db.Column(db.Numeric(8, 2), nullable=True)
    bool_value = db.Column(db.Boolean, nullable=True)
    text_value = db.Column(db.String(128), nullable=True)

    unit = db.relationship("InvUnit", backref="values")
    field = db.relationship("InvField")
    choice = db.relationship("InvFieldChoice")

    __table_args__ = (
        db.UniqueConstraint("unit_id", "field_id", name="uq_inv_unit_values_unit_field"),
    )


class InvModelDefault(db.Model):
    """A default field value for every new unit of one item (model)."""
    __tablename__ = "inv_model_defaults"

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(
        db.Integer, db.ForeignKey("supply_items.id", name="fk_inv_model_defaults_item_id"),
        nullable=False, index=True,
    )
    field_id = db.Column(
        db.Integer, db.ForeignKey("inv_fields.id", name="fk_inv_model_defaults_field_id"),
        nullable=False,
    )
    choice_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_field_choices.id", name="fk_inv_model_defaults_choice_id"),
        nullable=True,
    )
    number_value = db.Column(db.Numeric(8, 2), nullable=True)
    bool_value = db.Column(db.Boolean, nullable=True)
    text_value = db.Column(db.String(128), nullable=True)

    item = db.relationship("SupplyItem")
    field = db.relationship("InvField")
    choice = db.relationship("InvFieldChoice")

    __table_args__ = (
        db.UniqueConstraint("item_id", "field_id", name="uq_inv_model_defaults_item_field"),
    )


class InvTagLink(db.Model):
    """The record that a MAGFest tag was attached to a unit; undone_at marks an undo."""
    __tablename__ = "inv_tag_links"

    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(
        db.Integer, db.ForeignKey("inv_units.id", name="fk_inv_tag_links_unit_id"),
        nullable=False, index=True,
    )
    identifier_id = db.Column(
        db.Integer,
        db.ForeignKey("inv_unit_identifiers.id", name="fk_inv_tag_links_identifier_id"),
        nullable=False,
    )
    shift_id = db.Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        db.ForeignKey("inv_shifts.id", name="fk_inv_tag_links_shift_id"),
        nullable=True,
    )
    device_id = db.Column(
        db.Integer, db.ForeignKey("inv_devices.id", name="fk_inv_tag_links_device_id"),
        nullable=True,
    )
    performed_by_user_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    undone_at = db.Column(db.DateTime, nullable=True)
