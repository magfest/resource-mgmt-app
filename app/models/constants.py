"""
Model constants for status codes, roles, visibility modes, etc.

All constants are re-exported from app.models for backwards compatibility.
"""

# ============================================================
# Comment visibility
# ============================================================

COMMENT_VISIBILITY_PUBLIC = "PUBLIC"
COMMENT_VISIBILITY_ADMIN = "ADMIN"

# ============================================================
# Work item statuses (request header)
# ============================================================

WORK_ITEM_STATUS_DRAFT = "DRAFT"
WORK_ITEM_STATUS_AWAITING_DISPATCH = "AWAITING_DISPATCH"
WORK_ITEM_STATUS_SUBMITTED = "SUBMITTED"
WORK_ITEM_STATUS_UNDER_REVIEW = "UNDER_REVIEW"
WORK_ITEM_STATUS_FINALIZED = "FINALIZED"
WORK_ITEM_STATUS_UNAPPROVED = "UNAPPROVED"  # reopened after finalize
# Written only by line-level kickbacks as of August 2026. The request-level
# "Request Information" action also set this on the work item, but no template
# posted to its response route, so those items could never be checked out
# again; that action was removed. Item-level reads stay for historical rows in
# computations.py and admin_final/helpers.py.
WORK_ITEM_STATUS_NEEDS_INFO = "NEEDS_INFO"
WORK_ITEM_STATUS_PAUSED = "PAUSED"  # supplementary blocked while primary is unfinalized

# ============================================================
# Work line statuses (overall current state)
# ============================================================

WORK_LINE_STATUS_PENDING = "PENDING"
WORK_LINE_STATUS_NEEDS_INFO = "NEEDS_INFO"
WORK_LINE_STATUS_NEEDS_ADJUSTMENT = "NEEDS_ADJUSTMENT"
WORK_LINE_STATUS_APPROVED = "APPROVED"
WORK_LINE_STATUS_APPROVED_NEEDS_REVIEW = "APPROVED_NEEDS_REVIEW"
WORK_LINE_STATUS_REJECTED = "REJECTED"

# ============================================================
# Review stages
# ============================================================

REVIEW_STAGE_APPROVAL_GROUP = "APPROVAL_GROUP"
REVIEW_STAGE_ADMIN_FINAL = "ADMIN_FINAL"

# ============================================================
# Review decision statuses
# ============================================================

REVIEW_STATUS_PENDING = "PENDING"
REVIEW_STATUS_NEEDS_INFO = "NEEDS_INFO"
REVIEW_STATUS_NEEDS_ADJUSTMENT = "NEEDS_ADJUSTMENT"
REVIEW_STATUS_APPROVED = "APPROVED"
REVIEW_STATUS_APPROVED_NEEDS_REVIEW = "APPROVED_NEEDS_REVIEW"
REVIEW_STATUS_REJECTED = "REJECTED"

# ============================================================
# Role codes
# ============================================================

ROLE_SUPER_ADMIN = "SUPER_ADMIN"        # global admin
ROLE_WORKTYPE_ADMIN = "WORKTYPE_ADMIN"  # admin for a work type (e.g., BUDGET)
ROLE_WORKTYPE_VIEWER = "WORKTYPE_VIEWER"  # read-only on one work type; never decides
ROLE_APPROVER = "APPROVER"              # approver (typically scoped to approval group)

# Manages venues, spaces, and per-event assignments. Held by Event Ops, who
# must not hold SUPER_ADMIN. Unscoped: one team runs all venues, so the role
# sets no scope columns on user_roles and the existing partial index
# ix_user_roles_global_unique already covers it.
ROLE_SPACE_ADMIN = "SPACE_ADMIN"

# Inventory roles. Unscoped, like SPACE_ADMIN. They are not WORKTYPE_ADMIN
# because network leads scan and configure gear without administering the
# Supply catalog.
ROLE_INVENTORY_ADMIN = "INVENTORY_ADMIN"
ROLE_INVENTORY_OPERATOR = "INVENTORY_OPERATOR"

