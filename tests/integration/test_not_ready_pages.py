"""Stop-gap /techops and /supplyops notice pages.

Leadership announced both work types before they shipped. These two URLs
exist only to catch that announcement's links; they are not the work types'
real entry points, which stay at /<event>/<dept>/techops and /supply.
"""


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_techops_notice_renders_for_anonymous_visitor(client):
    resp = client.get("/techops")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "#super-techops-requests" in body
    assert "October 1, 2026" in body


def test_supplyops_notice_renders_for_anonymous_visitor(client):
    resp = client.get("/supplyops")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "#super-supplyops-requests" in body
    assert "October 1, 2026" in body


def test_notice_pages_do_not_leak_the_other_work_types_channel(client):
    techops = client.get("/techops").get_data(as_text=True)
    supplyops = client.get("/supplyops").get_data(as_text=True)

    assert "#super-supplyops-requests" not in techops
    assert "#super-techops-requests" not in supplyops


def test_super_admin_gets_a_link_past_the_notice(client, seed_workflow_data):
    _login(client, "test:admin")

    resp = client.get("/techops")

    assert resp.status_code == 200
    assert "/admin/techops/requests/" in resp.get_data(as_text=True)


def test_anonymous_visitor_gets_no_admin_link(client):
    resp = client.get("/techops")

    assert "/admin/techops/requests/" not in resp.get_data(as_text=True)


def test_real_techops_portfolio_route_is_not_shadowed(app):
    """The stop-gap must not capture the department-scoped TechOps URL."""
    adapter = app.url_map.bind("localhost")

    endpoint, _ = adapter.match("/TST2026/TESTDEPT/techops")

    assert endpoint == "work.techops_portfolio_redirect"
