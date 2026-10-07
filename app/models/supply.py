"""
Supply order models: categories, items, and line details.

These models support the supply/warehouse order workflow (future feature).
"""
from __future__ import annotations

from datetime import datetime

from app import db


# How sure the requester is of a line's quantity. Requesters order once a
# year, so supply ops trims "Rough guess" lines first when stock runs short.
# Codes are stored on SupplyOrderLineDetail; labels may be reworded freely.
QUANTITY_CONFIDENCE_OPTIONS = {
    "KNOWN": "I know for sure",
    "ESTIMATE": "Good estimate",
    "GUESS": "Rough guess",
}


class SupplyCategory(db.Model):
    """Categories for supply items - used for routing."""
    __tablename__ = "supply_categories"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)

    approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_supply_categories_approval_group_id"),
        nullable=True,
        index=True,
    )

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=True, default=None)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    approval_group = db.relationship("ApprovalGroup", foreign_keys=[approval_group_id])


class SupplyItem(db.Model):
    """Warehouse/supply catalog items."""
    __tablename__ = "supply_items"

    id = db.Column(db.Integer, primary_key=True)

    category_id = db.Column(
        db.Integer,
        db.ForeignKey("supply_categories.id", name="fk_supply_items_category_id"),
        nullable=False,
        index=True,
    )

    item_name = db.Column(db.String(256), nullable=False)
    unit = db.Column(db.String(32), nullable=False)  # "each", "case", "box"
    notes = db.Column(db.Text, nullable=True)
    # Short requester-facing hint shown next to the qty input at
    # decision time (e.g. "1 roll covers roughly one booth setup"),
    # aimed at once-a-year volunteers who over/under-order. Optional.
    order_guidance = db.Column(db.String(160), nullable=True)
    image_url = db.Column(db.String(512), nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_limited = db.Column(db.Boolean, nullable=False, default=False)
    is_popular = db.Column(db.Boolean, nullable=False, default=False)
    is_expendable = db.Column(db.Boolean, nullable=False, default=True)
    notes_required = db.Column(db.Boolean, nullable=False, default=False)
    internal_type = db.Column(db.String(32), nullable=True)

    unit_cost_cents = db.Column(db.Integer, nullable=True)
    qty_on_hand = db.Column(db.Integer, nullable=True)
    location_zone = db.Column(db.String(32), nullable=True)
    bin_location = db.Column(db.String(32), nullable=True)

    sort_order = db.Column(db.Integer, nullable=True, default=None)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    category = db.relationship("SupplyCategory", backref="items")


class SupplyOrderLineDetail(db.Model):
    """Supply order line details."""
    __tablename__ = "supply_order_line_details"

    work_line_id = db.Column(
        db.Integer,
        db.ForeignKey("work_lines.id", name="fk_supply_order_line_details_work_line_id"),
        primary_key=True,
    )

    item_id = db.Column(
        db.Integer,
        db.ForeignKey("supply_items.id", name="fk_supply_order_line_details_item_id"),
        nullable=False,
        index=True,
    )

    # Snapshot of routing at submission/review time
    routed_approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_supply_order_line_details_routed_approval_group_id"),
        nullable=True,
        index=True,
    )

    quantity_requested = db.Column(db.Integer, nullable=False)
    # A code from QUANTITY_CONFIDENCE_OPTIONS above. This is not the
    # budget ConfidenceLevel table; that one rates price, and supply has none.
    # NULL on a draft line, and on lines submitted before this column existed.
    quantity_confidence = db.Column(db.String(16), nullable=True)
    quantity_approved = db.Column(db.Integer, nullable=True)
    requester_notes = db.Column(db.Text, nullable=True)

    work_line = db.relationship("WorkLine", backref=db.backref("supply_detail", uselist=False, cascade="all, delete-orphan"))
    item = db.relationship("SupplyItem")
    routed_approval_group = db.relationship("ApprovalGroup", foreign_keys=[routed_approval_group_id])

    @property
    def quantity_confidence_label(self) -> str | None:
        return QUANTITY_CONFIDENCE_OPTIONS.get(self.quantity_confidence)

    __table_args__ = (
        db.Index("ix_supply_order_line_details_approval_routing", "routed_approval_group_id", "item_id"),
    )


class SupplyOrderDetail(db.Model):
    """Order-level details: one row per supply-order WorkItem.

    Pickup details live here (not per line) — requesters needing
    different pickup times place separate orders.
    """
    __tablename__ = "supply_order_details"

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_supply_order_details_work_item_id"),
        primary_key=True,
    )

    # Nullable in the DB (drafts may not have it yet); required at submit
    # by the cab's validation.
    # Stores the display string from form_utils.PICKUP_TIME_OPTIONS as a
    # snapshot — wording changes to the hardcoded list can't corrupt what
    # old orders show.
    pickup_time = db.Column(db.String(120), nullable=True)
    additional_notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    work_item = db.relationship(
        "WorkItem",
        backref=db.backref("supply_order_detail", uselist=False, cascade="all, delete-orphan"),
    )


class SupplyOrderSpace(db.Model):
    """One space a supply order will be used in.

    Supply ops plans delivery and staging from this list. Any space at the
    event's venue may be named, not only the department's assigned ones,
    because space allocation is not always right before orders open.
    """
    __tablename__ = "supply_order_spaces"

    id = db.Column(db.Integer, primary_key=True)

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_supply_order_spaces_work_item_id"),
        nullable=False,
        index=True,
    )
    space_id = db.Column(
        db.Integer,
        db.ForeignKey("spaces.id", name="fk_supply_order_spaces_space_id"),
        nullable=False,
        index=True,
    )

    # The name the requester picked, snapshotted at save. A combined space
    # shows under its event alias ("Chesapeake 4/5"); the generic line review
    # route cannot recompute that, so every page reads this column instead.
    space_label = db.Column(db.Text, nullable=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)

    work_item = db.relationship(
        "WorkItem",
        backref=db.backref(
            "supply_order_spaces",
            cascade="all, delete-orphan",
            order_by="SupplyOrderSpace.id",
        ),
    )
    space = db.relationship("Space")

    __table_args__ = (
        db.UniqueConstraint("work_item_id", "space_id",
                            name="uq_supply_order_spaces_work_item_space"),
    )
