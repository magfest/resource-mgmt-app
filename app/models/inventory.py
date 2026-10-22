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

    item = db.relationship("SupplyItem")
    current_container = db.relationship("InvContainer")
    identifiers = db.relationship("InvUnitIdentifier", backref="unit",
                                  order_by="InvUnitIdentifier.id")


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
