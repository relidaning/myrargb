"""Shared title key for IMDb verification and duplicate detection."""


def title_key(title: str | None) -> str:
    """Lowercase, then remove whitespace and literal periods."""
    return ''.join((title or '').lower().split()).replace('.', '')
