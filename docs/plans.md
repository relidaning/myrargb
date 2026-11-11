# Recorded work items

Consolidated from the former root lists. Sections retain their original statuses
and may describe older versions; confirm against current code before acting.


## From TODOS.md

# Todo-list

This file records bugs/todos found during processing.

- [x] **Database locked** — added `PRAGMA journal_mode=WAL` + `busy_timeout=5000` to `src/db/db.py`. (2026-05-15)
- [x] **Too many open files** — reduced IMDb consumer threads from 5 to 1 in `src/app.py`. (2026-05-15)
- [x] **Pagination not working** — added `src/utils/pager_utils.py`, offset/count support, conditional Next link. (2026-05-15)
- [x] **Year extraction on insert instead of predict** — moved `extract_year()` to `crawl_rargb()` before insert, removed from `predict()`. (2026-05-15)
- [x] **Predict consumer stalling** — database lock was the cause, fixed with WAL mode. (2026-05-15)
- [x] **IMDb consumer stalling** — same lock cause + 5 Playwright instances exhausting FDs. (2026-05-15)
- [x] **Logging missing function name** — added `%(funcName)s` to format. (2026-05-15)
- [x] **Year column missing in UI** — added to table header, rows, and CSS grid. (2026-05-15)
- [ ] **ImdbCrawler gives up on first result mismatch** — `src/crawler.py:94-98`: `return None` should be `continue` to try next `<li>`.
- [x] **Keyword search is dead** — removed `keyword` from routes, service, template, JS, and address bar. (2026-05-15)
- [x] **Dead page input** — removed manual rargb page input; incremental crawl always starts from page 1. (2026-05-15)
- [x] **Train button always visible** — now hidden until 20+ title_accurate samples are ready. (2026-05-15)
- [x] **Pagination missing total pages** — added page number shortcuts and "Page X / Y" display. (2026-05-15)
- [ ] **No driver cleanup for Selenium variants** — `SeleniumBrowerDriver` / `UndetectedChromeBrowserDriver` rely on `__del__`.
- [ ] **Heavy work on GET routes** — `/crawl_rargb`, `/predict`, `/crawl_imdb`, `/deduplicate` re-run on refresh/prefetch.


## From todo.md

# Optimization TODO

## Critical Bugs

- [x] **#1** Thread safety: `db.cur` / `db.conn` are shared across all threads (Flask + 3 Kafka consumers + background crawl). Concurrent `execute()` calls interleave cursor state, causing corrupted results or crashes. Fix: thread-local connections in `src/db/db.py`.

- [x] **#2** SQL injection: `repository.py:69` uses f-string interpolation in DELETE — `f"WHERE id = {id}"`. Use parameterized `WHERE id = ?` instead.

- [x] **#3** IMDb year mismatch bails on first result instead of trying next: `src/crawler.py:94` does `return None` on year mismatch, abandoning the remaining results. Should `continue` to the next `<li>`.

- [ ] **#4** Bloom filter silently deletes movies with no title: `bloom_utils.py:16` — `hasItem("")` returns `True`, so rows with empty titles are treated as duplicates and deleted. Guard should return `False` for empty strings.

## Architecture Issues

- [ ] **#5** Duplicated workflow WHERE logic: `get_items` and `count_items` in `service.py` both contain copy-pasted `if workflow == ...` blocks. Extract to a private `_workflow_where(workflow)` helper.

- [ ] **#6** Layering violation: `src/app.py:12` imports `_bloom` from `db.service` directly, and the `/deduplicate` route does raw DB operations. This logic belongs in `MovieService.deduplicate()`.

- [x] **#7** New `ProducerUtil()` on every `predict()` call (`service.py:232`). Creates a new Kafka producer per message. Should be a module-level singleton.

- [x] **#8** `ProducerUtil._available_cache` never resets (`kafka_utils.py`). If Kafka is down at startup, the app permanently believes Kafka is unavailable until restart. Reset the cache on `KafkaException` in `produce()`.

## Performance

- [x] **#9** New Playwright browser per crawled page: `service.py:129` creates a new `RargbCrawler()` inside the `while True` loop. Browser startup is expensive. Instantiate once before the loop.

- [x] **#10** New Playwright browser per IMDb lookup: `src/crawler.py:57` calls `DriverFactory().create_driver()` inside `ImdbCrawler.crawl()`. One browser launch per movie. Driver should be created in `__init__` and reused.

- [x] **#11** New `MovieService()` per Kafka message: `src/handler.py` instantiates a fresh service (and two repositories) for every consumed message. Use a module-level singleton.

## Minor

- [ ] **#12** `collected` table missing explicit `id` column: DDL in `src/db/db.py` defines `(start text, end text)`. `BaseRepository.find_one(id)` queries `WHERE id = ?` relying on SQLite implicit `rowid`. Define the column explicitly.

- [x] **#13** Duplicate `assert a is not None` in `RargbCrawler` (`src/crawler.py:38,47`). The second assertion fires on the same variable. Remove it.

- [x] **#14** Noisy `INFO` logging in `RargbCrawler` (`src/crawler.py:45`). Logs every scraped item at `INFO` level; bulk crawls produce hundreds of lines. Change to `DEBUG`.
