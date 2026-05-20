"""
Activity telemetry, notification logs, and audit models.

These models track system activity, email notifications, security events,
and configuration changes.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import BigInteger, Integer

from app import db
from .constants import NOTIF_STATUS_QUEUED, SCHED_NOTIF_STATUS_QUEUED


class ActivityEvent(db.Model):
    """High-volume access/action telemetry. Option 1 scope: log work item views/exports."""
    __tablename__ = "activity_events"

    id = db.Column(BigInteger, primary_key=True)

    occurred_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    actor_user_id = db.Column(db.String(64), nullable=True, index=True)

    work_type_id = db.Column(
        db.Integer,
        db.ForeignKey("work_types.id", name="fk_activity_events_work_type_id"),
        nullable=True,
        index=True,
    )

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_activity_events_work_item_id"),
        nullable=True,
        index=True,
    )

    work_line_id = db.Column(
        db.Integer,
        db.ForeignKey("work_lines.id", name="fk_activity_events_work_line_id"),
        nullable=True,
        index=True,
    )

    event_type = db.Column(db.String(64), nullable=False, index=True)

    # anonymized identifiers (HMAC-derived). Do not store raw IP or raw UA.
    ip_anon_id = db.Column(db.String(64), nullable=True, index=True)
    ip_net_anon_id = db.Column(db.String(64), nullable=True, index=True)
    ua_anon_id = db.Column(db.String(64), nullable=True, index=True)

    correlation_id = db.Column(db.String(64), nullable=True, index=True)
    route = db.Column(db.String(128), nullable=True)
    http_method = db.Column(db.String(16), nullable=True)

    metadata_json = db.Column(db.Text, nullable=True)

    work_type = db.relationship("WorkType")
    work_item = db.relationship("WorkItem")
    work_line = db.relationship("WorkLine")

    __table_args__ = (
        # Composite index for time-series analytics queries
        db.Index("ix_activity_events_occurred_type", "occurred_at", "event_type"),
    )


class NotificationLog(db.Model):
    """Proof of notifications sent (email now, other channels later)."""
    __tablename__ = "notification_logs"

    id = db.Column(BigInteger, primary_key=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    sent_at = db.Column(db.DateTime, nullable=True, index=True)

    channel = db.Column(db.String(16), nullable=False, default="EMAIL", index=True)
    template_key = db.Column(db.String(64), nullable=False, index=True)

    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", name="fk_notification_logs_work_item_id"),
        nullable=True,
        index=True,
    )

    recipient_user_id = db.Column(db.String(64), nullable=True, index=True)
    recipient_email = db.Column(db.String(256), nullable=False, index=True)

    subject = db.Column(db.String(256), nullable=True)

    status = db.Column(db.String(16), nullable=False, default=NOTIF_STATUS_QUEUED, index=True)

    provider_message_id = db.Column(db.String(128), nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    correlation_id = db.Column(db.String(64), nullable=True, index=True)
    metadata_json = db.Column(db.Text, nullable=True)

    work_item = db.relationship("WorkItem")

    __table_args__ = (
        # Composite index for queue processing (find pending notifications)
        db.Index("ix_notification_logs_status_created", "status", "created_at"),
    )


class ConfigAuditEvent(db.Model):
    """Audit log for configuration changes (expense accounts, approval groups, etc.)."""
    __tablename__ = "config_audit_events"

    id = db.Column(db.Integer, primary_key=True)

    entity_type = db.Column(db.String(64), nullable=False, index=True)  # expense_account, approval_group, etc.
    entity_id = db.Column(db.String(64), nullable=False, index=True)  # String to support both int IDs and UUID strings

    action = db.Column(db.String(32), nullable=False, index=True)  # CREATE, UPDATE, ARCHIVE, RESTORE
    changes_json = db.Column(db.Text, nullable=True)  # JSON diff of changed fields

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_by_user_id = db.Column(db.String(64), nullable=False, index=True)

    __table_args__ = (
        db.Index("ix_config_audit_entity", "entity_type", "entity_id"),
    )


class SecurityAuditLog(db.Model):
    """Security audit log for authentication and sensitive operations."""
    __tablename__ = "security_audit_logs"

    id = db.Column(db.Integer, primary_key=True)

    # When & Who
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    user_id = db.Column(db.String(64), nullable=True, index=True)  # Null for failed logins

    # Request context
    ip_address = db.Column(db.String(45), nullable=True)  # IPv6 max length
    user_agent = db.Column(db.String(512), nullable=True)

    # Event details
    event_type = db.Column(db.String(32), nullable=False, index=True)
    event_category = db.Column(db.String(32), nullable=False, index=True)  # AUTH, ADMIN, ACCESS
    severity = db.Column(db.String(16), nullable=False, default="INFO")  # INFO, WARNING, ALERT

    # Context data (JSON)
    details = db.Column(db.Text, nullable=True)  # JSON with event-specific data

    __table_args__ = (
        db.Index("ix_security_audit_timestamp_category", "timestamp", "event_category"),
        db.Index("ix_security_audit_user_timestamp", "user_id", "timestamp"),
    )


class EmailTemplate(db.Model):
    """
    Database-backed email templates with admin editing support.

    Replaces filesystem .txt templates with editable templates stored in the database.
    Templates use Jinja2 syntax and are rendered with work_item and base_url context.
    """
    __tablename__ = "email_templates"

    id = db.Column(db.Integer, primary_key=True)

    # Unique key for template lookup (e.g., "submitted", "dispatched", "needs_attention")
    template_key = db.Column(db.String(64), nullable=False, unique=True, index=True)

    # Human-readable name for admin UI
    name = db.Column(db.String(128), nullable=False)

    # Description of when this template is used
    description = db.Column(db.Text, nullable=True)

    # Email subject line (supports Jinja2 variables)
    subject = db.Column(db.String(256), nullable=False)

    # Email body text (supports Jinja2 variables)
    body_text = db.Column(db.Text, nullable=False)

    # Whether this template is active (inactive templates won't send)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # Version tracking for optimistic locking
    version = db.Column(db.Integer, nullable=False, default=1)

    # Timestamps
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Track who last modified
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class ScheduledNotification(db.Model):
    """
    Generic time-triggered email queue.

    Lifecycle: QUEUED -> SENDING -> SENT | FAILED | SKIPPED | CANCELLED.
    Drained by a periodic job; the reaper resets stale SENDING rows back to QUEUED.
    """
    __tablename__ = "scheduled_notifications"

    # SQLite needs literal INTEGER for rowid autoincrement; Postgres gets BIGINT.
    id = db.Column(
        BigInteger().with_variant(Integer(), "sqlite"),
        primary_key=True,
    )

    template_key = db.Column(db.String(64), nullable=False, index=True)
    recipient_email = db.Column(db.String(256), nullable=False, index=True)
    recipient_user_id = db.Column(db.String(64), nullable=True, index=True)

    dispatch_at = db.Column(db.DateTime, nullable=False, index=True)

    status = db.Column(
        db.String(16), nullable=False,
        default=SCHED_NOTIF_STATUS_QUEUED, index=True,
    )

    # FKs are SET NULL so target-row cleanups don't block migrations or drains.
    work_item_id = db.Column(
        db.Integer,
        db.ForeignKey("work_items.id", ondelete="SET NULL", name="fk_sched_notif_work_item_id"),
        nullable=True, index=True,
    )
    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", ondelete="SET NULL", name="fk_sched_notif_event_cycle_id"),
        nullable=True, index=True,
    )
    work_type_id = db.Column(
        db.Integer,
        db.ForeignKey("work_types.id", ondelete="SET NULL", name="fk_sched_notif_work_type_id"),
        nullable=True, index=True,
    )
    metadata_json = db.Column(db.Text, nullable=True)

    # Idempotency: callers declare a logical identity; unique constraint blocks dupes.
    dedup_key = db.Column(db.String(256), nullable=True, unique=True, index=True)

    claimed_at = db.Column(db.DateTime, nullable=True)
    claimed_by = db.Column(db.String(64), nullable=True)

    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)

    sent_at = db.Column(db.DateTime, nullable=True)
    provider_message_id = db.Column(db.String(128), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_sched_notif_status_dispatch", "status", "dispatch_at"),
    )


class SiteContent(db.Model):
    """
    Database-backed editable content blocks for UI text.

    Used for tab descriptions, info boxes, and other admin-editable text
    that appears throughout the application.

    Content blocks are identified by a unique key and can have:
    - title: Section title (heading inside the tab)
    - content: Explanatory text (supports markdown links)
    - display_style: How to render the content (PLAIN or INFO_BOX)
    """
    __tablename__ = "site_content"

    id = db.Column(db.Integer, primary_key=True)

    # Unique key for content lookup (e.g., "budget_tab_fixed_costs", "budget_tab_hotel")
    content_key = db.Column(db.String(64), nullable=False, unique=True, index=True)

    # Human-readable name for admin UI
    name = db.Column(db.String(128), nullable=False)

    # Category for grouping in admin UI (e.g., "Budget Tabs", "Approval Flow")
    category = db.Column(db.String(64), nullable=True, index=True)

    # Section title (heading inside the tab, e.g., "Badge Requests")
    title = db.Column(db.String(128), nullable=True)

    # Content text (supports markdown links)
    content = db.Column(db.Text, nullable=True)

    # Display style: PLAIN (muted text) or INFO_BOX (blue callout)
    display_style = db.Column(db.String(16), nullable=False, default="PLAIN")

    # Timestamps
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Track who last modified
    updated_by_user_id = db.Column(db.String(64), nullable=True)
