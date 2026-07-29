"""Environment variables that must reach app.config.

These knobs are read with current_app.config.get(KEY, DEFAULT), which succeeds
whether or not create_app() ever assigned the key — an unwired knob is silently
a no-op rather than an error. Both of these shipped unwired for a long time.
"""
import pytest

from app import create_app
from app.routes.auth import DEFAULT_ORG_DOMAINS, get_org_email_domains
from app.routes.work.helpers.checkout import (
    DEFAULT_CHECKOUT_TIMEOUTS,
    get_checkout_timeouts,
)

CONFIGURED_KEYS = ("CHECKOUT_TIMEOUTS", "ORG_EMAIL_DOMAINS")


@pytest.fixture
def make_app(monkeypatch):
    """Build an app with only the given env vars set for the keys under test."""
    def _make(**env):
        for key in CONFIGURED_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return create_app()
    return _make


def test_checkout_timeouts_unset_uses_builtin_defaults(make_app):
    with make_app().app_context():
        assert get_checkout_timeouts() == DEFAULT_CHECKOUT_TIMEOUTS


def test_checkout_timeouts_env_overrides_defaults(make_app):
    override = '{"APPROVER": 5, "SUPER_ADMIN": 999, "DEFAULT": 7}'
    with make_app(CHECKOUT_TIMEOUTS=override).app_context():
        assert get_checkout_timeouts() == {
            "APPROVER": 5,
            "SUPER_ADMIN": 999,
            "DEFAULT": 7,
        }


def test_checkout_timeouts_malformed_json_falls_back(make_app):
    # Must not raise at boot. The key is left unassigned so config.get()'s
    # default argument still fires — assigning None would break the callers,
    # which do timeouts.get(role, ...) on the result.
    app = make_app(CHECKOUT_TIMEOUTS="{not json")
    assert "CHECKOUT_TIMEOUTS" not in app.config
    with app.app_context():
        assert get_checkout_timeouts() == DEFAULT_CHECKOUT_TIMEOUTS


def test_org_email_domains_unset_uses_builtin_defaults(make_app):
    with make_app().app_context():
        assert get_org_email_domains() == DEFAULT_ORG_DOMAINS


def test_org_email_domains_env_overrides_defaults(make_app):
    # auth.py splits the raw string itself, including whitespace and case.
    with make_app(ORG_EMAIL_DOMAINS="example.org, Foo.NET").app_context():
        assert get_org_email_domains() == {"example.org", "foo.net"}