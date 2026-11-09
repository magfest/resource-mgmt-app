"""The CSP img-src directive follows the configured Supply image bucket.

The image service is covered in tests/unit/test_image_service.py; the About
tab's picture handling is covered in tests/inventory/test_item_page.py.
"""
from __future__ import annotations


def test_csp_img_src_includes_configured_supply_bucket(app, client):
    """The CSP img-src directive must include the S3 bucket host when
    SUPPLY_IMAGE_BUCKET is configured -- otherwise browsers block catalog
    photos in-page even though the objects are publicly readable."""
    app.config["SUPPLY_IMAGE_BUCKET"] = "test-bucket"
    resp = client.get("/health")
    csp = resp.headers.get("Content-Security-Policy", "")
    img_src = next(
        (d.strip() for d in csp.split(";") if d.strip().startswith("img-src")), "")
    assert "https://test-bucket.s3.amazonaws.com" in img_src

    # And without a bucket configured, img-src stays 'self'-only.
    app.config["SUPPLY_IMAGE_BUCKET"] = None
    csp = client.get("/health").headers.get("Content-Security-Policy", "")
    img_src = next(
        (d.strip() for d in csp.split(";") if d.strip().startswith("img-src")), "")
    assert "s3.amazonaws.com" not in img_src
