"""ProxyFix trusts only the forwarded headers the Heroku router writes.

The router appends the real client address to X-Forwarded-For and sets -Proto
and -Port. It does not set -Host or -Prefix, so a client controls those.
"""
import pytest

from app import create_app

ISOLATED_ENV_VARS = ("APP_ENV", "BEHIND_PROXY", "DYNO")


@pytest.fixture
def make_app(monkeypatch):
    def _make(**env):
        for key in ISOLATED_ENV_VARS:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("SECRET_KEY", "test-only")
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        app = create_app()

        @app.get("/_test/whoami")
        def _whoami():
            from flask import request, url_for
            return {"addr": request.remote_addr,
                    "url": url_for("_whoami", _external=True)}

        return app
    return _make


def test_heroku_without_behind_proxy_logs_an_error(make_app, caplog):
    make_app(DYNO="web.1")

    assert "BEHIND_PROXY" in caplog.text


def test_heroku_with_behind_proxy_logs_no_error(make_app, caplog):
    make_app(DYNO="web.1", BEHIND_PROXY="true")

    assert "BEHIND_PROXY" not in caplog.text


def test_the_client_address_is_the_entry_the_router_appends(make_app):
    client = make_app(BEHIND_PROXY="true").test_client()

    data = client.get("/_test/whoami",
                      headers={"X-Forwarded-For": "1.2.3.4, 75.163.6.68"}).get_json()

    assert data["addr"] == "75.163.6.68"


def test_a_client_supplied_forwarded_host_is_ignored(make_app):
    client = make_app(BEHIND_PROXY="true").test_client()

    data = client.get("/_test/whoami", base_url="https://rms.example",
                      headers={"X-Forwarded-Host": "evil.example",
                               "X-Forwarded-Prefix": "/evil"}).get_json()

    assert data["url"] == "https://rms.example/_test/whoami"
