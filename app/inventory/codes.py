"""Scanned-code normalization shared by labels, identifiers, and the resolver."""


def normalize_code(raw: str | None) -> str:
    """Return the matching form of a scanned code: trimmed and uppercased.

    Callers keep the raw text separately where it matters; vendor
    reconciliation uses the vendor's exact serial.
    """
    return (raw or "").strip().upper()
