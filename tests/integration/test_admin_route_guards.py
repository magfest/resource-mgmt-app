"""Every admin_final route refuses a user with no admin role.

This blueprint has no shared gate and cannot have one. It serves two
audiences: most routes answer to budget admins, the email ones to super admins
only. Gating the blueprint at either level would lock out one group or hand
the other the stored email bodies.

Each view therefore calls its own guard on its first line, which holds until
someone adds a route and forgets. This test walks the URL map rather than
trusting a reviewer to notice.
"""
import re

import pytest

from app.routes.admin_final import admin_final_bp

# Placeholder segments by werkzeug converter class. An int route given "x"
# fails to match and returns 404, which would read as an unguarded route.
_DUMMY = {
    "IntegerConverter": "1",
    "FloatConverter": "1.0",
    "PathConverter": "x",
    "UnicodeConverter": "x",
}


def _fill(rule):
    """Return a concrete URL for a rule, or None if a converter is unknown."""
    url = rule.rule
    for name, converter in rule._converters.items():
        value = _DUMMY.get(type(converter).__name__)
        if value is None:
            return None
        url = re.sub(rf"<[^<>]*\b{re.escape(name)}>", value, url)
    return None if "<" in url else url


def _admin_rules(app):
    return [
        rule for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith(f"{admin_final_bp.name}.")
    ]


def test_the_url_map_actually_has_admin_routes(app):
    """Guard the guard. A typo in the endpoint prefix would empty the sweep
    below and leave a test that passes by testing nothing."""
    rules = _admin_rules(app)
    assert len(rules) > 30
    assert all(_fill(rule) for rule in rules), "a rule this test cannot fill"


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_no_admin_route_answers_a_user_without_a_role(app, client, seed_workflow_data, method):
    """test:reviewer is authenticated and holds no admin role.

    Each request carries its own X-Forwarded-For. block_scanners blocks an IP
    after 10 404s in 60 seconds and then returns 403 to everything, which is
    the status this test asserts; sharing one IP would let the sweep pass
    because the app stopped answering rather than because the guards ran.
    """
    with client.session_transaction() as session:
        session["active_user_id"] = "test:reviewer"

    unguarded = []
    for index, rule in enumerate(_admin_rules(app)):
        if method not in rule.methods:
            continue
        url = _fill(rule)
        response = client.open(
            url, method=method,
            headers={"X-Forwarded-For": f"10.{index // 65536}.{index // 256 % 256}.{index % 256}"},
        )
        if response.status_code != 403:
            unguarded.append(f"{rule.endpoint} {method} {url} -> {response.status_code}")

    assert not unguarded, (
        "These admin routes did not return 403 for a user with no admin role. "
        "Every view in this blueprint must call require_admin or "
        "require_budget_admin before it does anything else:\n  "
        + "\n  ".join(unguarded)
    )
