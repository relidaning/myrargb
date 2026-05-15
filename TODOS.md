# Todo-list

This file records bugs/todos found during processing.

- [x] **Database locked** — added `PRAGMA journal_mode=WAL` + `busy_timeout=5000` to `db/db.py`. (2026-05-15)
- [x] **Too many open files** — reduced IMDb consumer threads from 5 to 1 in `app.py`. (2026-05-15)
- [x] **Pagination not working** — added `utils/pager_utils.py`, offset/count support, conditional Next link. (2026-05-15)
- [x] **Year extraction on insert instead of predict** — moved `extract_year()` to `crawl_rargb()` before insert, removed from `predict()`. (2026-05-15)
- [x] **Predict consumer stalling** — database lock was the cause, fixed with WAL mode. (2026-05-15)
- [x] **IMDb consumer stalling** — same lock cause + 5 Playwright instances exhausting FDs. (2026-05-15)
- [x] **Logging missing function name** — added `%(funcName)s` to format. (2026-05-15)
- [x] **Year column missing in UI** — added to table header, rows, and CSS grid. (2026-05-15)
- [ ] **ImdbCrawler gives up on first result mismatch** — `crawler.py:94-98`: `return None` should be `continue` to try next `<li>`.
- [x] **Keyword search is dead** — removed `keyword` from routes, service, template, JS, and address bar. (2026-05-15)
- [x] **Dead page input** — removed manual rargb page input; incremental crawl always starts from page 1. (2026-05-15)
- [x] **Train button always visible** — now hidden until 20+ title_accurate samples are ready. (2026-05-15)
- [x] **Pagination missing total pages** — added page number shortcuts and "Page X / Y" display. (2026-05-15)
- [ ] **No driver cleanup for Selenium variants** — `SeleniumBrowerDriver` / `UndetectedChromeBrowserDriver` rely on `__del__`.
- [ ] **Heavy work on GET routes** — `/crawl_rargb`, `/predict`, `/crawl_imdb`, `/deduplicate` re-run on refresh/prefetch.
