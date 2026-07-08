"""
Tests for image upload on the admin supply-item create/update routes
(app/routes/admin/supply_items.py).

The image *service* itself (validation, resizing, S3 upload) is exercised in
tests/unit/test_image_service.py; here we only verify the route wires the
uploaded file / remove-image checkbox to
process_and_upload_item_image/delete_item_image correctly, and that a
validation failure from the service degrades gracefully (item still saves,
error flashed, image_url left untouched).

The route imports the image-service functions lazily inside the handler
body (`from app.services.images import ...`), so monkeypatching has to
target the *source* module (`app.services.images`) rather than the route
module -- the route module has no module-level names to patch.
"""
from __future__ import annotations

import io

from app import db
from app.models import SupplyCategory, SupplyItem, User, UserRole, WorkType, ROLE_SUPER_ADMIN


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _seed_admin_and_category(app):
    """Seed a SUPER_ADMIN user, the SUPPLY work type, and one active supply
    category. The supply admin routes gate via require_supply_admin, which
    resolves the SUPPLY WorkType row (404 if not configured)."""
    with app.app_context():
        admin = User(
            id="test:admin", email="admin@test.local",
            display_name="Test Admin", is_active=True,
        )
        db.session.add(admin)
        db.session.flush()
        db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SUPER_ADMIN))

        db.session.add(WorkType(code="SUPPLY", name="Supply Orders", is_active=True))

        category = SupplyCategory(
            code="OFFICE", name="Office Supplies", is_active=True,
        )
        db.session.add(category)
        db.session.commit()
        return category.id


def test_create_with_image_file_sets_image_url(app, client, monkeypatch):
    """POST create with an image file -> item created, image_url set to the
    mocked service return value."""
    category_id = _seed_admin_and_category(app)

    from app.services import images
    fake_url = "https://test-bucket.s3.amazonaws.com/supply-items/999/fake.png"
    monkeypatch.setattr(
        images, "process_and_upload_item_image",
        lambda file_storage, item_id: fake_url,
    )

    _login(client, "test:admin")

    resp = client.post(
        "/admin/config/supply-items/new",
        data={
            "item_name": "Stapler",
            "category_id": str(category_id),
            "unit": "each",
            "image_file": (io.BytesIO(b"fake-bytes"), "photo.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    item = SupplyItem.query.filter_by(item_name="Stapler").one()
    assert item.image_url == fake_url


def test_update_with_remove_image_clears_url_and_deletes(app, client, monkeypatch):
    """POST update with remove_image=1 -> image_url None and
    delete_item_image called with the old URL."""
    category_id = _seed_admin_and_category(app)

    old_url = "https://test-bucket.s3.amazonaws.com/supply-items/1/old.png"
    with app.app_context():
        item = SupplyItem(
            category_id=category_id, item_name="Tape", unit="each",
            image_url=old_url,
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    deleted_urls = []
    from app.services import images
    monkeypatch.setattr(
        images, "delete_item_image", lambda url: deleted_urls.append(url),
    )

    _login(client, "test:admin")

    resp = client.post(
        f"/admin/config/supply-items/{item_id}",
        data={
            "item_name": "Tape",
            "category_id": str(category_id),
            "unit": "each",
            "remove_image": "1",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    reloaded = db.session.get(SupplyItem, item_id)
    assert reloaded.image_url is None
    assert deleted_urls == [old_url]


def test_update_without_touching_photo_preserves_image_url(app, client):
    """POST update with no image_file, no remove_image -> image_url untouched.

    Regression guard: the form has no image_url text input (photos are set
    only via upload), so the update route must not read image_url from the
    form -- doing so would silently wipe the photo on every ordinary edit.
    """
    category_id = _seed_admin_and_category(app)

    existing_url = "https://test-bucket.s3.amazonaws.com/supply-items/5/keep.png"
    with app.app_context():
        item = SupplyItem(
            category_id=category_id, item_name="Clipboard", unit="each",
            image_url=existing_url,
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

    _login(client, "test:admin")

    resp = client.post(
        f"/admin/config/supply-items/{item_id}",
        data={
            "item_name": "Clipboard (letter size)",
            "category_id": str(category_id),
            "unit": "each",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    reloaded = db.session.get(SupplyItem, item_id)
    assert reloaded.item_name == "Clipboard (letter size)"
    assert reloaded.image_url == existing_url


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


def test_create_with_invalid_image_still_creates_item_and_flashes(app, client, monkeypatch):
    """POST create where the mock raises ImageValidationError -> item still
    created, image_url None, response flashes the error."""
    category_id = _seed_admin_and_category(app)

    from app.services import images

    def _raise(file_storage, item_id):
        raise images.ImageValidationError("too big")

    monkeypatch.setattr(images, "process_and_upload_item_image", _raise)

    _login(client, "test:admin")

    resp = client.post(
        "/admin/config/supply-items/new",
        data={
            "item_name": "Notebook",
            "category_id": str(category_id),
            "unit": "each",
            "image_file": (io.BytesIO(b"fake-bytes"), "photo.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"too big" in resp.data

    item = SupplyItem.query.filter_by(item_name="Notebook").one()
    assert item.image_url is None
