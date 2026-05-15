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

## Core concepts

### Incremental crawling (collected range)

The `movies` table's `added` column holds the upload timestamp from rargb. The `collected` table stores a date range `[start, end]` — the oldest and newest `added` dates ever crawled. Since rargb `/movies/` is sorted newest-first:

- Items with `start < added < end` are inside the collected range and **skipped**.
- Items outside the range are **inserted**, and the range expands.
- The range is persisted **after every page**, so a crash loses at most one page of progress.
- Crawling stops when a page yields zero new items (all inside range).

### Year extraction

Regex `\b(19\d{2}|20[0-2]\d)\b` pulls the release year from the filename (e.g., `1999` from `The.Matrix.1999.1080p...`). Stored in the `year` column. Used by IMDb crawler to match the correct result.

### Layering

Strict separation: **`app.py`** = routes/web layer only, **`service.py`** = business logic, **`db.py` + `db_model/`** = data layer. Never import utils (Kafka, Bloom, etc.) directly into `app.py` — add a method to `MovieService` and call it from the route.

### Deduplication

Two layers:
1. **URL unique index** on `movies(url)` — prevents inserting the exact same torrent twice.
2. **Bloom filter** (`./data/bf.bin`) in the predict step — catches the same movie under different filenames/resolutions after title extraction.

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
