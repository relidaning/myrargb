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
- `RargbCrawler` is instantiated once per crawl invocation, outside the page loop (not per page).
- `PlaywrightDriver` creates the browser and context once in `__init__` and reuses them across all `fetch()` calls — stealth scripts and browser args are module-level constants, not per-request setup.

### Fine-tuning gate

`app.py`'s `index()` computes `trainable_count` (movies marked and ready for T5 fine-tuning) and `finetunable` (True when ≥20). Both are passed to the template — `finetunable` gates the fine-tune button in the UI.

### Known open issues

- **Layering note**: `app.py` imports `_bloom` from `db.service` directly for the `/deduplicate` route. Ideally this logic belongs in `MovieService.deduplicate()`.
- **`collected` table no explicit `id`**: DDL is `(start text, end text)`, relying on SQLite implicit `rowid`. `BaseRepository.find_one(id)` queries `WHERE id = ?` against this rowid.
- **Workflow WHERE duplication**: `get_items` and `count_items` in `service.py` have identical copy-pasted `if workflow == ...` blocks. Candidate for a `_workflow_where()` helper.
- **`bloom_utils.hasItem("")` returns `True`**: the latent risk is mitigated by the caller-side guard in `predict()` (`if not predicted_m.title: return`), but the guard lives only in `service.py` — other callers of `hasItem` must also guard before passing an empty string.

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

**GPU**: The app container uses `runtime: nvidia` (`NVIDIA_VISIBLE_DEVICES=all`) — T5 inference requires an NVIDIA GPU; the container will fail to start without one.

**Proxy**: `HTTP_PROXY` / `HTTPS_PROXY` are set to `http://172.17.0.1:10808` (host machine via Docker bridge) so the container can reach rargb.to and IMDb. Without a working proxy at that address, crawling will fail silently.