# Movement actions. A movement never changes after it is written.
INV_ACTION_ADD = "ADD"
INV_ACTION_MOVE = "MOVE"
INV_ACTION_ADJUST = "ADJUST"
INV_ACTION_DISCARD = "DISCARD"

# A unit's lasting condition across events. Per-event pipeline state is a
# separate record with a separate lifetime.
INV_CONDITION_IN_SERVICE = "IN_SERVICE"
INV_CONDITION_REPAIR = "REPAIR"
INV_CONDITION_RETIRED = "RETIRED"
INV_CONDITION_LOST = "LOST"

INV_OWNERSHIP_OWNED = "OWNED"
INV_OWNERSHIP_RENTED = "RENTED"

# FESTOPS_LABEL and MAGFEST_TAG are the kinds that must be unique among active
# identifiers. A rental's vendor serial is a SERIAL on a unit whose ownership
# is RENTED; serials repeat across vendors, so they are never unique.
INV_IDENT_FESTOPS_LABEL = "FESTOPS_LABEL"
INV_IDENT_MAGFEST_TAG = "MAGFEST_TAG"
INV_IDENT_VENDOR_TAG = "VENDOR_TAG"
INV_IDENT_SERIAL = "SERIAL"
INV_IDENT_MAC = "MAC"
INV_IDENT_UNIQUE_KINDS = (INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAGFEST_TAG)

INV_DISCARD_REASONS = ("DRIED_OUT", "BROKEN", "EXPIRED", "OTHER")

# A scanner station. Look up shows what a scan is. Count records quantities in
# a container. TV check and Printer check run a per-model checklist on one unit.
INV_STATION_LOOKUP = "LOOKUP"
INV_STATION_COUNT = "COUNT"
INV_STATION_TV_CHECK = "TV_CHECK"
INV_STATION_PRINTER_CHECK = "PRINTER_CHECK"
INV_STATIONS = (
    INV_STATION_LOOKUP, INV_STATION_COUNT, INV_STATION_TV_CHECK,
    INV_STATION_PRINTER_CHECK,
)
INV_CHECK_STATIONS = (INV_STATION_TV_CHECK, INV_STATION_PRINTER_CHECK)
INV_STATION_NAMES = {
    INV_STATION_LOOKUP: "Look up",
    INV_STATION_COUNT: "Count",
    INV_STATION_TV_CHECK: "TV check",
    INV_STATION_PRINTER_CHECK: "Printer check",
}

# Per-event prep status of a unit. The newest row for a unit and event wins.
INV_PREP_CHECKED = "CHECKED"
INV_PREP_FAILED_CHECK = "FAILED_CHECK"
INV_PREP_VOIDED = "VOIDED"

INV_CHECK_OPEN = "OPEN"
INV_CHECK_PASSED = "PASSED"
INV_CHECK_FAILED = "FAILED"
INV_CHECK_SET_ASIDE = "SET_ASIDE"
INV_CHECK_VOIDED = "VOIDED"

# What a NO answer means on a check line: FAIL fails the check; NOTE passes it
# and records the line's prompt as a note.
INV_MISSING_FAIL = "FAIL"
INV_MISSING_NOTE = "NOTE"
INV_ANSWER_YES = "YES"
INV_ANSWER_NO = "NO"

INV_SHIFT_END_ENDED = "ENDED"
INV_SHIFT_END_REPLACED = "REPLACED"
INV_SHIFT_END_IDLE = "IDLE"
INV_SHIFT_END_REVOKED = "REVOKED"

INV_FLAG_REASONS = ("WRONG_DATA", "DAMAGED", "UNKNOWN_ITEM", "OTHER")

INV_COUNT_LINE_APPLIED = "APPLIED"
INV_COUNT_LINE_STALE = "STALE"
INV_COUNT_LINE_UNCHANGED = "UNCHANGED"

