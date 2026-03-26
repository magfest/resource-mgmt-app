"""
Authentication routes for OAuth providers (Google and Keycloak).

Supports multiple OAuth providers, configurable via AUTH_PROVIDER env var:
- "google": Google OAuth (original implementation)
- "keycloak": Keycloak/OIDC
- "none" or unset: No OAuth (dev login only)
"""
from functools import wraps

from flask import Blueprint, redirect, url_for, session, flash, current_app, request, render_template
from authlib.integrations.flask_client import OAuth

from app import db

auth_bp = Blueprint('auth', __name__)

# Default organization domains (can be overridden via ORG_EMAIL_DOMAINS env var)
DEFAULT_ORG_DOMAINS = {'magfest.org', 'magwest.org', 'magstock.org'}


def get_org_email_domains() -> set:
    """Get the set of organization email domains from config or default."""
    config_domains = current_app.config.get('ORG_EMAIL_DOMAINS')
    if config_domains:
        # Support comma-separated string from env var
        if isinstance(config_domains, str):
            return {d.strip().lower() for d in config_domains.split(',') if d.strip()}
        return set(config_domains)
    return DEFAULT_ORG_DOMAINS


def is_magfest_email(email: str) -> bool:
    """Check if email is from an organization domain."""
    if not email or '@' not in email:
        return False
    domain = email.split('@')[-1].lower()
    return domain in get_org_email_domains()


