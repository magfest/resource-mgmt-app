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
WORK_ITEM_STATUS_NEEDS_INFO = "NEEDS_INFO"  # awaiting requester response
WORK_ITEM_STATUS_PAUSED = "PAUSED"  # supplementary blocked while primary is unfinalized

# ============================================================
# Work line statuses (overall current state)
# ============================================================

WORK_LINE_STATUS_PENDING = "PENDING"
WORK_LINE_STATUS_NEEDS_INFO = "NEEDS_INFO"
WORK_LINE_STATUS_NEEDS_ADJUSTMENT = "NEEDS_ADJUSTMENT"
WORK_LINE_STATUS_APPROVED = "APPROVED"
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
REVIEW_STATUS_REJECTED = "REJECTED"

# ============================================================
# Role codes
# ============================================================

ROLE_SUPER_ADMIN = "SUPER_ADMIN"        # global admin
ROLE_WORKTYPE_ADMIN = "WORKTYPE_ADMIN"  # admin for a work type (e.g., BUDGET)
ROLE_APPROVER = "APPROVER"              # approver (typically scoped to approval group)

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
AUDIT_EVENT_SUBMIT = "SUBMIT"
AUDIT_EVENT_DISPATCH = "DISPATCH"
AUDIT_EVENT_NEEDS_INFO_REQUESTED = "NEEDS_INFO_REQUESTED"
AUDIT_EVENT_NEEDS_INFO_RESPONDED = "NEEDS_INFO_RESPONDED"
AUDIT_EVENT_CHECKOUT = "CHECKOUT"
AUDIT_EVENT_CHECKIN = "CHECKIN"
AUDIT_EVENT_VIEW = "VIEW"

# ============================================================
# Review actions
# ============================================================

REVIEW_ACTION_APPROVE = "APPROVE"
REVIEW_ACTION_REJECT = "REJECT"
REVIEW_ACTION_NEEDS_INFO = "NEEDS_INFO"
REVIEW_ACTION_NEEDS_ADJUSTMENT = "NEEDS_ADJUSTMENT"
REVIEW_ACTION_RESET = "RESET"
REVIEW_ACTION_RESPOND = "RESPOND"

# ============================================================
# Routing strategy constants
# ============================================================

ROUTING_STRATEGY_EXPENSE_ACCOUNT = "expense_account"
ROUTING_STRATEGY_CONTRACT_TYPE = "contract_type"
ROUTING_STRATEGY_CATEGORY = "category"
ROUTING_STRATEGY_DIRECT = "direct"