# Space kinds. A SLICE is part of a room. A FREEFORM space has no parent and
# uses location_note for where it sits. Combining two slices for one event is
# not a kind; it is SpaceEventOverride.combined_into_space_id.
SPACE_KIND_ROOM = "ROOM"
SPACE_KIND_SLICE = "SLICE"
SPACE_KIND_FREEFORM = "FREEFORM"

SPACE_KINDS = (
    SPACE_KIND_ROOM,
    SPACE_KIND_SLICE,
    SPACE_KIND_FREEFORM,
)

# ============================================================
# Spend type selection modes for expense accounts
# ============================================================

SPEND_TYPE_MODE_SINGLE_LOCKED = "SINGLE_LOCKED"  # exactly one allowed spend type; UI locked
SPEND_TYPE_MODE_ALLOW_LIST = "ALLOW_LIST"        # choose from allowed spend types list

# ============================================================
# Department visibility modes for expense accounts
# ============================================================

VISIBILITY_MODE_ALL = "ALL_DEPARTMENTS"
VISIBILITY_MODE_RESTRICTED = "RESTRICTED"

# ============================================================
# UI grouping
# ============================================================

UI_GROUP_KNOWN_COSTS = "KNOWN_COSTS"
UI_GROUP_HOTEL_SERVICES = "HOTEL_SERVICES"
UI_GROUP_BADGES = "BADGES"

# ============================================================
# Prompt modes for "Known Costs" prompting behavior
# ============================================================

PROMPT_MODE_NONE = "NONE"
PROMPT_MODE_SUGGEST = "SUGGEST"
PROMPT_MODE_REQUIRE_EXPLICIT_NA = "REQUIRE_EXPLICIT_NA"

# ============================================================
# Request kinds within a portfolio
# ============================================================

REQUEST_KIND_PRIMARY = "PRIMARY"
REQUEST_KIND_SUPPLEMENTARY = "SUPPLEMENTARY"

# ============================================================
# Notification statuses
# ============================================================

NOTIF_STATUS_QUEUED = "QUEUED"
NOTIF_STATUS_SENT = "SENT"
NOTIF_STATUS_FAILED = "FAILED"
NOTIF_STATUS_SUPPRESSED = "SUPPRESSED"
# Outbox outcomes that predate any send attempt. The email debug page
# builds its status filter from distinct values, so these need no UI change.
NOTIF_STATUS_CANCELLED = "CANCELLED"
NOTIF_STATUS_RENDER_BLOCKED = "RENDER_BLOCKED"

# ============================================================
# Config audit actions
# ============================================================

CONFIG_AUDIT_CREATE = "CREATE"
CONFIG_AUDIT_UPDATE = "UPDATE"
CONFIG_AUDIT_ARCHIVE = "ARCHIVE"
CONFIG_AUDIT_RESTORE = "RESTORE"

# ============================================================
# Line audit event types
# ============================================================

AUDIT_EVENT_STATUS_CHANGE = "STATUS_CHANGE"
AUDIT_EVENT_REVIEW_DECISION = "REVIEW_DECISION"
AUDIT_EVENT_REQUESTER_RESPONSE = "REQUESTER_RESPONSE"
AUDIT_EVENT_ADMIN_FINAL = "ADMIN_FINAL"
AUDIT_EVENT_AMOUNT_OVERRIDE = "AMOUNT_OVERRIDE"
AUDIT_EVENT_LINE_CREATED = "LINE_CREATED"
AUDIT_EVENT_FIELD_CHANGE = "FIELD_CHANGE"
AUDIT_EVENT_LINE_DELETED = "LINE_DELETED"

# ============================================================
# Work item audit event types
# ============================================================

