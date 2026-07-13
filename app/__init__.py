from __future__ import annotations

import os
import secrets as stdlib_secrets  # Avoid conflict with app.secrets
import threading
from datetime import timedelta
from flask import Flask, session, render_template, g
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    # --- AWS Secrets Manager (optional) ---
    # If AWS_SECRETS_ARN is set, load secrets into environment variables
    # This must happen before reading other config
    from app.secrets import load_secrets_into_env, get_secret, get_database_url
    secrets_loaded = load_secrets_into_env()
    if secrets_loaded > 0:
        app.logger.info(f"Loaded {secrets_loaded} secrets from AWS Secrets Manager")

    # --- Environment Detection ---
    env = os.environ.get("APP_ENV", "development")
    is_production = env == "production"

    # --- Secret Key (REQUIRED in production) ---
    secret_key = get_secret("SECRET_KEY")
    if is_production and not secret_key:
        raise RuntimeError("SECRET_KEY is required in production (set in env or Secrets Manager)")
    app.config["SECRET_KEY"] = secret_key

    # --- Database ---
    db_url = get_database_url() or get_secret("DATABASE_URL")
    if is_production and not db_url:
        raise RuntimeError("DATABASE_URL is required in production (set in env or Secrets Manager)")
    if not db_url:
        db_path = os.path.join(app.instance_path, "magfest_budget.sqlite3")
        db_url = f"sqlite:///{db_path}"
    # Handle postgres:// vs postgresql:// (AWS RDS compatibility)
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Connection pool settings (important for Heroku Postgres / production)
    if db_url and "sqlite" not in db_url:
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_size": 5,           # Connections per worker (default, safe for Heroku Essential plans)
            "max_overflow": 3,        # Extra connections under burst load
            "pool_recycle": 1800,     # Recycle connections every 30 min (Heroku recycles them server-side)
            "pool_pre_ping": True,    # Verify connections are alive before use (prevents stale connection errors)
        }

    # --- Session Cookie Security ---
    app.config["SESSION_COOKIE_SECURE"] = is_production  # HTTPS only in production
    app.config["SESSION_COOKIE_HTTPONLY"] = True  # Prevent JS access
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"  # CSRF protection

    # --- Session Timeout (Auto Sign-Out) ---
    # Sessions expire after 30 minutes of inactivity in production, 60 minutes in dev
    session_timeout_minutes = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "30" if is_production else "60"))
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=session_timeout_minutes)
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True  # Reset timeout on each request (sliding window)

    # --- Beta Testing Mode ---
    # Enables role override dropdown for super-admins to test different permission levels
    beta_mode = os.environ.get("BETA_TESTING_MODE", "").lower()
    app.config["BETA_TESTING_MODE"] = beta_mode == "true" or (not is_production and beta_mode != "false")

    # --- Environment Banner ---
    # Show a warning banner for non-production environments
    app.config["ENV_BANNER_ENABLED"] = not is_production
    app.config["ENV_BANNER_MESSAGE"] = os.environ.get(
        "ENV_BANNER_MESSAGE",
        "Development Environment - Data may be reset at any time. Do not use for production."
    )

    # --- Authentication Modes ---
    # Dev login: local user switcher for development (requires explicit opt-in)
    dev_login = os.environ.get("DEV_LOGIN_ENABLED", "false").lower()
    app.config["DEV_LOGIN_ENABLED"] = dev_login == "true" and not is_production

    # --- OAuth Provider Selection ---
    # AUTH_PROVIDER: "google", "keycloak", or "none" (default: auto-detect based on config)
    auth_provider = os.environ.get("AUTH_PROVIDER", "").lower()

    # Google OAuth configuration
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID")
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    app.config["GOOGLE_CLIENT_ID"] = google_client_id
    app.config["GOOGLE_CLIENT_SECRET"] = google_client_secret
    google_configured = bool(google_client_id and google_client_secret)

    # Google OAuth domain restriction (comma-separated list of allowed domains)
    # Example: "magfest.org,magwest.org,magstock.org"
    allowed_domains = os.environ.get("GOOGLE_ALLOWED_DOMAINS", "")
    if allowed_domains:
        app.config["GOOGLE_ALLOWED_DOMAINS"] = {d.strip().lower() for d in allowed_domains.split(",") if d.strip()}
    else:
        app.config["GOOGLE_ALLOWED_DOMAINS"] = None

    # Keycloak OAuth configuration
    keycloak_url = os.environ.get("KEYCLOAK_URL")  # e.g., "https://auth.magfest.org"
    keycloak_realm = os.environ.get("KEYCLOAK_REALM", "magfest")
    keycloak_client_id = os.environ.get("KEYCLOAK_CLIENT_ID")
    keycloak_client_secret = os.environ.get("KEYCLOAK_CLIENT_SECRET")
    app.config["KEYCLOAK_URL"] = keycloak_url
    app.config["KEYCLOAK_REALM"] = keycloak_realm
    app.config["KEYCLOAK_CLIENT_ID"] = keycloak_client_id
    app.config["KEYCLOAK_CLIENT_SECRET"] = keycloak_client_secret
    keycloak_configured = bool(keycloak_url and keycloak_client_id and keycloak_client_secret)

    # Determine which auth provider to use
    if auth_provider == "keycloak" and keycloak_configured:
        app.config["AUTH_PROVIDER"] = "keycloak"
    elif auth_provider == "google" and google_configured:
        app.config["AUTH_PROVIDER"] = "google"
    elif auth_provider == "none":
        app.config["AUTH_PROVIDER"] = None
    elif keycloak_configured:
        # Auto-detect: prefer Keycloak if configured
        app.config["AUTH_PROVIDER"] = "keycloak"
    elif google_configured:
        app.config["AUTH_PROVIDER"] = "google"
    else:
        app.config["AUTH_PROVIDER"] = None

    # Legacy flags for template compatibility
    app.config["GOOGLE_AUTH_ENABLED"] = app.config["AUTH_PROVIDER"] == "google"
    app.config["KEYCLOAK_AUTH_ENABLED"] = app.config["AUTH_PROVIDER"] == "keycloak"

    # --- Email Notifications (AWS SES) ---
    email_enabled = os.environ.get("EMAIL_ENABLED", "").lower()
    app.config["EMAIL_ENABLED"] = email_enabled == "true"
    app.config["EMAIL_FROM_ADDRESS"] = os.environ.get("EMAIL_FROM_ADDRESS", "noreply@magfest.org")
    app.config["BASE_URL"] = os.environ.get("BASE_URL", "https://budget.magfest.org")
    app.config["AWS_SES_REGION"] = os.environ.get("AWS_SES_REGION", "us-east-1")
    app.config["AWS_SES_ACCESS_KEY"] = os.environ.get("AWS_SES_ACCESS_KEY")
    app.config["AWS_SES_SECRET_KEY"] = os.environ.get("AWS_SES_SECRET_KEY")

    # Email rate limits (safety mechanisms)
    app.config["EMAIL_HOURLY_LIMIT"] = int(os.environ.get("EMAIL_HOURLY_LIMIT", "50"))
    app.config["EMAIL_DAILY_LIMIT"] = int(os.environ.get("EMAIL_DAILY_LIMIT", "200"))
    app.config["EMAIL_CIRCUIT_BREAKER_THRESHOLD"] = int(os.environ.get("EMAIL_CIRCUIT_BREAKER_THRESHOLD", "5"))
    app.config["EMAIL_CIRCUIT_BREAKER_WINDOW"] = int(os.environ.get("EMAIL_CIRCUIT_BREAKER_WINDOW", "10"))

    # --- Slack Notifications ---
    app.config["SLACK_ENABLED"] = os.environ.get("SLACK_ENABLED", "").lower() == "true"
    app.config["SLACK_BOT_TOKEN"] = os.environ.get("SLACK_BOT_TOKEN")
    app.config["SLACK_CHANNEL_ID"] = os.environ.get("SLACK_CHANNEL_ID")

    # --- Supply catalog item images (S3) ---
    app.config["SUPPLY_IMAGE_BUCKET"] = os.environ.get("SUPPLY_IMAGE_BUCKET")
    app.config["SUPPLY_IMAGE_ACCESS_KEY"] = os.environ.get("SUPPLY_IMAGE_ACCESS_KEY")
    app.config["SUPPLY_IMAGE_SECRET_KEY"] = os.environ.get("SUPPLY_IMAGE_SECRET_KEY")
    app.config["SUPPLY_IMAGE_REGION"] = os.environ.get("SUPPLY_IMAGE_REGION", "us-east-1")

    # --- Proxy Fix for reverse proxies (Heroku, AWS, etc.) ---
    if os.environ.get("BEHIND_PROXY", "false").lower() == "true":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1,
        )

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    # --- Template Filters ---
    import math
    import re

    @app.template_filter('format_qty')
    def format_qty(value):
        """Format quantity - show as rounded-up integer."""
        if value is None:
            return '-'
        int_val = int(math.ceil(float(value)))
        return str(int_val)

    @app.template_filter('markdown_links')
    def markdown_links(text):
        """Convert markdown-style links [text](url) to HTML links.

        Also auto-links bare URLs that aren't already in markdown format.
        Example: [Hotel Policy](https://docs.google.com/...) becomes clickable.
        """
        if not text:
            return ''
        from markupsafe import escape

        # First, escape the entire input to neutralize any raw HTML
        text = str(escape(text))

        # Convert markdown links: [text](url) — both groups are now HTML-safe
        md_pattern = r'\[([^\]]+)\]\((https?://[^\s\)]+)\)'
        result = re.sub(md_pattern, r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
        # Then, auto-link bare URLs that aren't already in an href
        # Negative lookbehind to avoid double-linking
        bare_url_pattern = r'(?<!href=")(https?://[^\s<>"\'\)]+)(?![^<]*</a>)'
        result = re.sub(bare_url_pattern, r'<a href="\1" target="_blank" rel="noopener">\1</a>', result)
        return result

    @app.template_filter('strip_links')
    def strip_links(text):
        """Strip markdown links and bare URLs for plain text display (e.g., in dropdowns).

        Replaces [text](url) with just 'text' and removes bare URLs.
        """
        if not text:
            return ''
        # Replace markdown links [text](url) with just the text
        md_pattern = r'\[([^\]]+)\]\((https?://[^\s\)]+)\)'
        result = re.sub(md_pattern, r'\1', text)
        # Remove bare URLs
        bare_url_pattern = r'https?://[^\s<>"\'\)]+'
        result = re.sub(bare_url_pattern, '[see details]', result)
        return result

    @app.template_filter('user_display')
    def user_display(user_id):
        """Resolve a user ID to a display name. Caches lookups per-request."""
        if not user_id:
            return ''
        # Per-request cache on g
        if not hasattr(g, '_user_display_cache'):
            g._user_display_cache = {}
        cache = g._user_display_cache
        if user_id not in cache:
            from app.models import User
            user = User.query.get(user_id)
            cache[user_id] = (user.display_name or user.email) if user else str(user_id)
        return cache[user_id]

    # NOTE: get_site_content is registered as a Jinja global after register_all_routes()
    # to avoid circular imports that would cause the RouteHelpers (h) to be None.

    # Import models so migrations can detect them
    from . import models  # noqa: F401

    # --- Security Headers Middleware ---
    @app.after_request
    def add_security_headers(response):
        """Add security headers to all responses."""
        # Prevent clickjacking - don't allow embedding in iframes
        response.headers["X-Frame-Options"] = "DENY"

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # XSS protection (legacy but still useful for older browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions Policy (formerly Feature-Policy) - disable sensitive features
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        # Content Security Policy - restrict resource loading
        # Scripts require a nonce; styles allow inline (industry-standard compromise)
        # See docs/security.md for developer guidance on CSP nonces
        nonce = getattr(g, 'csp_nonce', None)
        if not nonce:
            # This should never happen - generate_csp_nonce runs before every request.
            # If it does, fail loudly rather than silently disabling CSP protection.
            app.logger.error("CSP nonce missing! generate_csp_nonce() may have failed.")
            nonce = "MISSING-NONCE-CHECK-LOGS"  # Will break scripts, making issue obvious
        # Catalog item photos are served from the app's S3 bucket — the one
        # non-'self' resource origin. Scoped to the exact bucket host (never
        # *.s3.amazonaws.com) so only our own bucket is embeddable.
        img_src = "img-src 'self' data:"
        supply_bucket = app.config.get("SUPPLY_IMAGE_BUCKET")
        if supply_bucket:
            img_src += f" https://{supply_bucket}.s3.amazonaws.com"
        csp_directives = [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",  # Nonce required for inline scripts
            "style-src 'self' 'unsafe-inline'",    # Inline styles allowed (low risk)
            img_src,
            "font-src 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "object-src 'none'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

        # HSTS - force HTTPS (only in production)
        if is_production:
            # max-age=31536000 = 1 year; includeSubDomains for all subdomains; preload for HSTS preload list
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

        return response

    # --- Scanner / bot flood protection ---
    # Two layers:
    #   1. Block known scanner paths instantly (no DB, no template).
    #   2. Track 404-per-IP in memory; auto-block IPs that trip the threshold.
    # This prevents scanner floods from exhausting the DB connection pool.
    from collections import defaultdict
    import time as _time

    _SCANNER_EXTENSIONS = ('.env', '.php', '.git/config', '.git/HEAD', '.asp', '.aspx', '.jsp', '.cgi')
    _SCANNER_PATHS = {
        '/wp-login.php', '/wp-admin', '/administrator', '/xmlrpc.php',
        '/wp-content', '/wp-includes', '/.well-known/security.txt',
    }

    _BLOCK_THRESHOLD = 10       # 404s from one IP before blocking
    _BLOCK_WINDOW = 60          # seconds to track 404 counts
    _BLOCK_DURATION = 300       # seconds to block an IP after threshold hit
    _ip_404_counts: dict[str, list] = defaultdict(list)  # IP -> list of timestamps
    _ip_blocked_until: dict[str, float] = {}             # IP -> unblock time

    @app.before_request
    def block_scanners():
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip and ',' in ip:
            ip = ip.split(',')[0].strip()
        now = _time.monotonic()

        # Check if IP is currently blocked
        if ip in _ip_blocked_until:
            if now < _ip_blocked_until[ip]:
                return '', 403
            else:
                del _ip_blocked_until[ip]
                _ip_404_counts.pop(ip, None)

        # Fast reject: known scanner paths/extensions
        path = request.path.rstrip('/')
        if (any(path.endswith(ext) for ext in _SCANNER_EXTENSIONS)
                or path in _SCANNER_PATHS):
            _record_ip_404(ip, now)
            return 'Not Found', 404

    def _record_ip_404(ip: str, now: float):
        """Track a 404 hit for an IP and block if threshold exceeded."""
        hits = _ip_404_counts[ip]
        hits.append(now)
        # Trim old entries outside the window
        cutoff = now - _BLOCK_WINDOW
        _ip_404_counts[ip] = [t for t in hits if t > cutoff]
        if len(_ip_404_counts[ip]) >= _BLOCK_THRESHOLD:
            _ip_blocked_until[ip] = now + _BLOCK_DURATION
            app.logger.warning(
                f"Blocked IP {ip} for {_BLOCK_DURATION}s after {_BLOCK_THRESHOLD} "
                f"scanner hits in {_BLOCK_WINDOW}s"
            )

    @app.after_request
    def track_404_ips(response):
        """Track IPs that generate 404s from normal routes too."""
        if response.status_code == 404:
            ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            if ip and ',' in ip:
                ip = ip.split(',')[0].strip()
            _record_ip_404(ip, _time.monotonic())
        return response

    # --- Session Management ---
    @app.before_request
    def make_session_permanent():
        """Make sessions permanent so PERMANENT_SESSION_LIFETIME applies."""
        session.permanent = True

    # --- CSP Nonce Generation ---
    @app.before_request
    def generate_csp_nonce():
        """Generate a unique nonce for Content Security Policy on each request.

        This nonce is used to allow specific inline scripts while blocking
        injected scripts. Each request gets a fresh nonce for security.

        Access in templates via: {{ csp_nonce }}
        Usage: <script nonce="{{ csp_nonce }}">...</script>
        """
        g.csp_nonce = stdlib_secrets.token_urlsafe(32)

    # -----------------------------
    # Helpers (auth + scoping)
    # -----------------------------
    # Demo / dev seeding helpers live in app/seeds/demo_data.py and
    # app/seeds/demo_users.py and are imported directly by their callers
    # (routes/dev.py, app/cli.py).

    def ensure_bootstrap_admins():
        """Ensure essential admin accounts exist. Runs on every deployment."""
        from .models import User, UserRole, ROLE_SUPER_ADMIN
        import uuid

        # Bootstrap admins from environment variable
        # Format: "email1:Display Name 1,email2:Display Name 2"
        # Falls back to empty list if not set (no admins auto-created)
        bootstrap_admins_str = os.environ.get("BOOTSTRAP_ADMINS", "")
        bootstrap_admins = []
        for entry in bootstrap_admins_str.split(","):
            entry = entry.strip()
            if ":" in entry:
                email, name = entry.split(":", 1)
                bootstrap_admins.append((email.strip(), name.strip()))


        for email, display_name in bootstrap_admins:
            user = db.session.query(User).filter_by(email=email).first()
            if not user:
                user = User(
                    id=str(uuid.uuid4()),
                    email=email,
                    display_name=display_name,
                    is_active=True,
                )
                db.session.add(user)
                db.session.flush()
                app.logger.info(f"Created bootstrap admin user: {email}")

            # Ensure SUPER_ADMIN role exists
            has_admin_role = (
                db.session.query(UserRole)
                .filter_by(user_id=user.id, role_code=ROLE_SUPER_ADMIN)
                .first()
            )
            if not has_admin_role:
                db.session.add(UserRole(
                    user_id=user.id,
                    role_code=ROLE_SUPER_ADMIN,
                ))
                app.logger.info(f"Granted SUPER_ADMIN role to: {email}")

        db.session.commit()

    def get_active_user_id() -> str | None:
        user_id = session.get("active_user_id")
        if user_id:
            return user_id
        # Only use dev default if dev login is enabled
        if app.config.get("DEV_LOGIN_ENABLED"):
            return "dev:alex"
        return None

    def get_active_user():
        from .models import User
        uid = get_active_user_id()
        if uid is None:
            return None
        return db.session.get(User, uid)

    def active_user_roles() -> list[str]:
        from .models import UserRole
        uid = get_active_user_id()
        rows = db.session.query(UserRole.role_code).filter(UserRole.user_id == uid).all()
        return [r[0] for r in rows]

    def has_role(role_code: str) -> bool:
        return role_code in set(active_user_roles())

    def _has_super_admin_role() -> bool:
        """Check if user has SUPER_ADMIN role in database.

        Note: This ignores beta testing role overrides. Use is_super_admin()
        for most permission checks.
        """
        from .models import ROLE_SUPER_ADMIN
        roles = set(active_user_roles())
        return ROLE_SUPER_ADMIN in roles

    def _get_role_override() -> str | None:
        """Get the current role override from session, if any.
        Only super-admins can have overrides, and only in beta testing mode.
        """
        if not app.config.get("BETA_TESTING_MODE"):
            return None
        if not _has_super_admin_role():
            return None
        return session.get("role_override")

    def is_super_admin() -> bool:
        """Check if user should be treated as a super admin.

        In beta testing mode, super admins can override their role to test
        as regular users. This function respects those overrides.
        """
        from .models import ROLE_SUPER_ADMIN

        # Check for role override
        override = _get_role_override()
        if override in ("none", "approver"):
            return False

        roles = set(active_user_roles())
        return ROLE_SUPER_ADMIN in roles

    def active_user_approval_group_ids() -> set[int]:
        from .models import UserRole, ROLE_APPROVER

        # Check for role override (beta testing mode)
        override = _get_role_override()
        if override == "none":
            return set()
        if override == "approver":
            # Use the selected test approval group from session
            test_group_id = session.get("role_override_approval_group_id")
            if test_group_id:
                return {int(test_group_id)}
            return set()

        uid = get_active_user_id()
        rows = (
            db.session.query(UserRole.approval_group_id)
            .filter(UserRole.user_id == uid)
            .filter(UserRole.role_code == ROLE_APPROVER)
            .filter(UserRole.approval_group_id.isnot(None))
            .all()
        )
        return {int(r[0]) for r in rows if r[0] is not None}

    def can_review_group(approval_group_id: int) -> bool:
        return is_super_admin() or (approval_group_id in active_user_approval_group_ids())

    def _get_approval_groups_for_template():
        """Get all active approval groups for the role override dropdown."""
        from .models import ApprovalGroup
        from .routes.admin.helpers import sort_with_override
        return (
            db.session.query(ApprovalGroup)
            .filter(ApprovalGroup.is_active == True)  # noqa: E712
            .order_by(*sort_with_override(ApprovalGroup))
            .all()
        )

    @app.context_processor
    def inject_active_user():
        # Skip expensive DB queries if we're handling an error response
        # (the scanner early-return already catches most, but this handles
        # any other error paths that still render templates)
        if getattr(g, '_skip_nav_queries', False):
            return {
                "csp_nonce": getattr(g, 'csp_nonce', ''),
                "active_user": None,
                "active_user_id": None,
                "active_user_roles": [],
                "is_super_admin": False,
                "beta_testing_mode": False,
                "can_override_role": False,
                "role_override": None,
                "role_override_approval_group_id": None,
                "_get_approval_groups": lambda: [],
                "dev_login_enabled": app.config.get("DEV_LOGIN_ENABLED", False),
                "google_auth_enabled": app.config.get("GOOGLE_AUTH_ENABLED", False),
                "keycloak_auth_enabled": app.config.get("KEYCLOAK_AUTH_ENABLED", False),
                "auth_provider": app.config.get("AUTH_PROVIDER"),
                "is_impersonating": False,
                "real_user_id": None,
                "env_banner_enabled": app.config.get("ENV_BANNER_ENABLED", False),
                "env_banner_message": app.config.get("ENV_BANNER_MESSAGE", ""),
                "nav_admin_work_types": [],
                "nav_approval_groups": [],
                "nav_event_cycle": None,
                "nav_dept_memberships": [],
                "nav_div_memberships": [],
            }

        u = get_active_user()
        roles = active_user_roles()
        has_super_admin = _has_super_admin_role()
        beta_mode = app.config.get("BETA_TESTING_MODE", False)
        override = _get_role_override()

        # Check if impersonating another user
        real_user_id = session.get("real_user_id")
        is_impersonating = bool(real_user_id and real_user_id != get_active_user_id())

        # --- Nav bar context (role-gated menus) ---
        _is_super = is_super_admin()
        nav_admin_work_types: list[str] = []
        nav_approval_groups = []
        nav_event_cycle = None
        nav_dept_memberships = []
        nav_div_memberships = []

        if u:
            from .models import (
                UserRole, WorkType, ApprovalGroup, EventCycle,
                DepartmentMembership, DivisionMembership, Department, Division,
                ROLE_WORKTYPE_ADMIN,
            )

            # nav_admin_work_types is the codes of work types this user can
            # admin (e.g. ["BUDGET"], ["BUDGET", "TECHOPS"]). Templates gate
            # per-work-type admin dropdowns on membership in this list.
            # Super admins implicitly admin every active work type.
            if _is_super:
                nav_admin_work_types = [
                    wt.code for wt in (
                        WorkType.query
                        .filter(WorkType.is_active.is_(True))
                        .order_by(WorkType.sort_order.asc(), WorkType.code.asc())
                        .all()
                    )
                ]
            else:
                admin_role_rows = (
                    db.session.query(UserRole.work_type_id)
                    .filter(UserRole.user_id == u.id)
                    .filter(UserRole.role_code == ROLE_WORKTYPE_ADMIN)
                    .filter(UserRole.work_type_id.isnot(None))
                    .all()
                )
                wt_ids = [int(r[0]) for r in admin_role_rows if r[0] is not None]
                if wt_ids:
                    nav_admin_work_types = [
                        wt.code for wt in (
                            WorkType.query
                            .filter(WorkType.id.in_(wt_ids))
                            .order_by(WorkType.sort_order.asc(), WorkType.code.asc())
                            .all()
                        )
                    ]

            from .routes.admin.helpers import sort_with_override as _sort_override

            # Approval groups the user can review (for Review menu)
            ag_ids = active_user_approval_group_ids()
            if ag_ids:
                nav_approval_groups = (
                    ApprovalGroup.query
                    .filter(ApprovalGroup.id.in_(ag_ids))
                    .filter(ApprovalGroup.is_active.is_(True))
                    .order_by(*_sort_override(ApprovalGroup))
                    .all()
                )

            # User menu: department/division memberships for current event
            # Use session-selected cycle, fallback to default
            selected_id = session.get('selected_event_cycle_id')
            if selected_id and selected_id != 'all':
                nav_event_cycle = EventCycle.query.filter_by(id=selected_id, is_active=True).first()
            if not nav_event_cycle:
                nav_event_cycle = (
                    EventCycle.query
                    .filter(EventCycle.is_default.is_(True))
                    .first()
                ) or (
                    EventCycle.query
                    .filter(EventCycle.is_active.is_(True))
                    .order_by(*_sort_override(EventCycle))
                    .first()
                )

            if nav_event_cycle:
                # Direct department memberships
                nav_dept_memberships = (
                    DepartmentMembership.query
                    .join(Department)
                    .filter(DepartmentMembership.user_id == u.id)
                    .filter(DepartmentMembership.event_cycle_id == nav_event_cycle.id)
                    .order_by(Department.name)
                    .all()
                )

                # Division memberships (division heads)
                nav_div_memberships = (
                    DivisionMembership.query
                    .join(Division)
                    .filter(DivisionMembership.user_id == u.id)
                    .filter(DivisionMembership.event_cycle_id == nav_event_cycle.id)
                    .order_by(Division.name)
                    .all()
                )

        ctx = {
            # CSP nonce for inline scripts (see docs/security.md)
            "csp_nonce": getattr(g, 'csp_nonce', ''),
            "active_user": u,
            "active_user_id": get_active_user_id(),
            "active_user_roles": roles,
            "is_super_admin": _is_super,
            "beta_testing_mode": beta_mode,
            "can_override_role": beta_mode and has_super_admin,
            "role_override": override,
            "role_override_approval_group_id": session.get("role_override_approval_group_id") if override == "approver" else None,
            "_get_approval_groups": _get_approval_groups_for_template,
            "dev_login_enabled": app.config.get("DEV_LOGIN_ENABLED", False),
            "google_auth_enabled": app.config.get("GOOGLE_AUTH_ENABLED", False),
            "keycloak_auth_enabled": app.config.get("KEYCLOAK_AUTH_ENABLED", False),
            "auth_provider": app.config.get("AUTH_PROVIDER"),
            # Impersonation
            "is_impersonating": is_impersonating,
            "real_user_id": real_user_id if is_impersonating else None,
            # Environment banner
            "env_banner_enabled": app.config.get("ENV_BANNER_ENABLED", False),
            "env_banner_message": app.config.get("ENV_BANNER_MESSAGE", ""),
            # Navigation bar
            "nav_admin_work_types": nav_admin_work_types,
            "nav_approval_groups": nav_approval_groups,
            "nav_event_cycle": nav_event_cycle,
            "nav_dept_memberships": nav_dept_memberships,
            "nav_div_memberships": nav_div_memberships,
        }
        return ctx

    from .routes import register_all_routes, RouteHelpers

    register_all_routes(
        app,
        RouteHelpers(
            get_active_user_id=get_active_user_id,
            get_active_user=get_active_user,
            active_user_roles=active_user_roles,
            is_super_admin=is_super_admin,
            active_user_approval_group_ids=active_user_approval_group_ids,
            can_review_group=can_review_group,
            has_super_admin_role=_has_super_admin_role,
        ),
    )

    # Make get_site_content available in all templates
    # This MUST be after register_all_routes() — importing site_content triggers
    # the admin module tree, which imports h from app.routes. If h hasn't been
    # set yet by register_all_routes(), all admin modules get h=None.
    from app.routes.admin.site_content import get_site_content
    app.jinja_env.globals['get_site_content'] = get_site_content

    # Register CLI commands (flask seed, etc.)
    from app.cli import register_cli
    register_cli(app)

    # --- Auto-migrate + auto-seed (runs once per process on first request) ---
    # Two separate jobs collapsed into one hook:
    #   1. flask_migrate.upgrade() ensures schema is at HEAD.
    #      - On Heroku this is a no-op (Procfile already ran migrations).
    #      - On local dev with a freshly deleted SQLite file, this creates
    #        all tables. Idempotent: Alembic compares version to head and
    #        no-ops if equal.
    #   2. If WorkType is empty after migration, run the canonical seed.
    #
    # Registered BEFORE run_bootstrap_once so that ensure_bootstrap_admins()
    # runs against an up-to-date schema (otherwise it'd fail on a fresh DB
    # querying the users table that hasn't been created yet).
    #
    # Idempotency at every layer:
    #   - threading.Lock prevents two threads in the same worker from racing.
    #   - Multi-worker race (rare; only on cold start with empty DB) is
    #     caught by the broad except — first worker wins, second worker's
    #     unique-constraint conflict is logged and swallowed.
    #   - run_all_seeds() itself uses per-row existence checks, so partial
    #     wipes of non-WorkType tables won't trigger this and don't need to.
    #   - Skipped entirely in TESTING mode (tests own their fixture data
    #     and call db.create_all() directly).
    _seed_done = {"done": False}
    _seed_lock = threading.Lock()

    @app.before_request
    def run_seed_once():
        if app.config.get("TESTING") or _seed_done["done"]:
            return
        with _seed_lock:
            if _seed_done["done"]:
                return
            _seed_done["done"] = True

        try:
            from flask_migrate import upgrade as alembic_upgrade
            alembic_upgrade()

            from .models import WorkType
            if db.session.query(WorkType).first():
                return  # populated DB; nothing to seed

            from .seeds.config_seed import run_all_seeds
            app.logger.info("Empty DB detected on first request — running structural seed.")
            run_all_seeds()
        except Exception as e:
            db.session.rollback()
            app.logger.warning(
                f"Auto-migrate/seed check failed: {e}. "
                f"Run `flask db upgrade && python -m app.seeds.config_seed` manually if needed."
            )

    # --- Bootstrap Admins (runs once on first request, after migrate/seed) ---
    _bootstrap_done = {"done": False}

    @app.before_request
    def run_bootstrap_once():
        if not _bootstrap_done["done"]:
            _bootstrap_done["done"] = True
            try:
                ensure_bootstrap_admins()
            except Exception as e:
                app.logger.warning(f"Bootstrap admins check failed (may be pre-migration): {e}")

    # --- Error Handlers ---
    from flask import redirect, url_for, flash, request
    from sqlalchemy.exc import OperationalError, DatabaseError

    @app.errorhandler(400)
    def bad_request_error(error):
        g._skip_nav_queries = True
        return render_template('errors/404.html', error=error), 400

    @app.errorhandler(403)
    def forbidden_error(error):
        # If user is not logged in, redirect to login
        if not session.get('active_user_id') and not app.config.get('DEV_LOGIN_ENABLED'):
            flash('Please sign in to continue.', 'info')
            return redirect(url_for('auth.login_page'))

        # Log access denied event
        user_id = session.get('active_user_id')
        if user_id:
            try:
                from app.security_audit import log_access_denied
                log_access_denied(user_id, request.path, str(error.description) if hasattr(error, 'description') else None)
                db.session.commit()
            except Exception as e:
                # Don't let audit logging failures break the error handler
                app.logger.warning(f"Failed to log access denied event: {e}")
                db.session.rollback()

        # User is logged in but doesn't have permission
        return render_template('errors/403.html', error=error), 403

    @app.errorhandler(401)
    def unauthorized_error(error):
        flash('Please sign in to continue.', 'info')
        return redirect(url_for('auth.login_page'))

    @app.errorhandler(404)
    def not_found_error(error):
        g._skip_nav_queries = True
        return render_template('errors/404.html', error=error), 404

    @app.errorhandler(405)
    def method_not_allowed_error(error):
        g._skip_nav_queries = True
        return render_template('errors/404.html', error=error), 405

    @app.errorhandler(500)
    def internal_error(error):
        # Rollback any pending database transaction to avoid connection issues
        db.session.rollback()
        app.logger.exception("Unhandled 500 error")   
        g._skip_nav_queries = True
        return render_template('errors/500.html', error=error), 500

    @app.errorhandler(OperationalError)
    def database_connection_error(error):
        # Database connection issues (connection refused, timeout, etc.)
        db.session.rollback()
        g._skip_nav_queries = True
        app.logger.error(f"Database connection error: {error}", exc_info=True)   
        return render_template('errors/503.html', error=error if app.debug else None), 503

    @app.errorhandler(DatabaseError)
    def database_error(error):
        # Other database errors
        db.session.rollback()
        g._skip_nav_queries = True
        app.logger.error(f"Database error: {error}", exc_info=True)      
        return render_template('errors/500.html', error=error if app.debug else None), 500

    # In production, catch all unhandled exceptions
    if is_production:
        @app.errorhandler(Exception)
        def unhandled_exception(error):
            db.session.rollback()
            g._skip_nav_queries = True
            app.logger.error(f"Unhandled exception: {error}", exc_info=True)
            return render_template('errors/500.html', error=None), 500

    # --- CLI Commands ---
    import click
    from datetime import datetime as dt

    @app.cli.command("cleanup-audit-logs")
    @click.option("--days", default=180, help="Delete logs older than N days (default: 180)")
    @click.option("--dry-run", is_flag=True, help="Show what would be deleted without deleting")
    def cleanup_audit_logs(days, dry_run):
        """Delete security audit logs older than specified days."""
        from app.models import SecurityAuditLog

        cutoff = dt.utcnow() - timedelta(days=days)
        query = SecurityAuditLog.query.filter(SecurityAuditLog.timestamp < cutoff)
        count = query.count()

        if dry_run:
            click.echo(f"Would delete {count} audit logs older than {cutoff}")
        else:
            query.delete()
            db.session.commit()
            click.echo(f"Deleted {count} audit logs older than {cutoff}")

    return app