"""Home page renders for a budget admin after the dead stats block was removed."""


def test_home_renders_for_budget_admin(client, seed_workflow_data):
    with client.session_transaction() as sess:
        sess["active_user_id"] = "test:admin"

    resp = client.get("/")
    assert resp.status_code == 200
