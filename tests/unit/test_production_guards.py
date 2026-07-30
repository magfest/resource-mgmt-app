"""Flags that must not be enable-able when APP_ENV=production.

BETA_TESTING_MODE gates super-admin user impersonation and the role-override
dropdown (app/routes/dev.py:116, :162, :191). Those routes never call
_require_dev_environment(), so the config flag is the only thing standing
between a super-admin and a live session swap. It shipped for a long time as
`beta_mode == "true" or (not is_production and ...)` — a form where the first
disjunct had no environment guard at all, so BETA_TESTING_MODE=true in
production turned impersonation on against real data.
"""
import pytest

from app import create_app

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
    # SECRET_KEY is mandatory in production (app/__init__.py:36-37).
    return make_app(APP_ENV="production", SECRET_KEY="test-only", **env)


def test_beta_testing_mode_cannot_be_forced_on_in_production(make_app):
    assert _prod(make_app, BETA_TESTING_MODE="true").config["BETA_TESTING_MODE"] is False


def test_beta_testing_mode_off_in_production_when_unset(make_app):
    assert _prod(make_app).config["BETA_TESTING_MODE"] is False


def test_beta_testing_mode_defaults_on_outside_production(make_app):
    # Dev convenience: on unless explicitly opted out.
    assert make_app().config["BETA_TESTING_MODE"] is True


def test_beta_testing_mode_opt_out_honored_outside_production(make_app):
    assert make_app(BETA_TESTING_MODE="false").config["BETA_TESTING_MODE"] is False


def test_dev_login_cannot_be_forced_on_in_production(make_app):
    # The sibling flag this one was aligned with — pinned so the pair stays symmetric.
    assert _prod(make_app, DEV_LOGIN_ENABLED="true").config["DEV_LOGIN_ENABLED"] is False