AUDIT_EVENT_FINALIZE = "FINALIZE"
AUDIT_EVENT_UNFINALIZE = "UNFINALIZE"
AUDIT_EVENT_BOARD_RELEASE = "BOARD_RELEASE"
AUDIT_EVENT_SUBMIT = "SUBMIT"
AUDIT_EVENT_RECALL_TO_DRAFT = "RECALL_TO_DRAFT"
AUDIT_EVENT_DISPATCH = "DISPATCH"
AUDIT_EVENT_NEEDS_INFO_REQUESTED = "NEEDS_INFO_REQUESTED"
AUDIT_EVENT_NEEDS_INFO_RESPONDED = "NEEDS_INFO_RESPONDED"
AUDIT_EVENT_CHECKOUT = "CHECKOUT"
AUDIT_EVENT_CHECKIN = "CHECKIN"
AUDIT_EVENT_VIEW = "VIEW"
AUDIT_EVENT_EXTENSION_GRANTED = "EXTENSION_GRANTED"
AUDIT_EVENT_EXTENSION_REVOKED = "EXTENSION_REVOKED"

# ============================================================
# Review actions
# ============================================================

REVIEW_ACTION_APPROVE = "APPROVE"
REVIEW_ACTION_APPROVE_NEEDS_REVIEW = "APPROVE_NEEDS_REVIEW"
REVIEW_ACTION_REJECT = "REJECT"
REVIEW_ACTION_NEEDS_INFO = "NEEDS_INFO"
REVIEW_ACTION_NEEDS_ADJUSTMENT = "NEEDS_ADJUSTMENT"
REVIEW_ACTION_RESET = "RESET"
REVIEW_ACTION_RESPOND = "RESPOND"
REVIEW_ACTION_REOPEN = "REOPEN"  # group-final work types only: decided line back to PENDING

# ============================================================
# Routing strategy constants
# ============================================================

ROUTING_STRATEGY_EXPENSE_ACCOUNT = "expense_account"
ROUTING_STRATEGY_CONTRACT_TYPE = "contract_type"
ROUTING_STRATEGY_CATEGORY = "category"
ROUTING_STRATEGY_DIRECT = "direct"

# ============================================================
# Email outbox statuses
# ============================================================

OUTBOX_STATUS_QUEUED = "QUEUED"
OUTBOX_STATUS_SENDING = "SENDING"
OUTBOX_STATUS_RENDER_BLOCKED = "RENDER_BLOCKED"
OUTBOX_STATUS_SENT = "SENT"
OUTBOX_STATUS_FAILED = "FAILED"
OUTBOX_STATUS_CANCELLED = "CANCELLED"
OUTBOX_STATUS_SUPPRESSED = "SUPPRESSED"

OUTBOX_TERMINAL_STATUSES = (
    OUTBOX_STATUS_SENT,
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_CANCELLED,
    OUTBOX_STATUS_SUPPRESSED,
)
OUTBOX_CLAIMABLE_STATUSES = (OUTBOX_STATUS_QUEUED, OUTBOX_STATUS_RENDER_BLOCKED)

ENQUEUE_OUTCOME_CREATED = "CREATED"
ENQUEUE_OUTCOME_DUPLICATE = "DUPLICATE"
ENQUEUE_OUTCOME_DEFERRED = "DEFERRED"
ENQUEUE_OUTCOME_BLOCKED_WINDOW = "BLOCKED_WINDOW"
ENQUEUE_OUTCOME_BLOCKED_INACTIVE = "BLOCKED_INACTIVE"

# Outcomes where no outbox row exists. A window blocks a whole (template,
# event) pair, so a reminder across 100 departments is one blocked run rather
# than 100 suppressed rows; there is nothing per-recipient to record.
ENQUEUE_BLOCKED_OUTCOMES = (
    ENQUEUE_OUTCOME_BLOCKED_WINDOW,
    ENQUEUE_OUTCOME_BLOCKED_INACTIVE,
)

NOTIFICATION_KINDS = (
    "submitted", "dispatched", "needs_attention", "response_received",
    "submission_confirmation", "finalized", "submission_reminder", "sent_back",
)
