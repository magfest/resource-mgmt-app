from __future__ import annotations

import os
import secrets as stdlib_secrets  # Avoid conflict with app.secrets
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

    # --- Proxy Fix for AWS AppRunner ---
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
        csp_directives = [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",  # Nonce required for inline scripts
            "style-src 'self' 'unsafe-inline'",    # Inline styles allowed (low risk)
            "img-src 'self' data:",
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
    # Helpers (demo auth + scoping)
    # -----------------------------

    def ensure_demo_reference_data():
        """Seed all reference/lookup tables needed for the budget workflow."""
        from .models import (
            ApprovalGroup,
            WorkType,
            SpendType,
            FrequencyOption,
            ConfidenceLevel,
            PriorityLevel,
        )

        # ApprovalGroups
        if not db.session.query(ApprovalGroup).first():
            groups = [
                ("TECH", "Tech", True, 10),
                ("HOTEL", "Hotel", True, 20),
                ("OTHER", "Other", True, 30),
            ]
            for code, name, active, sort in groups:
                db.session.add(
                    ApprovalGroup(code=code, name=name, is_active=active, sort_order=sort)
                )
            db.session.flush()

        # WorkTypes
        if not db.session.query(WorkType).first():
            work_types = [
                ("BUDGET", "Budget Request", True, 10),
            ]
            for code, name, active, sort in work_types:
                db.session.add(
                    WorkType(code=code, name=name, is_active=active, sort_order=sort)
                )
            db.session.flush()

        # SpendTypes
        if not db.session.query(SpendType).first():
            spend_types = [
                ("DIVVY", "Divvy", "Corporate card purchases", True, 10),
                ("BANK", "Bank", "Direct bank transfers / checks", True, 20),
                ("HOTEL_FEE", "Hotel Fee", "Fees paid directly to hotel", True, 30),
            ]
            for code, name, desc, active, sort in spend_types:
                db.session.add(
                    SpendType(code=code, name=name, description=desc, is_active=active, sort_order=sort)
                )
            db.session.flush()

        # FrequencyOptions
        if not db.session.query(FrequencyOption).first():
            frequencies = [
                ("ONE_TIME", "One Time", "Single purchase", True, 10),
                ("RECURRING", "Recurring", "Recurring expense across events", True, 20),
            ]
            for code, name, desc, active, sort in frequencies:
                db.session.add(
                    FrequencyOption(code=code, name=name, description=desc, is_active=active, sort_order=sort)
                )
            db.session.flush()

        # ConfidenceLevels
        if not db.session.query(ConfidenceLevel).first():
            confidence_levels = [
                ("CONFIRMED", "Confirmed", "Price is confirmed/quoted", True, 10),
                ("ESTIMATED", "Estimated", "Price is estimated", True, 20),
                ("PLACEHOLDER", "Placeholder", "Rough placeholder amount", True, 30),
            ]
            for code, name, desc, active, sort in confidence_levels:
                db.session.add(
                    ConfidenceLevel(code=code, name=name, description=desc, is_active=active, sort_order=sort)
                )
            db.session.flush()

        # PriorityLevels
        if not db.session.query(PriorityLevel).first():
            priority_levels = [
                ("CRITICAL", "Critical", "Essential for event operations", True, 10),
                ("HIGH", "High", "Important but event can proceed without", True, 20),
                ("MEDIUM", "Medium", "Nice to have", True, 30),
                ("LOW", "Low", "Optional / stretch goal", True, 40),
            ]
            for code, name, desc, active, sort in priority_levels:
                db.session.add(
                    PriorityLevel(code=code, name=name, description=desc, is_active=active, sort_order=sort)
                )
            db.session.flush()

        db.session.commit()

    def ensure_demo_expense_accounts():
        """Seed expense accounts (replaces old BudgetItemType)."""
        from .models import (
            ExpenseAccount,
            ApprovalGroup,
            SpendType,
            SPEND_TYPE_MODE_SINGLE_LOCKED,
            SPEND_TYPE_MODE_ALLOW_LIST,
        )

        if db.session.query(ExpenseAccount).first():
            return

        groups_by_code = {g.code: g for g in db.session.query(ApprovalGroup).all()}
        spend_by_code = {s.code: s for s in db.session.query(SpendType).all()}

        # Demo expense accounts
        demo_accounts = [
            # code, name, desc, approval_group, default_spend_type, spend_mode, is_fixed, unit_price_cents
            ("RADIO_RENTAL", "Radios (Rental)", "Handheld radios rental for operations",
             "TECH", "DIVVY", SPEND_TYPE_MODE_SINGLE_LOCKED, True, 5000),
            ("LAPTOP_RENTAL", "iPads / Laptops (Rental)", "Hartford rental computing devices",
             "TECH", "DIVVY", SPEND_TYPE_MODE_SINGLE_LOCKED, True, 15000),
            ("ETHERNET_DROPS", "Ethernet Drops", "Hardline internet drops from venue",
             "HOTEL", "HOTEL_FEE", SPEND_TYPE_MODE_SINGLE_LOCKED, True, 7500),
            ("OFFICE_SUPPLIES", "Office Supplies", "General office supplies",
             "OTHER", "BANK", SPEND_TYPE_MODE_ALLOW_LIST, False, None),
        ]

        for code, name, desc, group_code, spend_code, spend_mode, is_fixed, unit_price in demo_accounts:
            group = groups_by_code.get(group_code)
            spend_type = spend_by_code.get(spend_code)

            db.session.add(
                ExpenseAccount(
                    code=code,
                    name=name,
                    description=desc,
                    approval_group_id=group.id if group else None,
                    default_spend_type_id=spend_type.id if spend_type else None,
                    spend_type_mode=spend_mode,
                    is_fixed_cost=is_fixed,
                    default_unit_price_cents=unit_price,
                    unit_price_locked=is_fixed,
                    is_active=True,
                )
            )

        db.session.commit()

    def ensure_demo_budget_data():
        """Combined seeder for all budget reference data."""
        ensure_demo_reference_data()
        ensure_demo_expense_accounts()

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

    def ensure_demo_users():
        from .models import User, UserRole, ApprovalGroup, ROLE_SUPER_ADMIN, ROLE_APPROVER

        ensure_demo_budget_data()
        ensure_demo_org_data()

        any_user = db.session.query(User).first()
        if any_user:
            return

        groups_by_code = {g.code: g for g in db.session.query(ApprovalGroup).all()}
        tech = groups_by_code.get("TECH")
        hotel = groups_by_code.get("HOTEL")
        if not tech or not hotel:
            raise RuntimeError("Demo ApprovalGroups missing: expected TECH and HOTEL to exist.")
        tech_group_id = tech.id
        hotel_group_id = hotel.id

        # role format: (role_code, work_type_id, approval_group_id)
        demo_users = [
            # Plain users (no special role)
            ("dev:pat", "pat@dev.local", "dev:pat", "Pat (No Dept)", True, []),

            # Arcades
            ("dev:alex", "alex@dev.local", "dev:alex", "Alex (Arcades DH)", True, []),
            ("dev:riley", "riley@dev.local", "dev:riley", "Riley (Arcades Editor)", True, []),
            ("dev:sam", "sam@dev.local", "dev:sam", "Sam (Arcades Viewer)", True, []),

            # Guests
            ("dev:jordan", "jordan@dev.local", "dev:jordan", "Jordan (Guests DH)", True, []),
            ("dev:casey", "casey@dev.local", "dev:casey", "Casey (Guests Editor)", True, []),

            # Mixed membership
            ("dev:morgan", "morgan@dev.local", "dev:morgan", "Morgan (Arcades View / Guests Edit)", True, []),

            # Approvers (scoped to approval group)
            ("dev:tech_approver", "tech.approver@dev.local", "dev:tech_approver", "Tech Approver (Demo)", True,
             [(ROLE_APPROVER, None, tech_group_id)]),
            ("dev:hotel_approver", "hotel.approver@dev.local", "dev:hotel_approver", "Hotel Approver (Demo)", True,
             [(ROLE_APPROVER, None, hotel_group_id)]),

            # Elevated
            ("dev:admin", "admin@dev.local", "dev:admin", "Admin (Demo)", True, [(ROLE_SUPER_ADMIN, None, None)]),
        ]

        for user_id, email, auth_subject, display_name, is_active, roles in demo_users:
            u = db.session.get(User, user_id)
            if not u:
                u = User(id=user_id)
                db.session.add(u)

            u.email = email
            u.auth_subject = auth_subject
            u.display_name = display_name
            u.is_active = is_active

            # roles: easiest is clear then recreate for demo users
            db.session.query(UserRole).filter_by(user_id=user_id).delete()
            for role_code, work_type_id, approval_group_id in roles:
                db.session.add(UserRole(
                    user_id=user_id,
                    role_code=role_code,
                    work_type_id=work_type_id,
                    approval_group_id=approval_group_id,
                ))

        db.session.commit()
        ensure_demo_department_memberships()

    def ensure_demo_org_data():
        from .models import Department, EventCycle

        # Seed EventCycles if empty
        any_cycle = db.session.query(EventCycle).first()
        if not any_cycle:
            cycles = [
                # code, name, active, default, sort
                ("SMF2026", "Super MAGFest 2026", True, True, 10),
                ("SMF2027", "Super MAGFest 2027", True, False, 20),
            ]
            for code, name, active, is_default, sort in cycles:
                db.session.add(
                    EventCycle(
                        code=code,
                        name=name,
                        is_active=active,
                        is_default=is_default,
                        sort_order=sort,
                    )
                )
            db.session.flush()

        # Seed Departments if empty
        any_dept = db.session.query(Department).first()
        if not any_dept:
            depts = [
                # code, name, active, sort
                ("TECHOPS", "TechOps", True, 10),
                ("HOTELS", "Hotels", True, 20),
                ("BROADCAST", "BroadcastOps", True, 30),
                ("FESTOPS", "FestOps", True, 40),
                ("SUPPLY", "SupplyOps", True, 50),
                ("REG", "Registration", True, 60),
                ("PANEL", "Panels", True, 70),
                ("GUEST", "Guests", True, 80),
                ("ARCADE", "Arcades", True, 90),
            ]
            for code, name, active, sort in depts:
                db.session.add(
                    Department(
                        code=code,
                        name=name,
                        is_active=active,
                        sort_order=sort,
                    )
                )

        db.session.commit()

    def ensure_demo_department_memberships():
        from .models import (
            User,
            Department,
            EventCycle,
            DepartmentMembership,
        )

        # Ensure org data exists (departments + cycles)
        ensure_demo_org_data()

        # --- fetch the event cycle we want to test ---
        cycle = (
            db.session.query(EventCycle)
            .filter(EventCycle.code == "SMF2026")
            .one()
        )

        # --- fetch departments we want to test ---
        dept_by_code = {
            d.code: d
            for d in db.session.query(Department)
            .filter(Department.code.in_(["ARCADE", "GUEST"]))
            .all()
        }

        missing = [c for c in ["ARCADE", "GUEST"] if c not in dept_by_code]
        if missing:
            raise RuntimeError(f"Missing demo departments: {missing}")

        def upsert_membership(
                *, user_id: str, dept_code: str, is_department_head: bool
        ):
            dept = dept_by_code[dept_code]

            row = (
                db.session.query(DepartmentMembership)
                .filter(DepartmentMembership.user_id == user_id)
                .filter(DepartmentMembership.department_id == dept.id)
                .filter(DepartmentMembership.event_cycle_id == cycle.id)
                .one_or_none()
            )

            if not row:
                row = DepartmentMembership(
                    user_id=user_id,
                    department_id=dept.id,
                    event_cycle_id=cycle.id,
                )
                db.session.add(row)

            row.is_department_head = bool(is_department_head)

        # --- membership plan (truth table) ---
        # Format: (user_id, dept_code, is_department_head)
        # Work type access is managed separately via DepartmentMembershipWorkTypeAccess
        membership_plan = [
            # Arcades
            ("dev:alex", "ARCADE", True),  # DH
            ("dev:riley", "ARCADE", False),  # editor
            ("dev:sam", "ARCADE", False),  # viewer

            # Guests
            ("dev:jordan", "GUEST", True),  # DH
            ("dev:casey", "GUEST", False),  # editor

            # Mixed: Arcades view + Guests edit
            ("dev:morgan", "ARCADE", False),
            ("dev:morgan", "GUEST", False),
        ]

        # Validate users exist (fail loudly if demo users aren't seeded)
        user_ids = [u[0] for u in membership_plan]
        found = {u.id for u in db.session.query(User.id).filter(User.id.in_(user_ids)).all()}
        missing_users = [uid for uid in user_ids if uid not in found]
        if missing_users:
            raise RuntimeError(f"Missing demo users for memberships: {missing_users}")

        # Apply plan
        for user_id, dept_code, is_dh in membership_plan:
            upsert_membership(
                user_id=user_id,
                dept_code=dept_code,
                is_department_head=is_dh,
            )

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
        return db.session.get(User, get_active_user_id())

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
        nav_is_budget_admin = False
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

            # Budget admin = super admin OR WORKTYPE_ADMIN for the BUDGET work type
            if _is_super:
                nav_is_budget_admin = True
            else:
                budget_wt = WorkType.query.filter_by(code="BUDGET").first()
                if budget_wt:
                    nav_is_budget_admin = UserRole.query.filter_by(
                        user_id=u.id,
                        role_code=ROLE_WORKTYPE_ADMIN,
                        work_type_id=budget_wt.id,
                    ).first() is not None

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
            "nav_is_budget_admin": nav_is_budget_admin,
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
            ensure_demo_users=ensure_demo_users,
            ensure_demo_budget_data=ensure_demo_budget_data,
            ensure_demo_org_data=ensure_demo_org_data,
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

    # --- Bootstrap Admins (runs once on first request) ---
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
        return render_template('errors/404.html', error=error), 404

    @app.errorhandler(500)
    def internal_error(error):
        # Rollback any pending database transaction to avoid connection issues
        db.session.rollback()
        return render_template('errors/500.html', error=error), 500

    @app.errorhandler(OperationalError)
    def database_connection_error(error):
        # Database connection issues (connection refused, timeout, etc.)
        db.session.rollback()
        app.logger.error(f"Database connection error: {error}")
        return render_template('errors/503.html', error=error if app.debug else None), 503

    @app.errorhandler(DatabaseError)
    def database_error(error):
        # Other database errors
        db.session.rollback()
        app.logger.error(f"Database error: {error}")
        return render_template('errors/500.html', error=error if app.debug else None), 500

    # In production, catch all unhandled exceptions
    if is_production:
        @app.errorhandler(Exception)
        def unhandled_exception(error):
            db.session.rollback()
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