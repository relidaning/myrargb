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


def total_pages(total: int) -> int:
    return (total + PER_PAGE - 1) // PER_PAGE


def page_range(page: int, max_page: int):
    """Return a list of page numbers to display, with None for ellipsis gaps."""
    if max_page <= 7:
        return list(range(1, max_page + 1))

    pages = [1]
    start = max(2, page - 2)
    end = min(max_page - 1, page + 2)
    if start > 2:
        pages.append(None)
    pages.extend(range(start, end + 1))
    if end < max_page - 1:
        pages.append(None)
    pages.append(max_page)
    return pages
