"""The tabbed request page, shared by every work type.

Budget grew tabs first, wrapped in `if can_view_audit` with a duplicate
stacked layout underneath for everyone else. Two copies of the same content
drift. This component takes the audience test per tab instead, so there is
one layout and a requester simply sees one tab fewer.
"""
from __future__ import annotations

import pytest

from app import create_app


@pytest.fixture(scope="module")
def env():
    """The real Jinja environment, so the macro is exercised as it ships."""
    return create_app().jinja_env


def _render(env, tabs, body="{% if tab %}panel:{{ tab }}{% endif %}"):
    template = env.from_string(
        '{% from "components/_request_tabs.html" import request_tabs with context %}'
        "{% call(tab) request_tabs(TABS) %}" + body + "{% endcall %}"
    )
    return template.render(TABS=tabs, csp_nonce="test")


def test_each_declared_tab_gets_a_button_and_a_panel(env):
    html = _render(env, [{"id": "lines", "label": "Requested services"},
                         {"id": "comments", "label": "Comments"}])
    assert 'data-tab="lines"' in html
    assert 'data-tab="comments"' in html
    assert "panel:lines" in html
    assert "panel:comments" in html


def test_a_tab_whose_audience_test_fails_does_not_render(env):
    """`when` drops the tab entirely rather than rendering it empty. This is
    what replaces Budget's whole-page branch."""
    html = _render(env, [{"id": "lines", "label": "Requested services"},
                         {"id": "audit", "label": "Audit", "when": False}])
    assert 'data-tab="lines"' in html
    assert 'data-tab="audit"' not in html
    assert "panel:audit" not in html


def test_a_tab_with_no_when_key_renders(env):
    """Most tabs are for everyone. Requiring `when` on each would make the
    common case noisy."""
    html = _render(env, [{"id": "comments", "label": "Comments"}])
    assert 'data-tab="comments"' in html


def test_the_first_visible_tab_is_the_active_one(env):
    """Not the first declared one. Hiding the audit tab must not leave the
    page opening on a panel nobody can see."""
    html = _render(env, [{"id": "audit", "label": "Audit", "when": False},
                         {"id": "lines", "label": "Requested services"}])
    active = html.index("tab-btn active")
    assert html.index('data-tab="lines"', active - 400) > 0
    assert "tab-btn active" in html
    assert html.count("tab-btn active") == 1


def test_a_count_renders_only_when_given(env):
    html = _render(env, [{"id": "lines", "label": "Lines", "count": 7},
                         {"id": "comments", "label": "Comments"}])
    assert ">7<" in html
    # The comments tab declared no count, so it gets no badge.
    comments = html[html.index('data-tab="comments"'):]
    assert "badge" not in comments.split("</button>")[0]
