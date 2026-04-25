"""
Models package for MAGFest Budget application.

All models and constants are re-exported here for backwards compatibility.
Existing imports like `from app.models import User, WorkItem` continue to work.

Module organization:
- constants.py: Status codes, role codes, visibility modes, etc.
- org.py: EventCycle, Division, Department, User, Memberships
- workflow.py: WorkType, ApprovalGroup, WorkItem, WorkLine, Reviews, Comments, Audit
- budget.py: SpendType, ExpenseAccount, BudgetLineDetail
- contract.py: ContractType, ContractLineDetail
- supply.py: SupplyCategory, SupplyItem, SupplyOrderLineDetail
- telemetry.py: ActivityEvent, NotificationLog, SecurityAuditLog, ConfigAuditEvent
"""

# Re-export all constants
from .constants import (
    # Comment visibility
    COMMENT_VISIBILITY_PUBLIC,
    COMMENT_VISIBILITY_ADMIN,
    # Work item statuses
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_AWAITING_DISPATCH,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_UNDER_REVIEW,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_UNAPPROVED,
    WORK_ITEM_STATUS_NEEDS_INFO,
    WORK_ITEM_STATUS_PAUSED,
    # Work line statuses
    WORK_LINE_STATUS_PENDING,
    WORK_LINE_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
    WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_REJECTED,
    # Review stages
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STAGE_ADMIN_FINAL,
    # Review decision statuses
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_REJECTED,
    # Role codes
    ROLE_SUPER_ADMIN,
    ROLE_WORKTYPE_ADMIN,
    ROLE_APPROVER,
    # Spend type modes
    SPEND_TYPE_MODE_SINGLE_LOCKED,
    SPEND_TYPE_MODE_ALLOW_LIST,
    # Visibility modes
    VISIBILITY_MODE_ALL,
    VISIBILITY_MODE_RESTRICTED,
    # UI groups
    UI_GROUP_KNOWN_COSTS,
    UI_GROUP_HOTEL_SERVICES,
    UI_GROUP_BADGES,
    # Prompt modes
    PROMPT_MODE_NONE,
    PROMPT_MODE_SUGGEST,
    PROMPT_MODE_REQUIRE_EXPLICIT_NA,
    # Request kinds
    REQUEST_KIND_PRIMARY,
    REQUEST_KIND_SUPPLEMENTARY,
    # Notification statuses
    NOTIF_STATUS_QUEUED,
    NOTIF_STATUS_SENT,
    NOTIF_STATUS_FAILED,
    NOTIF_STATUS_SUPPRESSED,
    # Config audit actions
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
    CONFIG_AUDIT_ARCHIVE,
    CONFIG_AUDIT_RESTORE,
    # Line audit event types
    AUDIT_EVENT_STATUS_CHANGE,
    AUDIT_EVENT_REVIEW_DECISION,
    AUDIT_EVENT_REQUESTER_RESPONSE,
    AUDIT_EVENT_ADMIN_FINAL,
    AUDIT_EVENT_AMOUNT_OVERRIDE,
    AUDIT_EVENT_LINE_CREATED,
    AUDIT_EVENT_FIELD_CHANGE,
    AUDIT_EVENT_LINE_DELETED,
    # Work item audit event types
    AUDIT_EVENT_FINALIZE,
    AUDIT_EVENT_UNFINALIZE,
    AUDIT_EVENT_SUBMIT,
    AUDIT_EVENT_DISPATCH,
    AUDIT_EVENT_NEEDS_INFO_REQUESTED,
    AUDIT_EVENT_NEEDS_INFO_RESPONDED,
    AUDIT_EVENT_CHECKOUT,
    AUDIT_EVENT_CHECKIN,
    AUDIT_EVENT_VIEW,
    # Review actions
    REVIEW_ACTION_APPROVE,
    REVIEW_ACTION_REJECT,
    REVIEW_ACTION_NEEDS_INFO,
    REVIEW_ACTION_NEEDS_ADJUSTMENT,
    REVIEW_ACTION_RESET,
    REVIEW_ACTION_RESPOND,
    # Routing strategies
    ROUTING_STRATEGY_EXPENSE_ACCOUNT,
    ROUTING_STRATEGY_CONTRACT_TYPE,
    ROUTING_STRATEGY_CATEGORY,
    ROUTING_STRATEGY_DIRECT,
)

