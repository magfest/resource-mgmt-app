"""
Tests for the supply catalog item image service (app/services/images.py).

S3 is mocked throughout — these tests never touch real AWS. Setup mirrors
the `app` fixture pattern used across tests/unit (see test_techops_models.py
and conftest.py): DATABASE_URL is forced to in-memory SQLite before
create_app() so we never touch the developer's dev database.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image
from unittest.mock import MagicMock
from werkzeug.datastructures import FileStorage


def _png_upload(width=1200, height=900, name="photo.png", mode="RGB", color="red"):
    buf = io.BytesIO()
    Image.new(mode, (width, height), color).save(buf, format="PNG")
    buf.seek(0)
    return FileStorage(stream=buf, filename=name, content_type="image/png")


def _mock_s3(app, monkeypatch):
    from app.services import images
    fake_s3 = MagicMock()
    monkeypatch.setattr(images, "_s3_client", lambda: fake_s3)
    app.config["SUPPLY_IMAGE_BUCKET"] = "test-bucket"
    return fake_s3


def test_upload_resizes_and_converts_opaque_png_to_jpeg(app, monkeypatch):
    # Opaque PNGs (photos) re-encode as JPEG — PNG's losslessness makes
    # photos huge even at 600px, and transparency is PNG's only value here.
    from app.services import images
    fake_s3 = _mock_s3(app, monkeypatch)

    with app.app_context():
        url = images.process_and_upload_item_image(_png_upload(), item_id=42)

    assert url.startswith("https://test-bucket.s3.amazonaws.com/supply-items/42/")
    assert url.endswith(".jpg")
    put = fake_s3.put_object.call_args.kwargs
    uploaded = Image.open(io.BytesIO(put["Body"]))
    assert max(uploaded.size) <= 600          # resized
    assert uploaded.format == "JPEG"
    assert put["ContentType"] == "image/jpeg"


def test_transparent_png_stays_png(app, monkeypatch):
    # An actually-used alpha channel (any pixel < 255) keeps PNG.
    from app.services import images
    fake_s3 = _mock_s3(app, monkeypatch)

    with app.app_context():
        url = images.process_and_upload_item_image(
            _png_upload(mode="RGBA", color=(255, 0, 0, 128)), item_id=7)

    assert url.endswith(".png")
    put = fake_s3.put_object.call_args.kwargs
    assert put["ContentType"] == "image/png"
    assert Image.open(io.BytesIO(put["Body"])).format == "PNG"


def test_fully_opaque_alpha_png_converts_to_jpeg(app, monkeypatch):
    # Exporters often add an alpha channel that's all-opaque — that alone
    # shouldn't keep us on PNG.
    from app.services import images
    fake_s3 = _mock_s3(app, monkeypatch)

    with app.app_context():
        url = images.process_and_upload_item_image(
            _png_upload(mode="RGBA", color=(255, 0, 0, 255)), item_id=8)

    assert url.endswith(".jpg")
    assert fake_s3.put_object.call_args.kwargs["ContentType"] == "image/jpeg"


def test_jpeg_upload_stays_jpeg(app, monkeypatch):
    from app.services import images
    fake_s3 = _mock_s3(app, monkeypatch)

    buf = io.BytesIO()
    Image.new("RGB", (800, 800), "blue").save(buf, format="JPEG")
    buf.seek(0)
    jpg = FileStorage(stream=buf, filename="photo.jpg", content_type="image/jpeg")

    with app.app_context():
        url = images.process_and_upload_item_image(jpg, item_id=9)

    assert url.endswith(".jpg")
    assert fake_s3.put_object.call_args.kwargs["ContentType"] == "image/jpeg"


def test_upload_rejects_wrong_type(app):
    from app.services.images import process_and_upload_item_image, ImageValidationError
    bad = FileStorage(stream=io.BytesIO(b"%PDF-1.4 not an image"),
                      filename="doc.pdf", content_type="application/pdf")
    with app.app_context(), pytest.raises(ImageValidationError):
        process_and_upload_item_image(bad, item_id=1)


def test_upload_rejects_truncated_image(app):
    # Valid PNG header but corrupt/truncated body: Image.open() succeeds,
    # img.load() raises bare OSError — must surface as ImageValidationError,
    # not escape as an untyped exception (future 500).
    from app.services.images import process_and_upload_item_image, ImageValidationError
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "blue").save(buf, format="PNG")
    truncated = buf.getvalue()[: len(buf.getvalue()) // 2]
    bad = FileStorage(stream=io.BytesIO(truncated),
                      filename="cut.png", content_type="image/png")
    with app.app_context(), pytest.raises(ImageValidationError):
        process_and_upload_item_image(bad, item_id=1)


def test_upload_rejects_oversize(app):
    from app.services.images import process_and_upload_item_image, ImageValidationError, MAX_UPLOAD_BYTES
    big = FileStorage(stream=io.BytesIO(b"x" * (MAX_UPLOAD_BYTES + 1)),
                      filename="big.png", content_type="image/png")
    with app.app_context(), pytest.raises(ImageValidationError):
        process_and_upload_item_image(big, item_id=1)
