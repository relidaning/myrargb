"""Pagination helpers — column whitelist, offset/page math, count query."""

_ALLOWED_COLUMNS = {
    "id", "filename", "size", "title", "url", "score", "genre",
    "poster", "marked", "title_accurate", "trained_flag", "added", "year",
}

PER_PAGE = 20


class PagerError(ValueError):
    pass


def validate_order_by(order_by: str) -> str:
    """Whitelist-validate an ORDER BY clause to prevent SQL injection.

    Raises PagerError on unrecognized columns or directions.
    """
    clean = []
    for part in order_by.split(","):
        part = part.strip()
        tokens = part.split()
        if not tokens or tokens[0].lower() not in _ALLOWED_COLUMNS:
            raise PagerError(f"Invalid column in order_by: {part!r}")
        col = tokens[0]
        direction = "ASC"
        if len(tokens) > 1:
            direction = tokens[1].upper()
            if direction not in ("ASC", "DESC"):
                raise PagerError(f"Invalid direction in order_by: {part!r}")
        clean.append(f"{col} {direction}")
    return ", ".join(clean)


def page_offset(page: int) -> int:
    return (page - 1) * PER_PAGE


def has_next_page(total: int, page: int) -> bool:
    return page * PER_PAGE < total
