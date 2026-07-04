# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                          # Install dependencies
uv run python app.py             # Run the app (Flask dev server on :5000)
docker compose up --build        # Full stack: app + Kafka + Kafka UI
./send_msg_to_kafka.sh           # Trigger incremental crawl via HTTP
```

## Architecture

Flask web app that crawls rargb.to `/movies/` for movie torrents, extracts clean titles with a fine-tuned T5-small model, and enriches metadata from IMDb.

**Pipeline (Kafka-driven, no keyword threading):**
```
Kafka: crawl_rargb trigger (empty payload)
  → browse rargb.to/movies/ page by page (newest-first, Playwright)
  → insert new items into SQLite
  → produce {movie} to predict topic
  → T5 model extracts title, regex extracts year from filename
  → Bloom-filter dedup on predicted title
  → produce {movie} to crawl_imdb topic
  → IMDb search by title + year → store poster, score
```

## Key Concepts

### Incremental crawling (collected range)

The `movies` table's `added` column holds the upload timestamp from rargb. The `collected` table stores a date range `[start, end]` — the oldest and newest `added` dates ever crawled. Since rargb `/movies/` is sorted newest-first:

- Items with `start < added < end` are inside the collected range and **skipped**.
- Items outside the range are **inserted**, and the range expands.
- The range is persisted **after every page**, so a crash loses at most one page of progress.
- Crawling stops when a page yields zero new items (all inside range).

### Year extraction

Regex `\b(19\d{2}|20[0-2]\d)\b` pulls the release year from the filename (e.g., `1999` from `The.Matrix.1999.1080p...`). Stored in the `year` column. Used by IMDb crawler to match the correct result.

When an IMDb search result's year doesn't match, the crawler must `continue` to the next `<li>` — not `return None` — so remaining candidates are still checked.

IMDb search results may omit score or year fields; guard with inline conditionals (`li_score.text.strip() if li_score else None`) to avoid `AttributeError` on missing elements.

### Layering

Strict separation: **`app.py`** = routes/web layer only, **`service.py`** = business logic, **`db.py` + `db_model/`** = data layer. Routes needing the Bloom filter import `_bloom` from `db.service` (to share the singleton) — not from `utils.bloom_utils` directly.

All SQL queries use parameterized placeholders (bound arguments) — never string interpolation. Torrent filenames are untrusted input and would otherwise allow injection via `db/db.py` and `db/repository.py`.

### Deduplication

Two layers:
1. **URL unique index** on `movies(url)` — prevents inserting the exact same torrent twice.
2. **Bloom filter** (`./data/bf.bin`) in the predict step — catches the same movie under different filenames/resolutions after title extraction.

**Bloom filter rules (critical):**
- `_bloom = BloomUtils()` is a **module-level singleton** in `db/service.py`. Never instantiate per-request — in-memory state accumulates across Kafka messages; re-instantiating loses it.
- `predict()` must call `_bloom.add(title)` **after** `movieRepository.update()`, or duplicates arriving later in the same session slip through.
- `normalize()` in `bloom_utils.py` uses regex `[^a-z0-9]+` → space, so `a.b.c`, `a b c`, and `a-b-c` all hash identically. It replaced the old `deal_string()` which only stripped dots.
- Always return early when `predicted_m.title` is empty before the bloom check — `hasItem("")` returns `True` and would otherwise silently delete the row.

### Thread safety

`db/db.py` uses thread-local SQLite connections. Flask + 7 Kafka consumer daemon threads share one process — a shared `connection/cursor` would interleave concurrent `execute()` calls and corrupt results or crash.

### Module-level singletons

- `service.py`: `_bloom` (BloomUtils) and `_producer` (ProducerUtil) are module-level singletons — never instantiate per-request or per-message.
- `service.py`: `ImdbCrawler` is a lazy singleton — `_imdb_crawler = None` at module level, initialized on first use by `_get_imdb_crawler()`; driver created in `__init__` and reused across `crawl()` calls.
- `handler.py`: `_service` (MovieService) is a module-level singleton — not re-instantiated per Kafka message.
- `RargbCrawler` is instantiated once per crawl invocation, outside the page loop (not per page), and **must be closed**: `crawl_rargb()` in `db/service.py` wraps the page loop in `try/finally: crawler.close()`. `RargbCrawler.close()` and a no-op default `BrowserDriver.close()` on the ABC were added after a prior OOM incident — see [Known open issues](#known-open-issues) and browser cleanup note below.
- `PlaywrightDriver` creates the browser and context once in `__init__` and reuses them across all `fetch()` calls — stealth scripts and browser args are module-level constants, not per-request setup. Unlike `SeleniumBrowerDriver`/`UndetectedChromeBrowserDriver` (which clean up via `__del__` → `driver.quit()`), `PlaywrightDriver` has no destructor — its Chromium subprocess only ever closes via an explicit `close()` call, so any new code path that creates a `RargbCrawler`/`PlaywrightDriver` must close it (`ImdbCrawler`'s browser is the one intentional exception — it's a lazy singleton meant to stay open for the app's lifetime).

### Fine-tuning gate

`app.py`'s `index()` computes `trainable_count` (movies marked and ready for T5 fine-tuning) and `finetunable` (True when ≥20). Both are passed to the template — `finetunable` gates the fine-tune button in the UI.

### Known open issues

- **Layering note**: `app.py` imports `_bloom` from `db.service` directly for the `/deduplicate` route. Ideally this logic belongs in `MovieService.deduplicate()`.
- **`collected` table no explicit `id`**: DDL is `(start text, end text)`, relying on SQLite implicit `rowid`. `BaseRepository.find_one(id)` queries `WHERE id = ?` against this rowid.
- **Workflow WHERE duplication**: `get_items` and `count_items` in `service.py` have identical copy-pasted `if workflow == ...` blocks. Candidate for a `_workflow_where()` helper.
- **`bloom_utils.hasItem("")` returns `True`**: the latent risk is mitigated by the caller-side guard in `predict()` (`if not predicted_m.title: return`), but the guard lives only in `service.py` — other callers of `hasItem` must also guard before passing an empty string.
- **BS4 reference-cycle memory growth (fixed 2026-07-02)**: BS4 tags hold `.parent`/sibling back-references, so parsed trees are reference cycles reclaimable only by the cyclic GC, not refcounting. Under a fast back-to-back loop over hundreds of pages (e.g. draining a large `crawl_imdb` backlog), cycles piled up faster than GC scheduled a collection and process RSS ballooned — observed reaching ~5GB and pushing host memory to near-OOM (2026-07-02). Fix applied: both `RargbCrawler.crawl()` and `ImdbCrawler.crawl()` wrap soup usage in `try/finally: soup.decompose()`, so cycles are severed on every exit path (return, break, raise) and each page is reclaimed immediately by refcounting. `ImdbCrawler` also converts extracted `.string` values to plain `str` so returned `Movie` objects hold no `NavigableString` back-references into the tree. Any new BS4 call site must follow the same pattern.
- **`crawl_rargb` produce failures leave orphaned DB rows**: when `_producer.produce()` for the `predict` topic fails (e.g. transient Kafka outage), `crawl_rargb()` logs and moves on — the item is already inserted but no Kafka message is ever sent for it, so the consumer never picks it up (Kafka lag stays 0; the backlog is invisible to lag metrics). Existing routes `/produce/predict` and `/produce/imdb` (backed by `produce_predict_backlog()` / `produce_imdb_backlog()`) can be triggered manually to backfill. No automatic retry exists yet — a periodic background thread calling these on a timer (matching the daemon-consumer-thread pattern) was proposed but not built.

## Key files

| File | Role |
|---|---|
| `app.py` | Flask entry point, 6 routes, starts 7 Kafka consumer daemon threads |
| `handler.py` | Kafka message deserialization → `MovieService` delegation |
| `db/service.py` | `MovieService`: crawl/predict/imdb orchestration, collected-range logic |
| `db/repository.py` | `BaseRepository[T]` with generic SQLite CRUD |
| `db/db.py` | Singleton `MyRargbDB`, table creation, column migrations |
| `db_model/__init__.py` | Pydantic models with `@table(name)` decorator |
| `crawler.py` | `RargbCrawler` (Playwright → BeautifulSoup), `ImdbCrawler` (IMDb mobile) |
| `model/model.py` | T5-small wrapper (HuggingFace), fine-tuning on `filename → title_accurate` |
| `browserdriver/driver.py` | `PlaywrightDriver` with stealth scripts (default), `SeleniumBrowerDriver`, `UndetectedChromeBrowserDriver` |
| `workflow.py` | `Workflow` enum for query filtering (TRAINING/PREDICT/SCORING/QUERYING/DEDUPLICATION) |
| `utils/kafka_utils.py` | `ProducerUtil`, `ConsumerUtil` (manual-commit polling loop) |
| `utils/bloom_utils.py` | `BloomUtils` wrapping `bloom-filter` lib, persisted to `./data/bf.bin` |

## Database

SQLite at `./data/myrargb.db`. Key tables:

- **movies** — `id, filename, size, title, url, score, genre, poster, marked, title_accurate, trained_flag, added, year`
- **collected** — `id, start, end` (date range of crawled items, used for incremental resume)
- **config** — `id, key, value` (currently unused; formerly held watermark)

Model fine-tuning checkpoint at `./data/my_finetuned_t5/`.

## Infrastructure

Docker Compose: app (Playwright image), Kafka (KRaft mode), Kafka UI (`:9090`). Kafka topics: `xyz.lidaning.myrargb.topics.crawl_rargb`, `.predict`, `.crawl_imdb`. Consumer groups: `xyz.lidaning.myrargb.consumers.*`.

`ProducerUtil._available_cache` is reset to `None` on `KafkaException` in `produce()` — if Kafka is temporarily down at startup, recovery is detected automatically on the next produce call without a restart.

**GPU**: The app container uses `runtime: nvidia` (`NVIDIA_VISIBLE_DEVICES=all`) — T5 inference requires an NVIDIA GPU; the container will fail to start without one. In practice (observed 2026-07-02 via `nvidia-smi` during a predict backlog drain) the T5 model runs CPU-only — GPU showed 0 processes and near-idle usage despite the runtime being configured — so predict throughput and host CPU/RAM load should be attributed to CPU inference, not assumed to be offloaded to CUDA.

**Memory / allocator**: `MALLOC_ARENA_MAX=2` is set in `docker-compose.yml` for the app container. Without it, glibc's per-thread malloc arenas (7 consumer threads + Flask) retain freed heap instead of returning it to the OS — after the 2026-07-02 backlog drain, RSS plateaued at ~4 GiB even though idle sampling showed no active leak (the `soup.decompose()` fix had stopped real growth). Judge memory health by whether RSS is flat at idle, not by whether it drops back to the ~1 GiB startup baseline.

**Proxy**: `HTTP_PROXY` / `HTTPS_PROXY` are set to `http://172.17.0.1:10808` (host machine via Docker bridge) so the container can reach rargb.to and IMDb. Without a working proxy at that address, crawling will fail silently.
