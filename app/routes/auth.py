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
    """Show login page with available authentication options."""
    # If user is already logged in, redirect to home
    if session.get('active_user_id'):
        return redirect(url_for('home.index'))

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

    oauth = get_oauth()

    try:
        token = oauth.google.authorize_access_token()
    except Exception as e:
        current_app.logger.error(f'Google OAuth error: {e}')
        flash('Authentication failed. Please try again.', 'error')
        return redirect(url_for('home.index'))

    # Get user info from Google
    user_info = token.get('userinfo')
    if not user_info:
        # Fallback: fetch from userinfo endpoint
        user_info = oauth.google.userinfo()

    email = user_info.get('email', '').lower().strip()
    if not email:
        flash('Could not retrieve email from Google account', 'error')
        return redirect(url_for('home.index'))

    # Check domain restriction
    allowed_domains = current_app.config.get('GOOGLE_ALLOWED_DOMAINS')
    if allowed_domains:
        email_domain = email.split('@')[-1] if '@' in email else ''
        if email_domain not in allowed_domains:
            flash(f'Sign-in is restricted to authorized email domains. '
                  f'Please use your organization email.', 'error')
            return redirect(url_for('auth.login_page'))

    subject = user_info.get('sub')
    display_name = user_info.get('name', email.split('@')[0])

    return _complete_login(email, subject, display_name, provider='google')


def _handle_keycloak_callback():
    """Handle Keycloak OAuth callback."""
    from app.models import User

    oauth = get_oauth()

    try:
        token = oauth.keycloak.authorize_access_token()
    except Exception as e:
        current_app.logger.error(f'Keycloak OAuth error: {e}')
        flash('Authentication failed. Please try again.', 'error')
        return redirect(url_for('home.index'))

    # Get user info from token or userinfo endpoint
    user_info = token.get('userinfo')
    if not user_info:
        # Fallback: fetch from userinfo endpoint
        user_info = oauth.keycloak.userinfo()

    email = user_info.get('email', '').lower().strip()
    if not email:
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
    """
    from app.models import User
    import uuid

    # Try to find existing user by email
    user = db.session.query(User).filter(
        db.func.lower(User.email) == email
    ).first()

    if user:
        # Existing user - update their auth subject if not set or was dev
        if not user.auth_subject or user.auth_subject.startswith('dev:'):
            user.auth_subject = subject
            db.session.commit()

        if not user.is_active:
            flash('Your account is inactive. Please contact an administrator.', 'error')
            return redirect(url_for('home.index'))

        session['active_user_id'] = user.id
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
        db.session.commit()

        session['active_user_id'] = user.id
        flash(f'Welcome, {user.display_name}! Your account has been created. '
              'Contact an administrator to get access to departments.', 'info')

    # Clear any role override from previous session
    session.pop('role_override', None)
    session.pop('role_override_approval_group_id', None)

    return redirect(url_for('home.index'))


@auth_bp.get('/auth/logout')
def logout():
    """Log out the current user."""
    auth_provider = current_app.config.get('AUTH_PROVIDER')

    # Clear session
    session.pop('active_user_id', None)
    session.pop('role_override', None)
    session.pop('role_override_approval_group_id', None)

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
