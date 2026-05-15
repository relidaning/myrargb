# Sessions

## 2026-05-15 — myrargb-optmization-searching

**Goal:** Make the crawling/search logic more efficient. The old approach used a year-based keyword threaded through the entire pipeline (Kafka → crawl → predict → IMDb), causing overlapping/duplicate crawls.

**Accomplished:**

1. **Removed keyword from the pipeline.** Kafka messages no longer carry `keyword` or `page`. Year extracted from filename via regex `\b(19\d{2}|20[0-2]\d)\b` and stored in new `year` column. IMDb uses `movie.year` for matching.

2. **Switched crawl URL** from `rargb.to/search/` (which returned mixed content with empty search) to `rargb.to/movies/` (pure movie listing, newest-first).

3. **Replaced single watermark with collected date range.** `collected` table stores `[start, end]` (oldest/newest `added` dates seen). Range expands as items are inserted, persisted after every page. Crash-resumption: items inside range are skipped on restart.

4. **Added URL unique index** on `movies(url)` to prevent duplicate inserts at DB level.

5. **Added Kafka availability gate.** `crawl_rargb` checks Kafka reachability upfront — refuses to crawl if broker is down, keeping the pipeline event-driven.

6. **Added backlog routes.** `/produce/predict` and `/produce/imdb` re-produce Kafka messages for items stuck without titles/scores (e.g., from earlier crawls where Kafka was down).

7. **Added layer for inline/batch processing.** `/predict` and `/crawl_imdb` routes for processing items without Kafka (fallback). `/deduplicate` route for Bloom filter cleanup.

8. **Added handler error handling** (`handler.py`) — wrapped callbacks in try/except to prevent daemon thread death on a single bad item.

9. **Updated docs** — README.md, CLAUDE.md, SESSIONS.md.

**Unresolved (see TODOS.md):**

- Predict consumer stalls after processing ~150 items. Root cause unknown — no errors logged.
- IMDb consumer has ~2500 lag and appears stuck. Many items hit `NoneType` errors or 15s timeouts, but 5 threads should make progress.
- Year extraction should move to crawl time (before insert) instead of predict time.