# Re-export organization models
from .org import (
    EventCycle,
    Division,
    Department,
    User,
    DivisionMembership,
    DivisionMembershipWorkTypeAccess,
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    EventCycleDivision,
    EventCycleDepartment,
)

# Re-export workflow models
from .workflow import (
    ApprovalGroup,
    WorkType,
    WorkTypeConfig,
    WorkPortfolio,
    UserRole,
    WorkItem,
    WorkLine,
    WorkLineAuditEvent,
    WorkItemAuditEvent,
    WorkLineComment,
    WorkItemComment,
    WorkLineReview,
)

# Re-export budget models
from .budget import (
    SpendType,
    FrequencyOption,
    ConfidenceLevel,
    PriorityLevel,
    ExpenseAccount,
    ExpenseAccountSpendType,
    ExpenseAccountDepartment,
    ExpenseAccountEventOverride,
    BudgetLineDetail,
)

# Re-export contract models
from .contract import (
    ContractType,
    ContractLineDetail,
)

# Re-export supply models
from .supply import (
    SupplyCategory,
    SupplyItem,
    SupplyOrderLineDetail,
)

# Re-export telemetry models
from .telemetry import (
    ActivityEvent,
    NotificationLog,
    ConfigAuditEvent,
    SecurityAuditLog,
    EmailTemplate,
    SiteContent,
)

