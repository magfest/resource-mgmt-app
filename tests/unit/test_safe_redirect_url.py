"""Open-redirect guard for user-supplied return URLs."""
from app.routes.admin.helpers import safe_redirect_url


def test_rejects_backslash_protocol_relative():
    # Browsers normalize "/\" to "//" — must be treated as external.
    assert safe_redirect_url("/\\evil.com", fallback="/safe") == "/safe"
    assert safe_redirect_url("//evil.com", fallback="/safe") == "/safe"


def test_allows_normal_relative_paths():
    assert safe_redirect_url("/admin/budget/", fallback="/safe") == "/admin/budget/"
    assert safe_redirect_url("https://evil.com", fallback="/safe") == "/safe"
