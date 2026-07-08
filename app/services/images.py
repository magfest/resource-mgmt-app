"""
Supply catalog item images: validate, resize, strip EXIF, upload to S3.

Images are decoration, never load-bearing — callers must treat a missing
or failed image as a non-fatal condition (item saves without one).
Heroku's filesystem is ephemeral, hence S3 rather than local storage.
"""
from __future__ import annotations

import io
import uuid

import boto3
from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

# Format allowlist: PIL format name -> (file extension, mime type)
ALLOWED_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_DIMENSION = 600  # long-edge cap; single rendition (thumbnail = future)


class ImageValidationError(ValueError):
    """User-fixable problem with the uploaded file (type, size)."""


class ImageStorageError(RuntimeError):
    """S3-side failure — surface as a form error, keep the item."""


def _s3_client():
    # Dedicated per-service credentials, mirroring the SES pattern in
    # services/email.py — the generic AWS_* env vars stay free for other
    # boto3 clients (e.g. Secrets Manager). Falls back to the default
    # credential chain (IAM role, ~/.aws/credentials) if keys aren't set.
    access_key = current_app.config.get("SUPPLY_IMAGE_ACCESS_KEY")
    secret_key = current_app.config.get("SUPPLY_IMAGE_SECRET_KEY")
    region = current_app.config.get("SUPPLY_IMAGE_REGION", "us-east-1")
    if access_key and secret_key:
        return boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
    return boto3.client("s3", region_name=region)


def _bucket() -> str:
    bucket = current_app.config.get("SUPPLY_IMAGE_BUCKET")
    if not bucket:
        raise ImageStorageError(
            "SUPPLY_IMAGE_BUCKET is not configured; image uploads are disabled."
        )
    return bucket


def _uses_transparency(img: Image.Image) -> bool:
    """True only if the image has an alpha channel with at least one
    non-opaque pixel (or palette transparency). Many exporters add a fully
    opaque alpha channel — that alone shouldn't keep us on PNG."""
    if img.mode == "P":
        return "transparency" in img.info
    if img.mode in ("RGBA", "LA", "PA"):
        return img.getchannel("A").getextrema()[0] < 255
    return False


def process_and_upload_item_image(file_storage, item_id: int) -> str:
    """Validate + resize + upload one catalog image. Returns the public URL."""
    raw = file_storage.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ImageValidationError("Image is too large (max 5 MB).")
    if not raw:
        raise ImageValidationError("Uploaded file is empty.")

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Image.DecompressionBombError as exc:
        # Pixel-count bomb guard tripped — user-fixable, not a server bug.
        raise ImageValidationError("Image dimensions are too large.") from exc
    except (UnidentifiedImageError, OSError) as exc:
        # UnidentifiedImageError: not an image at all.
        # Bare OSError: valid header but corrupt/truncated body (from load()).
        raise ImageValidationError("File is not a supported image (JPEG, PNG, or WebP).") from exc

    fmt = img.format
    if fmt not in ALLOWED_FORMATS:
        raise ImageValidationError("File is not a supported image (JPEG, PNG, or WebP).")

    # Apply EXIF orientation before re-encoding drops the tag — otherwise
    # phone photos display sideways. Also drops EXIF (location data etc.).
    img = ImageOps.exif_transpose(img)

    # PNG is lossless, so a photo uploaded as PNG stays huge even at 600px.
    # Transparency is the only thing PNG buys us here — re-encode as JPEG
    # unless the alpha channel is actually used.
    if fmt == "PNG" and not _uses_transparency(img):
        fmt = "JPEG"
    ext, mime = ALLOWED_FORMATS[fmt]

    if fmt == "JPEG" and img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

    out = io.BytesIO()
    img.save(out, format=fmt)
    body = out.getvalue()

    bucket = _bucket()
    key = f"supply-items/{item_id}/{uuid.uuid4().hex}.{ext}"
    try:
        _s3_client().put_object(
            Bucket=bucket, Key=key, Body=body,
            ContentType=mime, CacheControl="public, max-age=86400",
        )
    except Exception as exc:  # boto raises many types; all are storage errors here
        raise ImageStorageError(f"Could not upload image to storage: {exc}") from exc

    return f"https://{bucket}.s3.amazonaws.com/{key}"


def delete_item_image(image_url: str) -> None:
    """Best-effort delete of a previously uploaded image. Only touches keys
    in our bucket — externally pasted URLs are left alone. Never raises."""
    try:
        bucket = _bucket()
    except ImageStorageError:
        return
    prefix = f"https://{bucket}.s3.amazonaws.com/"
    if not (image_url or "").startswith(prefix):
        return
    key = image_url[len(prefix):]
    try:
        _s3_client().delete_object(Bucket=bucket, Key=key)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to delete item image %s", key)