# Define __all__ for explicit exports
__all__ = [
    # Constants - Comment visibility
    "COMMENT_VISIBILITY_PUBLIC",
    "COMMENT_VISIBILITY_ADMIN",
    # Constants - Work item statuses
    "WORK_ITEM_STATUS_DRAFT",
    "WORK_ITEM_STATUS_AWAITING_DISPATCH",
    "WORK_ITEM_STATUS_SUBMITTED",
    "WORK_ITEM_STATUS_UNDER_REVIEW",
    "WORK_ITEM_STATUS_FINALIZED",
    "WORK_ITEM_STATUS_UNAPPROVED",
    "WORK_ITEM_STATUS_NEEDS_INFO",
    "WORK_ITEM_STATUS_PAUSED",
    # Constants - Work line statuses
    "WORK_LINE_STATUS_PENDING",
    "WORK_LINE_STATUS_NEEDS_INFO",
    "WORK_LINE_STATUS_NEEDS_ADJUSTMENT",
    "WORK_LINE_STATUS_APPROVED",
    "WORK_LINE_STATUS_REJECTED",
    # Constants - Review stages
    "REVIEW_STAGE_APPROVAL_GROUP",
    "REVIEW_STAGE_ADMIN_FINAL",
    # Constants - Review decision statuses
    "REVIEW_STATUS_PENDING",
    "REVIEW_STATUS_NEEDS_INFO",
    "REVIEW_STATUS_NEEDS_ADJUSTMENT",
    "REVIEW_STATUS_APPROVED",
    "REVIEW_STATUS_REJECTED",
    # Constants - Role codes
    "ROLE_SUPER_ADMIN",
    "ROLE_WORKTYPE_ADMIN",
    "ROLE_APPROVER",
    # Constants - Spend type modes
    "SPEND_TYPE_MODE_SINGLE_LOCKED",
    "SPEND_TYPE_MODE_ALLOW_LIST",
    # Constants - Visibility modes
    "VISIBILITY_MODE_ALL",
    "VISIBILITY_MODE_RESTRICTED",
    # Constants - UI groups
    "UI_GROUP_KNOWN_COSTS",
    "UI_GROUP_HOTEL_SERVICES",
    "UI_GROUP_BADGES",
    # Constants - Prompt modes
    "PROMPT_MODE_NONE",
    "PROMPT_MODE_SUGGEST",
    "PROMPT_MODE_REQUIRE_EXPLICIT_NA",
    # Constants - Request kinds
    "REQUEST_KIND_PRIMARY",
    "REQUEST_KIND_SUPPLEMENTARY",
    # Constants - Notification statuses
    "NOTIF_STATUS_QUEUED",
    "NOTIF_STATUS_SENT",
    "NOTIF_STATUS_FAILED",
    "NOTIF_STATUS_SUPPRESSED",
    # Constants - Config audit actions
    "CONFIG_AUDIT_CREATE",
    "CONFIG_AUDIT_UPDATE",
    "CONFIG_AUDIT_ARCHIVE",
    "CONFIG_AUDIT_RESTORE",
    # Constants - Line audit event types
    "AUDIT_EVENT_STATUS_CHANGE",
    "AUDIT_EVENT_REVIEW_DECISION",
    "AUDIT_EVENT_REQUESTER_RESPONSE",
    "AUDIT_EVENT_ADMIN_FINAL",
    "AUDIT_EVENT_AMOUNT_OVERRIDE",
    "AUDIT_EVENT_LINE_CREATED",
    "AUDIT_EVENT_FIELD_CHANGE",
    "AUDIT_EVENT_LINE_DELETED",
    # Constants - Work item audit event types
    "AUDIT_EVENT_FINALIZE",
    "AUDIT_EVENT_UNFINALIZE",
    "AUDIT_EVENT_SUBMIT",
    "AUDIT_EVENT_DISPATCH",
    "AUDIT_EVENT_NEEDS_INFO_REQUESTED",
    "AUDIT_EVENT_NEEDS_INFO_RESPONDED",
    "AUDIT_EVENT_CHECKOUT",
    "AUDIT_EVENT_CHECKIN",
    "AUDIT_EVENT_VIEW",
    # Constants - Review actions
    "REVIEW_ACTION_APPROVE",
    "REVIEW_ACTION_REJECT",
    "REVIEW_ACTION_NEEDS_INFO",
    "REVIEW_ACTION_NEEDS_ADJUSTMENT",
    "REVIEW_ACTION_RESET",
    "REVIEW_ACTION_RESPOND",
    # Constants - Routing strategies
    "ROUTING_STRATEGY_EXPENSE_ACCOUNT",
    "ROUTING_STRATEGY_CONTRACT_TYPE",
    "ROUTING_STRATEGY_CATEGORY",
    "ROUTING_STRATEGY_DIRECT",
    # Organization models
    "EventCycle",
    "Division",
    "Department",
    "User",
    "DivisionMembership",
    "DivisionMembershipWorkTypeAccess",
    "DepartmentMembership",
    "DepartmentMembershipWorkTypeAccess",
    "EventCycleDivision",
    "EventCycleDepartment",
    # Workflow models
    "ApprovalGroup",
    "WorkType",
    "WorkTypeConfig",
    "WorkPortfolio",
    "UserRole",
    "WorkItem",
    "WorkLine",
    "WorkLineAuditEvent",
    "WorkItemAuditEvent",
    "WorkLineComment",
    "WorkItemComment",
    "WorkLineReview",
    # Budget models
    "SpendType",
    "FrequencyOption",
    "ConfidenceLevel",
    "PriorityLevel",
    "ExpenseAccount",
    "ExpenseAccountSpendType",
    "ExpenseAccountDepartment",
    "ExpenseAccountEventOverride",
    "BudgetLineDetail",
    # Contract models
    "ContractType",
    "ContractLineDetail",
    # Supply models
    "SupplyCategory",
    "SupplyItem",
    "SupplyOrderLineDetail",
    # Telemetry models
    "ActivityEvent",
    "NotificationLog",
    "ConfigAuditEvent",
    "SecurityAuditLog",
    "EmailTemplate",
    "SiteContent",
]
