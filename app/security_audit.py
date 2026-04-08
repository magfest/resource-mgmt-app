"""Security audit logging helpers.

This module provides functions to log security-relevant events such as
authentication, administrative actions, and sensitive data access.

Events are stored in the SecurityAuditLog table for later review and
compliance reporting. Designed for PII compliance with 6-month retention.
"""
from __future__ import annotations

import json
from datetime import datetime
from flask import request, session, has_request_context
from app import db
from app.models import SecurityAuditLog

# Event categories
CATEGORY_AUTH = "AUTH"
CATEGORY_ADMIN = "ADMIN"
CATEGORY_ACCESS = "ACCESS"
CATEGORY_SECURITY = "SECURITY"

# Event types
EVENT_LOGIN_SUCCESS = "LOGIN_SUCCESS"
EVENT_LOGIN_FAILURE = "LOGIN_FAILURE"
EVENT_LOGOUT = "LOGOUT"
EVENT_USER_VIEW = "USER_VIEW"
EVENT_USER_MODIFY = "USER_MODIFY"
EVENT_CONFIG_MODIFY = "CONFIG_MODIFY"
EVENT_IMPERSONATE_START = "IMPERSONATE_START"
EVENT_IMPERSONATE_END = "IMPERSONATE_END"
EVENT_SENSITIVE_VIEW = "SENSITIVE_VIEW"
EVENT_ACCESS_DENIED = "ACCESS_DENIED"

# Severities
SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_ALERT = "ALERT"


def log_security_event(
    event_type: str,
    category: str,
    severity: str = SEVERITY_INFO,
    user_id: str | None = None,
    details: dict | None = None,
) -> SecurityAuditLog:
    """Log a security audit event.

    Args:
        event_type: The type of event (e.g., LOGIN_SUCCESS, ACCESS_DENIED)
        category: Event category (AUTH, ADMIN, ACCESS, SECURITY)
        severity: Severity level (INFO, WARNING, ALERT)
        user_id: User ID associated with event. If None, attempts to get from session.
        details: Optional dict of additional event-specific data (will be JSON-serialized)

    Returns:
        The created SecurityAuditLog record (not yet committed)

    Note:
        This function adds the event to the session but does NOT commit.
        The caller is responsible for committing the transaction.
    """
    # Get user_id from session if not provided
    if user_id is None:
        user_id = session.get("active_user_id") if has_request_context() else None

    # Get request context
    ip_address = None
    user_agent = None
    if has_request_context():
        ip_address = request.remote_addr
        user_agent = str(request.user_agent)[:512] if request.user_agent else None

    event = SecurityAuditLog(
        timestamp=datetime.utcnow(),
        user_id=user_id,
        ip_address=ip_address,
        user_agent=user_agent,
        event_type=event_type,
        event_category=category,
        severity=severity,
        details=json.dumps(details) if details else None,
    )
    db.session.add(event)
    return event


# Convenience functions for common events

def log_login_success(user_id: str, provider: str, email: str) -> SecurityAuditLog:
    """Log successful authentication.

    Args:
        user_id: The authenticated user's ID
        provider: OAuth provider used (e.g., 'google', 'keycloak')
        email: User's email address
    """
    return log_security_event(
        EVENT_LOGIN_SUCCESS,
        CATEGORY_AUTH,
        SEVERITY_INFO,
        user_id=user_id,
        details={"provider": provider, "email": email},
    )


def log_login_failure(reason: str, email: str = None, provider: str = None,
                      severity: str = SEVERITY_WARNING) -> SecurityAuditLog:
    """Log failed authentication attempt.

    Args:
        reason: Why the login failed (e.g., 'domain_restricted', 'oauth_error', 'stale_session')
        email: Email that attempted to log in (if known)
        provider: OAuth provider that was used (if known)
        severity: Severity level (default WARNING, use INFO for benign failures like stale_session)
    """
    return log_security_event(
        EVENT_LOGIN_FAILURE,
        CATEGORY_AUTH,
        severity,
        user_id=None,
        details={"reason": reason, "email": email, "provider": provider},
    )


def log_logout(user_id: str) -> SecurityAuditLog:
    """Log user logout.

    Args:
        user_id: The user who logged out
    """
    return log_security_event(
        EVENT_LOGOUT,
        CATEGORY_AUTH,
        SEVERITY_INFO,
        user_id=user_id,
    )


def log_access_denied(user_id: str, path: str, reason: str = None) -> SecurityAuditLog:
    """Log 403 access denied.

    Args:
        user_id: The user who was denied access
        path: The path/resource they tried to access
        reason: Optional reason for denial
    """
    return log_security_event(
        EVENT_ACCESS_DENIED,
        CATEGORY_SECURITY,
        SEVERITY_WARNING,
        user_id=user_id,
        details={"path": path, "reason": reason},
    )


def log_impersonation_start(admin_user_id: str, target_user_id: str) -> SecurityAuditLog:
    """Log admin starting impersonation.

    Args:
        admin_user_id: The admin who is impersonating
        target_user_id: The user being impersonated
    """
    return log_security_event(
        EVENT_IMPERSONATE_START,
        CATEGORY_ADMIN,
        SEVERITY_WARNING,
        user_id=admin_user_id,
        details={"target_user_id": target_user_id},
    )


def log_impersonation_end(admin_user_id: str, target_user_id: str) -> SecurityAuditLog:
    """Log admin ending impersonation.

    Args:
        admin_user_id: The admin who was impersonating
        target_user_id: The user who was being impersonated
    """
    return log_security_event(
        EVENT_IMPERSONATE_END,
        CATEGORY_ADMIN,
        SEVERITY_INFO,
        user_id=admin_user_id,
        details={"target_user_id": target_user_id},
    )
