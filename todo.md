# Optimization TODO

## Critical Bugs

- [x] **#1** Thread safety: `db.cur` / `db.conn` are shared across all threads (Flask + 3 Kafka consumers + background crawl). Concurrent `execute()` calls interleave cursor state, causing corrupted results or crashes. Fix: thread-local connections in `db/db.py`.

- [x] **#2** SQL injection: `repository.py:69` uses f-string interpolation in DELETE — `f"WHERE id = {id}"`. Use parameterized `WHERE id = ?` instead.

- [x] **#3** IMDb year mismatch bails on first result instead of trying next: `crawler.py:94` does `return None` on year mismatch, abandoning the remaining results. Should `continue` to the next `<li>`.

- [ ] **#4** Bloom filter silently deletes movies with no title: `bloom_utils.py:16` — `hasItem("")` returns `True`, so rows with empty titles are treated as duplicates and deleted. Guard should return `False` for empty strings.

## Architecture Issues

- [ ] **#5** Duplicated workflow WHERE logic: `get_items` and `count_items` in `service.py` both contain copy-pasted `if workflow == ...` blocks. Extract to a private `_workflow_where(workflow)` helper.

- [ ] **#6** Layering violation: `app.py:12` imports `_bloom` from `db.service` directly, and the `/deduplicate` route does raw DB operations. This logic belongs in `MovieService.deduplicate()`.

- [x] **#7** New `ProducerUtil()` on every `predict()` call (`service.py:232`). Creates a new Kafka producer per message. Should be a module-level singleton.

- [x] **#8** `ProducerUtil._available_cache` never resets (`kafka_utils.py`). If Kafka is down at startup, the app permanently believes Kafka is unavailable until restart. Reset the cache on `KafkaException` in `produce()`.

## Performance

- [x] **#9** New Playwright browser per crawled page: `service.py:129` creates a new `RargbCrawler()` inside the `while True` loop. Browser startup is expensive. Instantiate once before the loop.

- [x] **#10** New Playwright browser per IMDb lookup: `crawler.py:57` calls `DriverFactory().create_driver()` inside `ImdbCrawler.crawl()`. One browser launch per movie. Driver should be created in `__init__` and reused.

- [x] **#11** New `MovieService()` per Kafka message: `handler.py` instantiates a fresh service (and two repositories) for every consumed message. Use a module-level singleton.

## Minor

- [ ] **#12** `collected` table missing explicit `id` column: DDL in `db/db.py` defines `(start text, end text)`. `BaseRepository.find_one(id)` queries `WHERE id = ?` relying on SQLite implicit `rowid`. Define the column explicitly.

- [x] **#13** Duplicate `assert a is not None` in `RargbCrawler` (`crawler.py:38,47`). The second assertion fires on the same variable. Remove it.

- [x] **#14** Noisy `INFO` logging in `RargbCrawler` (`crawler.py:45`). Logs every scraped item at `INFO` level; bulk crawls produce hundreds of lines. Change to `DEBUG`.
