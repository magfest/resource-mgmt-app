"""Scanned-code normalization shared by labels, identifiers, and the resolver."""
import re

from app.models.constants import (
    INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAC, INV_IDENT_MAGFEST_TAG,
    INV_IDENT_SERIAL, INV_IDENT_VENDOR_TAG, MAGFEST_TAG_PREFIXES, MAGFEST_TAG_WIDTH,
)

_TAG_PATTERN = re.compile(r"([A-Z]+)(\d+)")
_MAC_SEPARATORS = re.compile(r"[:.\-\s]")
_MAC_PATTERN = re.compile(r"[0-9A-F]{12}")


def normalize_code(raw: str | None) -> str:
    """Return the matching form of a scanned code: trimmed and uppercased.

    Callers keep the raw text separately where it matters; vendor
    reconciliation uses the vendor's exact serial.
    """
    return (raw or "").strip().upper()


def magfest_tag(raw: str | None) -> str | None:
    """Return the canonical MAGFest tag, or None when the text is not one.

    A tag is a known prefix plus a number from 1 up to the tag width, so
    net0001 and NET00001 find the same unit.
    """
    match = _TAG_PATTERN.fullmatch(normalize_code(raw))
    if match is None or match.group(1) not in MAGFEST_TAG_PREFIXES:
        return None
    number = int(match.group(2))
    if not 0 < number < 10 ** MAGFEST_TAG_WIDTH:
        return None
    return f"{match.group(1)}{number:0{MAGFEST_TAG_WIDTH}d}"


def normalize_mac(raw: str | None) -> str | None:
    """Return a MAC as 12 uppercase hex digits, or None when it is not one."""
    digits = _MAC_SEPARATORS.sub("", normalize_code(raw))
    return digits if _MAC_PATTERN.fullmatch(digits) else None


def normalize_identifier(kind: str, raw: str) -> str:
    """Return the matching form of an identifier of the given kind.

    Raises:
        ValueError: The text is not a valid tag or MAC. The message is shown
            to the operator as written.
    """
    if kind == INV_IDENT_MAGFEST_TAG:
        tag = magfest_tag(raw)
        if tag is None:
            raise ValueError(f"{raw.strip()} is not a MAGFest tag.")
        return tag
    if kind == INV_IDENT_MAC:
        mac = normalize_mac(raw)
        if mac is None:
            raise ValueError(f"{raw.strip()} is not a MAC address.")
        return mac
    if kind in (INV_IDENT_FESTOPS_LABEL, INV_IDENT_VENDOR_TAG, INV_IDENT_SERIAL):
        return normalize_code(raw)
    raise ValueError(f"{kind} is not an identifier kind.")