def login_required(f):
    """Decorator to require user authentication.

    If user is not logged in:
    - Redirects to login page if OAuth is enabled
    - Returns 401 if no auth methods are available
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('active_user_id'):
            # Check if dev login would provide a default user
            if current_app.config.get('DEV_LOGIN_ENABLED'):
                # Dev mode - will get default user, allow through
                pass
            else:
                # No user and no dev default - must login
                flash('Please sign in to continue.', 'info')
                return redirect(url_for('auth.login_page'))
        return f(*args, **kwargs)
    return decorated_function


# OAuth client - initialized lazily
_oauth = None


def get_oauth():
    """Get or create the OAuth client with configured providers."""
    global _oauth
    if _oauth is None:
        _oauth = OAuth(current_app)

        auth_provider = current_app.config.get('AUTH_PROVIDER')

        # Register Google provider if configured
        if auth_provider == 'google':
            _oauth.register(
                name='google',
                client_id=current_app.config.get('GOOGLE_CLIENT_ID'),
                client_secret=current_app.config.get('GOOGLE_CLIENT_SECRET'),
                server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
                client_kwargs={
                    'scope': 'openid email profile'
                }
            )

        # Register Keycloak provider if configured
        if auth_provider == 'keycloak':
            keycloak_url = current_app.config.get('KEYCLOAK_URL')
            keycloak_realm = current_app.config.get('KEYCLOAK_REALM')
            _oauth.register(
                name='keycloak',
                client_id=current_app.config.get('KEYCLOAK_CLIENT_ID'),
                client_secret=current_app.config.get('KEYCLOAK_CLIENT_SECRET'),
                server_metadata_url=f'{keycloak_url}/realms/{keycloak_realm}/.well-known/openid-configuration',
                client_kwargs={
                    'scope': 'openid email profile'
                }
            )

    return _oauth


@auth_bp.get('/login')
def login_page():
    """Show login page with available authentication options.

    If only one auth method is available, skips the login page
    and redirects straight to the OAuth provider.
    """
    # If user is already logged in, redirect to home
    if session.get('active_user_id'):
        return redirect(url_for('home.index'))

    # If only one auth method, skip the button page and go straight to OAuth
    auth_provider = current_app.config.get('AUTH_PROVIDER')
    dev_login = current_app.config.get('DEV_LOGIN_ENABLED')
    if auth_provider and not dev_login:
        return redirect(url_for('auth.login'))

    return render_template('auth/login.html')


@auth_bp.get('/auth/login')
def login():
    """Initiate OAuth login with the configured provider."""
    auth_provider = current_app.config.get('AUTH_PROVIDER')

    if not auth_provider:
        flash('No authentication provider is configured', 'error')
        return redirect(url_for('home.index'))

    oauth = get_oauth()
    redirect_uri = url_for('auth.callback', _external=True)

    if auth_provider == 'google':
        return oauth.google.authorize_redirect(redirect_uri)
    elif auth_provider == 'keycloak':
        return oauth.keycloak.authorize_redirect(redirect_uri)
    else:
        flash(f'Unknown authentication provider: {auth_provider}', 'error')
        return redirect(url_for('home.index'))


@auth_bp.get('/auth/callback')
def callback():
    """Handle OAuth callback from the configured provider."""
    auth_provider = current_app.config.get('AUTH_PROVIDER')

    if not auth_provider:
        flash('No authentication provider is configured', 'error')
        return redirect(url_for('home.index'))

    if auth_provider == 'google':
        return _handle_google_callback()
    elif auth_provider == 'keycloak':
        return _handle_keycloak_callback()
    else:
        flash(f'Unknown authentication provider: {auth_provider}', 'error')
        return redirect(url_for('home.index'))


def _handle_google_callback():
    """Handle Google OAuth callback."""
    from app.models import User
    from app.security_audit import log_login_failure

    oauth = get_oauth()

    try:
        token = oauth.google.authorize_access_token()
    except Exception as e:
        current_app.logger.error(f'Google OAuth error: {e}')
        log_login_failure("oauth_error", provider="google")
        db.session.commit()
        flash('Authentication failed. Please try again.', 'error')
        return redirect(url_for('home.index'))

    # Get user info from Google
    user_info = token.get('userinfo')
    if not user_info:
        # Fallback: fetch from userinfo endpoint
        user_info = oauth.google.userinfo()

    email = user_info.get('email', '').lower().strip()
    if not email:
        log_login_failure("missing_email", provider="google")
        db.session.commit()
        flash('Could not retrieve email from Google account', 'error')
        return redirect(url_for('home.index'))

    # Check domain restriction
    allowed_domains = current_app.config.get('GOOGLE_ALLOWED_DOMAINS')
    if allowed_domains:
        email_domain = email.split('@')[-1] if '@' in email else ''
        if email_domain not in allowed_domains:
            log_login_failure("domain_restricted", email=email, provider="google")
            db.session.commit()
            flash(f'Sign-in is restricted to authorized email domains. '
                  f'Please use your organization email.', 'error')
            return redirect(url_for('auth.login_page'))

    subject = user_info.get('sub')
    display_name = user_info.get('name', email.split('@')[0])

    return _complete_login(email, subject, display_name, provider='google')


def _handle_keycloak_callback():
    """Handle Keycloak OAuth callback."""
    from app.models import User
    from app.security_audit import log_login_failure

    oauth = get_oauth()

    try:
        token = oauth.keycloak.authorize_access_token()
    except Exception as e:
        current_app.logger.error(f'Keycloak OAuth error: {e}')
        log_login_failure("oauth_error", provider="keycloak")
        db.session.commit()
        flash('Authentication failed. Please try again.', 'error')
        return redirect(url_for('home.index'))

    # Get user info from token or userinfo endpoint
    user_info = token.get('userinfo')
    if not user_info:
        # Fallback: fetch from userinfo endpoint
        user_info = oauth.keycloak.userinfo()

    email = user_info.get('email', '').lower().strip()
    if not email:
        log_login_failure("missing_email", provider="keycloak")
        db.session.commit()
        flash('Could not retrieve email from Keycloak account', 'error')
        return redirect(url_for('home.index'))

    # Keycloak subject ID
    subject = user_info.get('sub')

    # Keycloak provides various name fields
    display_name = (
        user_info.get('name') or
        user_info.get('preferred_username') or
        f"{user_info.get('given_name', '')} {user_info.get('family_name', '')}".strip() or
        email.split('@')[0]
    )

    return _complete_login(email, subject, display_name, provider='keycloak')


def _complete_login(email: str, subject: str, display_name: str, provider: str):
    """Complete the login process after OAuth validation.

    This is shared logic for all OAuth providers.

    User lookup priority:
    1. auth_subject (stable identifier from OAuth provider)
    2. email (fallback for migration of legacy users)

    Email updates:
    - Only updates email if new email is from a MAGFest domain
    - Prevents personal email addresses from overwriting org emails
    """
    from app.models import User
    from app.security_audit import log_login_success, log_login_failure
    import uuid

    user = None

    # Primary lookup: auth_subject (stable identifier)
    if subject:
        user = User.query.filter_by(auth_subject=subject).first()

    # Fallback lookup: email (for legacy users without auth_subject)
    if not user:
        user = db.session.query(User).filter(
            db.func.lower(User.email) == email
        ).first()

        if user:
            # Legacy user found by email - backfill auth_subject
            if not user.auth_subject or user.auth_subject.startswith('dev:'):
                current_app.logger.info(
                    f"Backfilling auth_subject for user {user.id} ({user.email})"
                )
                user.auth_subject = subject

    if user:
        # Existing user found
        # Update email only if new email is from MAGFest domain
        if is_magfest_email(email) and user.email.lower() != email.lower():
            current_app.logger.info(
                f"Updating email for user {user.id}: {user.email} -> {email}"
            )
            user.email = email

        if not user.is_active:
            log_login_failure("inactive_user", email=email, provider=provider)
            db.session.commit()
            flash('Your account is inactive. Please contact an administrator.', 'error')
            return redirect(url_for('home.index'))

        # Session fixation prevention: clear session before setting new auth
        session.clear()
        session['active_user_id'] = user.id

        # Log successful login
        log_login_success(user.id, provider, email)
        db.session.commit()

        flash(f'Welcome back, {user.display_name}!', 'success')
    else:
        # New user - create with no permissions
        new_user_id = str(uuid.uuid4())

        user = User(
            id=new_user_id,
            email=email,
            auth_subject=subject,
            display_name=display_name,
            is_active=True,
        )
        db.session.add(user)

        # Session fixation prevention: clear session before setting new auth
        session.clear()
        session['active_user_id'] = user.id

        # Log successful login (new user creation)
        log_login_success(user.id, provider, email)
        db.session.commit()

        flash(f'Welcome, {user.display_name}! Your account has been created. '
              'Contact an administrator to get access to departments.', 'info')

    return redirect(url_for('home.index'))


@auth_bp.get('/auth/logout')
def logout():
    """Log out the current user."""
    from app.security_audit import log_logout

    auth_provider = current_app.config.get('AUTH_PROVIDER')

    # Log logout before clearing session (we need the user_id)
    user_id = session.get('active_user_id')
    if user_id:
        log_logout(user_id)
        db.session.commit()

    # Clear session
    session.pop('active_user_id', None)
    session.pop('role_override', None)
    session.pop('role_override_approval_group_id', None)
    session.pop('selected_event_cycle_id', None)

    flash('You have been logged out.', 'info')

    # For Keycloak, optionally redirect to Keycloak logout to end SSO session
    # This is commented out for now - uncomment if full SSO logout is desired
    # if auth_provider == 'keycloak':
    #     keycloak_url = current_app.config.get('KEYCLOAK_URL')
    #     keycloak_realm = current_app.config.get('KEYCLOAK_REALM')
    #     redirect_uri = url_for('home.index', _external=True)
    #     return redirect(
    #         f'{keycloak_url}/realms/{keycloak_realm}/protocol/openid-connect/logout'
    #         f'?redirect_uri={redirect_uri}'
    #     )

    return redirect(url_for('home.index'))
