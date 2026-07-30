"""APP_ENV resolution, and the flags that must not be enable-able in production.

Ten hardening measures in create_app() are opt-in on `is_production`, which is a
single comparison against APP_ENV (app/__init__.py:38-46): required SECRET_KEY
and DATABASE_URL, SESSION_COOKIE_SECURE, the 30-minute idle timeout, HSTS, the
catch-all error handler, and the force-disabling of BETA_TESTING_MODE and
DEV_LOGIN_ENABLED. So APP_ENV is the single point of failure for all ten, and
"unset" must not be readable as "development".

BETA_TESTING_MODE in particular gates super-admin user impersonation and the
role-override dropdown (app/routes/dev.py:116, :162, :191). Those routes never
call _require_dev_environment(), so the config flag is the only thing standing
between a super-admin and a live session swap. It shipped for a long time as
`beta_mode == "true" or (not is_production and ...)` — a form whose first
disjunct had no environment guard at all, so BETA_TESTING_MODE=true in
production turned impersonation on against real data.

SESSION_COOKIE_SECURE is used as the observable proxy for `is_production`:
app/__init__.py:78 assigns it that value directly and nothing else touches it.
"""
import pytest

from app import KNOWN_APP_ENVS, create_app

# Cleared before each build so the developer's own environment cannot leak in.
ISOLATED_ENV_VARS = ("APP_ENV", "BETA_TESTING_MODE", "DEV_LOGIN_ENABLED", "SECRET_KEY")


@pytest.fixture
def make_app(monkeypatch):
    """Build an app with only the given env vars set for the flags under test.

    DATABASE_URL is left alone: tests/conftest.py pins it to in-memory SQLite at
    import time, which also satisfies create_app's production requirement.
    """
    def _make(**env):
        for key in ISOLATED_ENV_VARS:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return create_app()
    return _make


def _prod(make_app, **env):
    # SECRET_KEY is mandatory in production (app/__init__.py:51-52).
    env.setdefault("APP_ENV", "production")
    return make_app(SECRET_KEY="test-only", **env)


def _dev(make_app, **env):
    env.setdefault("APP_ENV", "development")
    return make_app(**env)


# --- APP_ENV must be explicit and recognized ---

def test_unset_app_env_refuses_to_boot(make_app):
    # The whole point: absence must not be silently read as "development".
    with pytest.raises(RuntimeError, match="APP_ENV"):
        make_app()


def test_unrecognized_app_env_refuses_to_boot(make_app):
    # "prod" would previously have meant development, disabling all ten guards.
    with pytest.raises(RuntimeError, match="APP_ENV"):
        make_app(APP_ENV="prod")


@pytest.mark.parametrize("value", ["Production", "PRODUCTION", "  production  "])
def test_app_env_is_normalized_before_comparison(make_app, value):
    # Line 38 had no .strip()/.lower(), unlike the flag parsing further down,
    # so casing or stray whitespace silently downgraded the environment.
    app = make_app(APP_ENV=value, SECRET_KEY="test-only")
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["BETA_TESTING_MODE"] is False


@pytest.mark.parametrize("value", ["development", "testing"])
def test_recognized_non_production_envs_boot_without_secret_key(make_app, value):
    assert value in KNOWN_APP_ENVS
    assert make_app(APP_ENV=value).config["SESSION_COOKIE_SECURE"] is False


# --- Flags that must stay off in production ---

def test_beta_testing_mode_cannot_be_forced_on_in_production(make_app):
    assert _prod(make_app, BETA_TESTING_MODE="true").config["BETA_TESTING_MODE"] is False


def test_beta_testing_mode_off_in_production_when_unset(make_app):
    assert _prod(make_app).config["BETA_TESTING_MODE"] is False


def test_beta_testing_mode_defaults_on_outside_production(make_app):
    # Dev convenience: on unless explicitly opted out.
    assert _dev(make_app).config["BETA_TESTING_MODE"] is True


def test_beta_testing_mode_opt_out_honored_outside_production(make_app):
    assert _dev(make_app, BETA_TESTING_MODE="false").config["BETA_TESTING_MODE"] is False


def test_dev_login_cannot_be_forced_on_in_production(make_app):
    # The sibling flag this one was aligned with — pinned so the pair stays symmetric.
    assert _prod(make_app, DEV_LOGIN_ENABLED="true").config["DEV_LOGIN_ENABLED"] is False
