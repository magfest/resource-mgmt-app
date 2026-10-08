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
    INV_CONDITION_IN_SERVICE, INV_IDENT_FESTOPS_LABEL, INV_OWNERSHIP_OWNED,
)


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
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


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
    is_active = db.Column(db.Boolean, nullable=False, default=True)
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
    """One code that finds a unit: a FestOps label, vendor serial, or tag.

    value keeps the text as scanned, because vendor reconciliation reports
    use the vendor's own serials. Matching uses normalized_value. Only an
    active FESTOPS_LABEL must be unique; two vendors can share a serial.
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
            "uq_inv_unit_identifiers_active_label", "normalized_value",
            unique=True,
            postgresql_where=db.text(
                f"retired_at IS NULL AND kind = '{INV_IDENT_FESTOPS_LABEL}'"),
            sqlite_where=db.text(
                f"retired_at IS NULL AND kind = '{INV_IDENT_FESTOPS_LABEL}'"),
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
    quantity = db.Column(db.Integer, nullable=False, default=0)
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


class InvBadgeSession(db.Model):
    """One volunteer's shift on one shared device.

    Attendee data boundary: badge_code resolves to an attendee in Ubersystem.
    Do not log it or put it in a URL.
    """
    __tablename__ = "inv_badge_sessions"

    id = _bigint_pk()
    device_id = db.Column(
        db.Integer, db.ForeignKey("inv_devices.id", name="fk_inv_badge_sessions_device_id"),
        nullable=False, index=True,
    )
    badge_code = db.Column(db.String(16), nullable=False)
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
    badge_session_id = db.Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        db.ForeignKey("inv_badge_sessions.id", name="fk_inv_counts_badge_session_id"),
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

    item = db.relationship("SupplyItem")


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
    badge_session_id = db.Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        db.ForeignKey("inv_badge_sessions.id", name="fk_inv_movements_badge_session_id"),
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
    note = db.Column(db.String(256), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    item = db.relationship("SupplyItem")
    unit = db.relationship("InvUnit")
    subject_container = db.relationship("InvContainer", foreign_keys=[subject_container_id])
    from_container = db.relationship("InvContainer", foreign_keys=[from_container_id])
    to_container = db.relationship("InvContainer", foreign_keys=[to_container_id])
    badge_session = db.relationship("InvBadgeSession")
    device = db.relationship("InvDevice")

    __table_args__ = (
        db.CheckConstraint("quantity IS NULL OR quantity > 0",
                           name="ck_inv_movements_quantity_positive"),
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
