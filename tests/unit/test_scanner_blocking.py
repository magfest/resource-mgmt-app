"""Scanner/bot flood protection — threshold blocking must not error."""


def test_scanner_threshold_blocks_ip_without_error(client):
    # Each scanner request is counted twice — once in the block_scanners
    # before_request hook and again in the track_404_ips after_request hook
    # (which Flask still runs on before_request-short-circuited responses) —
    # so the effective block threshold is crossed around the 5th request,
    # well within this loop of 10. The request that crosses it executes the
    # block+log branch in _record_ip_404; before the fix that branch raises
    # NameError (current_app undefined).
    for _ in range(10):
        resp = client.get("/wp-admin")
        assert resp.status_code in (403, 404)

    # Once blocked, subsequent requests get a clean 403.
    resp = client.get("/wp-admin")
    assert resp.status_code == 403


def test_rotating_a_forged_forwarded_for_does_not_evade_the_block(client):
    """The client writes every X-Forwarded-For entry except the last one Heroku
    appends, so the block keys on the address ProxyFix resolves instead."""
    for n in range(10):
        client.get("/wp-admin", headers={"X-Forwarded-For": f"203.0.113.{n}"})

    assert client.get("/").status_code == 403


def test_a_forged_forwarded_for_cannot_block_another_address(client):
    for _ in range(10):
        client.get("/wp-admin", headers={"X-Forwarded-For": "198.51.100.7"})

    resp = client.get("/", environ_base={"REMOTE_ADDR": "198.51.100.7"})

    assert resp.status_code != 403


def test_a_rejected_scanner_hit_logs_no_csp_error(client, caplog):
    client.get("/wp-admin")

    assert "CSP nonce missing" not in caplog.text
