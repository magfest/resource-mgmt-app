"""Scanned-code normalization shared by labels, identifiers, and the resolver.

Also holds the shape of a sign-in card value, so the ledger can refuse one as a
unit identifier without importing app.inventory.sign_in, which imports the ledger.
"""
import re

from app.models.constants import (
    INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAC, INV_IDENT_MAGFEST_TAG,
    INV_IDENT_SERIAL, INV_IDENT_VENDOR_TAG,
)

_MAC_SEPARATORS = re.compile(r"[:.\-\s]")
_MAC_PATTERN = re.compile(r"[0-9A-F]{12}")

CARD_PREFIX = "OP-"
# Crockford base32 drops I, L and O, which misread as 1 and 0, and U to avoid
# accidental words. A card never prints its value as text.
CARD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CARD_BODY_LENGTH = 12
_CARD_PATTERN = re.compile(
    rf"{re.escape(CARD_PREFIX)}[{CARD_ALPHABET}]{{{CARD_BODY_LENGTH}}}")
SIGN_IN_CARD = "That is a sign-in card."


def normalize_code(raw: str | None) -> str:
    """Return the matching form of a scanned code: trimmed and uppercased.

    Callers keep the raw text separately where it matters; vendor
    reconciliation uses the vendor's exact serial.
    """
    return (raw or "").strip().upper()


def is_card_code(raw: str | None) -> bool:
    """Return True when the scanned value has the shape of a sign-in card."""
    return _CARD_PATTERN.fullmatch(normalize_code(raw)) is not None


def normalize_mac(raw: str | None) -> str | None:
    """Return a MAC as 12 uppercase hex digits, or None when it is not one."""
    digits = _MAC_SEPARATORS.sub("", normalize_code(raw))
    return digits if _MAC_PATTERN.fullmatch(digits) else None


def normalize_identifier(kind: str, raw: str) -> str:
    """Return the matching form of an identifier of the given kind.

    Raises:
        ValueError: The text is not a valid MAC. The message is shown
            to the operator as written.
    """
    if kind == INV_IDENT_MAC:
        mac = normalize_mac(raw)
        if mac is None:
            raise ValueError(f"{raw.strip()} is not a MAC address.")
        return mac
    # MAGFest tags have no fixed format: LAT100081 and FEST00001 are both in use,
    # and new prefixes appear without notice. A tag matches on its exact text.
    if kind in (INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAGFEST_TAG,
                INV_IDENT_VENDOR_TAG, INV_IDENT_SERIAL):
        return normalize_code(raw)
    raise ValueError(f"{kind} is not a kind of number.")
