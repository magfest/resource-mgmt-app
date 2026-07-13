"""Scanner/bot flood protection — threshold blocking must not error."""


def test_scanner_threshold_blocks_ip_without_error(client):
    # 10 scanner-path 404s in the window trip the block threshold.
    # The 10th request executes the block+log branch in _record_ip_404;
    # before the fix that branch raises NameError (current_app undefined).
    for _ in range(10):
        resp = client.get("/wp-admin")
        assert resp.status_code in (403, 404)

    # Once blocked, subsequent requests get a clean 403.
    resp = client.get("/wp-admin")
    assert resp.status_code == 403
