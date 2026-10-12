# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                          # Install dependencies
uv run python app.py             # Run the app (Flask dev server on :5000)
docker compose up --build        # Full stack: app + Kafka + Kafka UI
./send_msg_to_kafka.sh           # Trigger incremental crawl via HTTP
```

**Dev workflow**: `docker-compose.override.yml` (auto-loaded) bind-mounts all source, templates, and static files into the container. Template/CSS/JS edits go live on browser refresh (`TEMPLATES_AUTO_RELOAD` is set in `app.py`); Python edits need `docker compose restart myrargb`; only dependency changes (`pyproject.toml`/`uv.lock`) need `--build`. `.dockerignore` excludes `data/` and `*.log` from the image — don't remove those entries; `app.log` once ballooned the image by 267MB.

## Architecture

Flask web app with two independent verticals sharing one process/DB:
1. Crawls rargb.to `/movies/` for movie torrents, extracts clean titles with a fine-tuned T5-small model, and enriches metadata from IMDb.
2. Tracks TV show premieres/schedules via the TVMaze API (plain `requests`, no key or browser needed) so users can browse a calendar, search, follow shows, and get notified of new episodes.

**Movie pipeline (Kafka-driven, no keyword threading):**
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

**TV pipeline (Kafka-driven, periodic poll since TVMaze has no push mechanism):**
```
Kafka: crawl_tvmaze trigger (empty payload; also fired every 6h by a background thread)
  → crawl_schedule(): pull TVMaze TV + web schedules for [-7, +14] days
    → upsert into tv_schedule cache table (the calendar reads only from this cache)
    → prune entries older than 7 days
  → check_followed_episodes(): poll each followed show's next-episode
    → notify (tv_notifications row) only if the next-episode id changed since last check
```

## Key Concepts

### Incremental crawling (collected range)

The `movies` table's `added` column holds the upload timestamp from rargb. The `collected` table stores a date range `[start, end]` — the oldest and newest `added` dates ever crawled. Since rargb `/movies/` is sorted newest-first:

- Items with `start < added < end` are inside the collected range and **skipped**.
- Items outside the range are **inserted**, and the range expands.
- The range is persisted **after every page**, so a crash loses at most one page of progress.
- **Known bug — an interrupted catch-up crawl skips movies for good**: because the new `end` is saved after the first (newest) page, a crawl that stops before reaching the old `end` (a page fetch with no result table returns `[]` and breaks the loop, an exception, a restart) leaves the uncrawled stretch inside the range, and every later crawl skips it. Simulated 2026-10-10 on a copy of the live DB (fake 11-page site, 250 new movies, page 4 fails once): 75 of 250 stored, reruns add none. Fix was PR #11, squash-merged to `origin/master` as `9e9f2a2` on 2026-10-10 (review reproduced it on copies of the live DB: 75 → 250 of 250 after one rerun, also when the page raises or the crawl is interrupted twice; uninterrupted and idle crawls unchanged) — not live until local `master` takes it in and the app is restarted: `end` moves only once the crawl has reached the old `end`, and until then a rerun walks past the known URLs above it instead of stopping on them (cost: the newest pages are fetched again on a rerun). It does not repair a gap that already exists, a page that always fails still blocks the catch-up, and an interrupted first-ever crawl on an empty DB (no old `end` to reach) still stops for good. Take it in before the first crawl after a long stop — on 2026-10-10 the saved range ended at 2026-08-15.
- Crawling stops when a page yields zero new items (all inside range).

### Year extraction

Regex `\b(19\d{2}|20[0-2]\d)\b` pulls the release year from the filename (e.g., `1999` from `The.Matrix.1999.1080p...`). Stored in the `year` column. Used by IMDb crawler to match the correct result.

The year only reaches the IMDb step if the `crawl_imdb` message carries it: `predict()` used to queue just id + title, so `ImdbCrawler` saw `year = None` and took the first search result whatever its year (`Nobody's Fool` 2018 got the 1994 film's score). PR #8 (squash-merged to `origin/master` as `341889d` on 2026-10-05 — not live until local `master` takes it in and the app is restarted) queues the full row, and adds `IMDB_YEAR_TOLERANCE = 1` in `crawler.py`: an exact-year result wins, otherwise a result one year off is accepted (filename and IMDb years often differ by one, festival vs general release); set it to 0 for exact matching. Consequences: rows whose only candidates are other-year films now end `unmatched` instead of taking a wrong film's score (review sample of 30: 3 such rows, 1 corrected, 1 gained); rows scored before the fix are not re-checked (clear `score` on rows with a year, then `/produce/imdb`); and `/produce/imdb` on `origin/master` still sends rows without the year because `get_items()` doesn't select it — the uncommitted local edit adds it.

When an IMDb search result's year doesn't match, the crawler must `continue` to the next `<li>` — not `return None` — so remaining candidates are still checked.

IMDb search results may omit score or year fields; guard with inline conditionals (`li_score.text.strip() if li_score else None`) to avoid `AttributeError` on missing elements.

### Layering

Strict separation: **`app.py`** = routes/web layer only, **`service.py`** = business logic, **`db.py` + `db_model/`** = data layer. Routes needing the Bloom filter import `_bloom` from `db.service` (to share the singleton) — not from `utils.bloom_utils` directly.

All SQL queries use parameterized placeholders (bound arguments) — never string interpolation. Torrent filenames are untrusted input and would otherwise allow injection via `db/db.py` and `db/repository.py`.

### Deduplication

Two layers:
1. **URL unique index** on `movies(url)` — prevents inserting the exact same torrent twice.
2. **Bloom filter** (`./data/bf.bin`) in the predict step — catches the same movie under different filenames/resolutions after title extraction. It is probabilistic: on local `master` a hit alone still deletes the row, false positives included; PR #9 (merged to `origin/master`) confirms a hit against the database first (see [Known open issues](#known-open-issues)).

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
- `handler.py`: `_service` (MovieService) and `_tv_service` (TvService) are module-level singletons — not re-instantiated per Kafka message.
- `db/tv_service.py`: has its own module-level `_producer` (ProducerUtil) — a second instance, deliberately not shared with `db/service.py`'s to avoid the import coupling.
- `RargbCrawler` is instantiated once per crawl invocation, outside the page loop (not per page), and **must be closed**: `crawl_rargb()` in `db/service.py` wraps the page loop in `try/finally: crawler.close()`. `RargbCrawler.close()` and a no-op default `BrowserDriver.close()` on the ABC were added after a prior OOM incident — see [Known open issues](#known-open-issues) and browser cleanup note below.
- `PlaywrightDriver` creates the browser and context once in `__init__` and reuses them across all `fetch()` calls — stealth scripts and browser args are module-level constants, not per-request setup. Unlike `SeleniumBrowerDriver`/`UndetectedChromeBrowserDriver` (which clean up via `__del__` → `driver.quit()`), `PlaywrightDriver` has no destructor — its Chromium subprocess only ever closes via an explicit `close()` call, so any new code path that creates a `RargbCrawler`/`PlaywrightDriver` must close it (`ImdbCrawler`'s browser is the one intentional exception — it's a lazy singleton meant to stay open for the app's lifetime).

### TV show feature (TVMaze)

- **Cache-backed calendar, not live-fetch-per-pageload**: an earlier version of `/tv` called the TVMaze API live on every page load (7 days × 2 endpoints), taking several seconds. Now `TvService.crawl_schedule()` populates a `tv_schedule` cache table (window: 7 days back, 14 days forward; pruned on every crawl) via the periodic background thread and `/crawl_tvmaze`, and `/tv` only ever reads from SQLite — page load is ~10-20ms. Any new calendar-like view should read from a cache table populated by a scheduled job, not call TVMaze inline in the request path.
- **Calendar week alignment**: `/tv` snaps the requested `start` date back to the preceding Sunday (`requested - timedelta(days=(requested.weekday()+1)%7)`) so the 7 columns always read Sun→Sat as a real week, not a rolling 7-day window starting on an arbitrary weekday.
- **Followed shows sort first, regardless of score**: `TvScheduleRepository.get_by_date()` only returns the top N (page_size) rows per day for performance, ordered by score — a followed show with a low/missing score would silently be excluded from that page entirely (not a highlighting bug, an exclusion bug). Fixed by a `LEFT JOIN tv_shows` that orders `follow_status = 'following'` first, then by score, so a followed show always lands on the visible page; "Load more" pagination (`GET /tv/day/<date>`) stays consistent since it's one ordering, not a separate merge.
- **`_baseline_next_episode()` on follow**: recording the show's current `next_episode_id` at follow time (without notifying) prevents `check_followed_episodes()` from firing a spurious notification on the very first poll for an episode the user already knew about when they followed.
- **Jinja renders Python `None` as the literal string `"None"`**: hit when `tv_shows`/`tv_schedule` rows created before the `language` column existed had `language = NULL` — templates must guard (`{% if s.language %}`) rather than interpolate directly.
- **Testing lesson**: verifying the follow-priority fix by calling `POST /tv/tvmaze/<id>/follow` directly against the live running container actually mutated real app state (a real show got followed and had to be manually deleted). State-changing verification (follow/abandon/etc.) must go through a local dev DB or an isolated smoke-test script — never the live container's endpoints.
- **Hover detail popover**: hovering anything with `data-tvmaze-id` for 1s opens a detail popover (`tv.html`). `GET /tv/show/<tvmaze_id>` → `TvService.get_show_detail()` serves from `tv_shows`, fetching from TVMaze and upserting on a miss (calendar/search entries usually aren't cached yet). Caveat: a cached row only refetches when `summary` is NULL, so score/status go stale — no TTL yet. Frontend prefetches detail + full-res poster at hover start (the 1s timer only gates visibility); the full-res poster URL is derived client-side by swapping `/medium_portrait/` → `/original_untouched/` (with `onerror` fallback), not stored.
- **XSS rule for innerHTML**: TVMaze data (show names, genres, networks, notification messages) is user-submitted upstream. Any JS that interpolates it into `innerHTML` must wrap strings in the `esc()` helper (defined in both `tv.html` and `index.html`) and numeric ids in `Number()` when placed inside `onclick` handlers. Jinja-rendered paths auto-escape; only the JS template literals need this.
- **`crawl_tvmaze` is trigger-only from the web layer**: `GET /crawl_tvmaze` and the 6h poll thread both call `TvService.trigger_crawl()`, which produces an empty message to the `crawl_tvmaze` topic (503 if Kafka is down). The actual crawl (minutes of rate-limited TVMaze calls) runs only in the Kafka consumer — never run `crawl_schedule()` inline in a request path.

### Fine-tuning gate

`app.py`'s `index()` computes `trainable_count` (movies marked and ready for T5 fine-tuning) and `finetunable` (True when ≥20). Both are passed to the template — `finetunable` gates the fine-tune button in the UI.

### Known open issues

- **rargb.to is behind a Cloudflare check the headless browser doesn't pass, and a blocked crawl looks like a success**: measured 2026-10-11 from the host through the proxy with the app's own `RargbCrawler` — every listing page tried (1 to 1500) came back as the "Just a moment..." check page (HTTP 403) with no result table, and waiting 42 s in the headless Chromium did not clear it; IMDb through the same browser and proxy still works. When the block began is unknown (newest stored movie: 2026-08-15), and it was not tested from inside the container. Not solved — no new movies arrive until it is (open decision: another mirror, another source, or a different proxy exit; first check whether a normal browser through the same proxy gets in). The crawler returned `[]` for the check page, so `crawl_rargb()` returned `True` and `/crawl_rargb` answered 200 "Crawling completed." with nothing stored. Fix was PR #12, squash-merged to `origin/master` as `863b3ec` on 2026-10-11 (review: through Flask's test client on copies of the live DB, the real blocked site, a fake check page on page 1 or 2, a crawler that raises and an unreachable Kafka all went 200 → 502, a normal crawl unchanged; the button's toast was not tested in a browser) — same caveat as PR #3: not live until local `master` takes it in and the app is restarted: `RargbCrawler.crawl()` raises `RargbBlockedError` on the check page, `crawl_rargb()` returns `False` when the crawl is blocked or raises, and the route answers 502 with a message the Crawl button shows (also when Kafka is unreachable). A no-table page that is not the check page still ends the crawl quietly, and a crawl started by a `crawl_rargb` Kafka message still shows a block only as an ERROR log line — the consumer ignores the result.
- **Layering note**: `app.py` imports `_bloom` from `db.service` directly for the `/deduplicate` route. PR #9 (merged to `origin/master` as `1f0f5a2`, not yet in local `master`) moves this logic into `MovieService.deduplicate()` and drops the import.
- **A Bloom-filter hit is treated as certain, so real movies get deleted**: `BloomUtils` builds the filter with the library defaults (10,000 elements, 10% error rate) and `predict()` deletes the row on any hit. Measured 2026-10-07 on a copy of `data/bf.bin` (47,926 bits, 4 probes, 31% set, ~4,450 titles): 1.04% of random strings report as seen, so about 1 in 100 new movies is deleted with only an INFO line, and never re-crawled because its date is inside the collected range; the rate reaches ~10% at 10,000 titles. `/deduplicate` is worse: the persisted filter already holds every predicted title, so each row matches itself — one click deleted the newest 1,000 titled rows on a copy of the DB. Fix was PR #9, squash-merged to `origin/master` as `1f0f5a2` on 2026-10-07 (review reproduced it on copies of the live DB and filter: 20 false-positive titles 0/20 → 20/20 kept, a real duplicate still deleted) — same caveat as PR #3: not live until local `master` takes it in and the app is restarted. On a hit `predict()` deletes only if `_title_exists()` finds another row with the same `normalize()`d title (0.7 ms → 14 ms on a hit, reads all titles), and `MovieService.deduplicate()` compares stored titles, keeping the earliest row of each (same copy: 190 removed, then 0). Until it is live, don't use the Deduplicate button; after that, note it compares by title only, so a remake or another year's film with the same title is removed too (open decision: also compare `year`). The rule either way: the filter may only say "maybe seen" — any delete must be confirmed against the database.
- **Backlog routes re-queue at most 1,000 rows per call**: `/produce/predict` and `/produce/imdb` go through `get_items()`, whose default is `limit=1000` — a larger backlog needs repeated calls. Not fixed (`get_items()` is touched by the uncommitted local edits).
- **`collected` table no explicit `id`**: DDL is `(start text, end text)`, relying on SQLite implicit `rowid`. `BaseRepository.find_one(id)` queries `WHERE id = ?` against this rowid.
- **Workflow WHERE duplication**: `get_items` and `count_items` in `service.py` have identical copy-pasted `if workflow == ...` blocks. Candidate for a `_workflow_where()` helper.
- **`bloom_utils.hasItem("")` returns `True`**: the latent risk is mitigated by the caller-side guard in `predict()` (`if not predicted_m.title: return`), but the guard lives only in `service.py` — other callers of `hasItem` must also guard before passing an empty string.
- **BS4 reference-cycle memory growth (fixed 2026-07-02)**: BS4 tags hold `.parent`/sibling back-references, so parsed trees are reference cycles reclaimable only by the cyclic GC, not refcounting. Under a fast back-to-back loop over hundreds of pages (e.g. draining a large `crawl_imdb` backlog), cycles piled up faster than GC scheduled a collection and process RSS ballooned — observed reaching ~5GB and pushing host memory to near-OOM (2026-07-02). Fix applied: both `RargbCrawler.crawl()` and `ImdbCrawler.crawl()` wrap soup usage in `try/finally: soup.decompose()`, so cycles are severed on every exit path (return, break, raise) and each page is reclaimed immediately by refcounting. `ImdbCrawler` also converts extracted `.string` values to plain `str` so returned `Movie` objects hold no `NavigableString` back-references into the tree. Any new BS4 call site must follow the same pattern.
- **`crawl_rargb` produce failures leave orphaned DB rows**: when `_producer.produce()` for the `predict` topic fails (e.g. transient Kafka outage), `crawl_rargb()` logs and moves on — the item is already inserted but no Kafka message is ever sent for it, so the consumer never picks it up (Kafka lag stays 0; the backlog is invisible to lag metrics). Existing routes `/produce/predict` and `/produce/imdb` (backed by `produce_predict_backlog()` / `produce_imdb_backlog()`) can be triggered manually to backfill. No automatic retry exists yet — a periodic background thread calling these on a timer (matching the daemon-consumer-thread pattern) was proposed but not built.
- **Train/predict race on the shared T5 model**: `POST /model/train` fine-tunes the shared `model` object on a Flask request thread while the `predict` Kafka consumer may concurrently call `generate()` on the same weights — reproduced 2026-09-29: the Trainer moves the model to `cuda:0` mid-prediction and `predict()` crashes with a cpu/cuda device-mismatch `RuntimeError`. Fix in PR #2 (`opt/myrargb-20260929-0317`; reviewed 2026-10-04 — lock and best-checkpoint fixes verified, but held unmerged because its `docker-compose.yml` hunk needs a redeploy to prove): a `threading.Lock` on `MyRargbModel` held for all of `train()` and from tokenize through decode in `predict()` (a predict during training just waits).
- **Early stopping saves the last, not best, checkpoint**: `model/model.py`'s `Trainer` uses `EarlyStoppingCallback` with `metric_for_best_model="eval_loss"` but without `load_best_model_at_end=True`, so `save_pretrained()` after an early stop writes the final (stopped-improving) epoch's weights. Fix (`load_best_model_at_end=True`) is in PR #2, held (see above).
- **Consumer thread dies on a failed commit**: `ConsumerUtil`'s loop called `consumer.commit(msg)` outside any `try`, so a `KafkaException` (broker restart, `REBALANCE_IN_PROGRESS`) ended the daemon thread silently — Flask keeps serving but that pipeline stage is dead until app restart. Fix (catch `KafkaException`, log, continue) was PR #3, squash-merged to `origin/master` as `0d3fd87` on 2026-10-04 — not live until local `master` (diverged from origin) takes it in and the app is restarted.
- **`predict` redelivery deletes the row**: a redelivered `predict` message (commit didn't land before crash/restart, or `/produce/predict` re-queuing in-flight rows) finds its own title already in the Bloom filter and deletes the movie as a duplicate. Fix (`predict()` re-reads the row and returns early if it is gone or already has a `title` — consistent with every predict caller selecting only `title IS NULL` rows) was PR #5, squash-merged to `origin/master` as `8508997` on 2026-10-04 — same caveat as PR #3: not live until local `master` takes it in and the app is restarted.
- **Trainer checkpoints left on disk**: `train()` leaves `data/results/checkpoint-*` (~695 MB each incl. optimizer state, 1.4 GB total) after `save_pretrained()` to `data/my_finetuned_t5`. Fix: `shutil.rmtree("./data/results", ignore_errors=True)` after saving — deferred until PR #2 (same file) merges.
- **`ProducerUtil.produce()` never calls `poll()`**: librdkafka frees a message only after its delivery report is served, so without `poll()` every produced message stays in the local queue; after 100k lifetime produces (`queue.buffering.max.messages`) every `produce()` raises `BufferError: Local: Queue full` — `crawl_rargb` then orphans each inserted row, and `db/tv_service.py` catches only `KafkaException`, not `BufferError`. Delivery failures are also silent. Reproduced 2026-09-29 with librdkafka's in-process mock broker (`test.mock.num.brokers: 1`), which allows testing producer behavior without the live stack. Fix (`poll(0)` per produce, `poll(1)` + retry on `BufferError`, `on_delivery` failure logging, bounded `flush(5)` in `__del__`) was PR #4, squash-merged to `origin/master` as `03a2b42` on 2026-10-04 (on top of PR #3, same file) — same caveat: not live until local `master` takes it in and the app is restarted.
- **Playwright's sync API is bound to its creating thread**: the `ImdbCrawler` singleton is shared by the `crawl_imdb` consumer thread and the `/crawl_imdb` route (a Flask request thread), so whichever uses it second fails every fetch with `cannot switch to a different thread`, and after three attempts the row is written `unmatched` for good (reproduced 2026-10-09: `The Matrix` scored 8.7 on the creating thread, `Inception` came back `unmatched` from a second). A Chromium that dies (crash, OOM kill) is likewise never replaced — every later fetch raises `TargetClosedError` until restart. Fix was PR #10, squash-merged to `origin/master` as `844c15e` on 2026-10-09 (review: second-thread fetches 0/3 → 3/3, 8 concurrent threads 0/8 → 8/8, fetches after killing Chromium 0/3 → 3/3; not tested in the container) — same caveat as PR #3: not live until local `master` takes it in and the app is restarted. `PlaywrightDriver` submits every Playwright call (`_launch`, `_fetch`, `_shutdown`) to its own one-worker `ThreadPoolExecutor`, so `fetch()`/`close()` work from any thread and callers are served in turn, and `_fetch` relaunches the browser once (WARNING log) when `new_page()` fails — a browser that dies mid-fetch still fails that one fetch, and rows already marked `unmatched` by the bug need the `UPDATE` + `/produce/imdb` re-queue. Rule either way: never touch `_pw`/`_browser`/`_context` from a caller's thread.
- **IMDb WAF check fires on every page**: `PlaywrightDriver.fetch()` treats any page containing "awswaf" as a WAF challenge, but every real IMDb page contains it (`window.awsWafCookieDomainList`), so each fetch runs all 5 "wait 3 s + network idle" rounds — measured 19–24 s per search page vs 2.9 s for a bare `goto`; a no-result search is retried 3 times, so ~1 min. Fix (match only `challenge-container` / `AwsWafIntegration`; 2.5–3.6 s per fetch, ~11 s for the first fetch of a session; rargb pages never matched and are unaffected) was PR #7, squash-merged to `origin/master` as `7d91833` on 2026-10-04 — same caveat as PR #3: not live until local `master` takes it in and the app is restarted.
- **IMDb search uses the raw predicted title**: `ImdbCrawler` puts the T5 title into the search URL as is and un-encoded, but many predicted titles still carry the year, release tags or dot separators (`Blow 2001 Upscaled BluRay`, `Hoppers.2026.1080p`) — as of 2026-10-04, 1,537 of 4,057 IMDb-processed movies (38%) were `score = 'unmatched'`, 64% of them with such titles. Fix in PR #7 (merged as `7d91833`, see above): `clean_search_title()` in `crawler.py` cuts at the year / first release tag, turns dots into spaces and URL-encodes; a hand-corrected `title_accurate` is searched as typed (40 sampled `unmatched` rows: 21 → 32 matched; the review's own sample: 14 → 32, no row lost). Existing `unmatched` rows are not re-queued by the fix: `UPDATE movies SET score = NULL WHERE score = 'unmatched'`, then `/produce/imdb`. Still unmatched after it: short titles (`Up`, `Blue`, `Cell`) whose right year isn't in the first results — adding the year to the query is untried.
- **IMDb title is never extracted**: `ImdbCrawler`'s `h3.ipc-title__text` lookup finds nothing on current IMDb search pages, so a matched row gets its score but no IMDb title (seen on all 32 matches in the PR #7 review, 2026-10-04, before and after that PR; whether the poster comes back was not checked). Not fixed: PR #8 reports the selector should be `h4`, not `h3` (not verified by its review), and left it alone because fixing it makes every match overwrite the model's title with IMDb's — an open decision.
- **Transient IMDb failure becomes a permanent `unmatched`**: after three failed attempts `ImdbCrawler.crawl()` sets `score = "unmatched"` (deliberate per the code comments), and `/produce/imdb` only selects rows with no score, so the row is never retried — yet 21 of 40 sampled `unmatched` rows matched on a rerun with unchanged code. Open decision: leave the score empty on failure so the next `/produce/imdb` retries it.
- **Calendar history contradicts retention**: the `/tv` UI lets you page back indefinitely, but `crawl_schedule()` prunes `tv_schedule` entries older than 7 days — two weeks back is silently empty. Either widen retention (rows are tiny) or cap the "Prev week" navigation.
- **Find-then-write upsert race in TV crawl**: `crawl_schedule()`/show upserts do SELECT-then-INSERT per row; an overlapping 6h-poll and manual trigger can both miss the SELECT and collide on the unique index. `INSERT ... ON CONFLICT DO UPDATE` would fix the race and halve the query count. Not yet applied.

## Key files

| File | Role |
|---|---|
| `app.py` | Flask entry point, ~20 routes (movies + TV), starts 4 Kafka consumer daemon threads (crawl_rargb/predict/crawl_imdb/crawl_tvmaze) + 1 periodic TVMaze-poll thread |
| `handler.py` | Kafka message deserialization → `MovieService`/`TvService` delegation |
| `db/service.py` | `MovieService`: crawl/predict/imdb orchestration, collected-range logic |
| `db/tv_service.py` | `TvService`: TVMaze calendar cache, search, follow/abandon, next-episode notification polling |
| `db/repository.py` | `BaseRepository[T]` with generic SQLite CRUD, plus `TvShowRepository`/`TvNotificationRepository`/`TvScheduleRepository` |
| `db/db.py` | Singleton `MyRargbDB`, table creation, column migrations |
| `db_model/__init__.py` | Pydantic models with `@table(name)` decorator, incl. `TvShow`/`TvNotification`/`TvScheduleEntry` |
| `crawler.py` | `RargbCrawler` (Playwright → BeautifulSoup), `ImdbCrawler` (IMDb mobile) |
| `tv_crawler.py` | `TvMazeClient` — plain `requests.Session` wrapper around TVMaze's `/schedule`, `/shows`, `/search/shows` endpoints |
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
- **tv_shows** — `id, tvmaze_id (unique), name, premiered, ended, status, score, genres, poster, summary, network, language, follow_status, next_episode_id, updated_at`. `follow_status` is `null`/`following`/`abandoned`; `next_episode_id` is the TVMaze episode id last seen, used to detect a new episode for notifications.
- **tv_notifications** — `id, tv_show_id, message, episode_code, airdate, read_flag, created_at`
- **tv_schedule** — `id, date, tvmaze_id, name, poster, score, genres, network, language, episode_code, airdate, updated_at` (unique on `date, tvmaze_id`). Cache table for the calendar view, populated by `TvService.crawl_schedule()`; window is 7 days back / 14 forward, pruned on every crawl.

Model fine-tuning checkpoint at `./data/my_finetuned_t5/`.

## Infrastructure

Docker Compose: app (Playwright image), Kafka (KRaft mode), Kafka UI (`:9090`). Kafka topics: `xyz.lidaning.myrargb.topics.crawl_rargb`, `.predict`, `.crawl_imdb`, `.crawl_tvmaze`. Consumer groups: `xyz.lidaning.myrargb.consumers.*`. Since TVMaze has no push mechanism, a background daemon thread in `app.py` produces an empty `crawl_tvmaze` message every `TVMAZE_POLL_INTERVAL_SECONDS` (6h) in addition to the manual `/crawl_tvmaze` trigger.

`ProducerUtil._available_cache` is reset to `None` on `KafkaException` in `produce()`, but that does not recover a broker that was down at the first check: every caller (`crawl_rargb()`, the `/produce/*` backlog calls) asks `available()` first and returns on `False`, so `produce()` is never reached and the app refuses crawls as "Kafka is unreachable" until restart (reproduced 2026-10-13 with librdkafka's mock broker: down, then up → still `False`). The reverse also holds — a broker that goes down later stays "reachable". It can happen at start-up because `depends_on: kafka` waits for the container to start, not for the broker to listen; the stopped container's log shows no sign it ever did. Fix was PR #13, squash-merged to `origin/master` as `f0e8468` on 2026-10-13 (review with the real `ProducerUtil` against the mock broker behind a local TCP forwarder: down then up went `False` for good → `True` after 31 s, a broker gone again → `False` after 31 s; not tested in the container or with the real broker) — same caveat as PR #3: not live until local `master` takes it in and the app is restarted: the answer expires after `ProducerUtil.AVAILABLE_TTL_SECONDS = 30` (cost: one metadata request per crawl/backlog call once the answer is older than that, and a 3 s timeout on each such call while Kafka is down; for up to 30 s after a broker dies `available()` still says `True`). Still open: a healthcheck on `kafka` with `depends_on: condition: service_healthy` (not done — `docker-compose.yml` is shared with the held PRs #2 and #6), and a broker that dies mid-crawl is only seen as delivery ERROR lines.

**Restart policy / image size**: as of 2026-09-29 no service had a `restart:` policy, so the stack stayed down after a host reboot; PR #2 adds `restart: unless-stopped` to `myrargb` and `kafka` (needs `docker compose up -d` after merge). The deployed image is ~19 GB: ~8 GB is the `uv sync` layer (CUDA torch + nvidia-* wheels, largely wasted since inference is CPU-only — switching to CPU torch wheels is an open decision), and ~2 GB is `data/` baked in by an image built before the `.dockerignore` fix (a rebuild removes it).

**Kafka broker memory / persistence**: `docker-compose.yml` sets neither `KAFKA_HEAP_OPTS` nor `KAFKA_LOG_DIRS`, so the `apache/kafka` image defaults apply: `-Xms1G -Xmx1G` (broker RSS grows to ~1 GiB over hours, though only ~185 MB of heap is live — mostly the 128 MB log-cleaner buffer) and a log dir of `/tmp/kafka-logs` in the container's writable layer, so every container recreate (`docker compose down`, or `up` after any compose change) drops all topics, queued messages and committed offsets. Fix in PR #6 (`opt/myrargb-20261002-0341`; reviewed 2026-10-04 — merges cleanly and the config validates, but held unmerged because a compose-only change needs a redeploy to prove; it shares `docker-compose.yml` with the held PR #2, so one `docker compose up -d` can cover both): `KAFKA_HEAP_OPTS: -Xms128m -Xmx384m` (don't go below ~384m — the cleaner buffer alone is 128 MB) and `KAFKA_LOG_DIRS: /var/lib/kafka/data` on a named `kafka-data` volume. Applying it recreates the broker one last time, so run `/produce/predict` and `/produce/imdb` afterwards to re-queue lost rows. `data/kafka/` is a stale leftover from an old bind mount, unused by the current stack. Still open: `kafka-ui` is always on (~305 MiB) and could sit behind a compose profile; no service has a json-file log size limit.

**GPU**: The app container uses `runtime: nvidia` (`NVIDIA_VISIBLE_DEVICES=all`) — T5 inference requires an NVIDIA GPU; the container will fail to start without one. In practice (observed 2026-07-02 via `nvidia-smi` during a predict backlog drain) the T5 model runs CPU-only — GPU showed 0 processes and near-idle usage despite the runtime being configured — so predict throughput and host CPU/RAM load should be attributed to CPU inference, not assumed to be offloaded to CUDA.

**Memory / allocator**: `MALLOC_ARENA_MAX=2` is set in `docker-compose.yml` for the app container. Without it, glibc's per-thread malloc arenas (7 consumer threads + Flask) retain freed heap instead of returning it to the OS — after the 2026-07-02 backlog drain, RSS plateaued at ~4 GiB even though idle sampling showed no active leak (the `soup.decompose()` fix had stopped real growth). Judge memory health by whether RSS is flat at idle, not by whether it drops back to the ~1 GiB startup baseline.

**Proxy**: `HTTP_PROXY` / `HTTPS_PROXY` are set to `http://172.17.0.1:10808` (host machine via Docker bridge) so the container can reach rargb.to and IMDb. Without a working proxy at that address, crawling will fail silently.

**SQLite pragmas**: every connection sets `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`. NORMAL (not FULL) matters because repositories commit per row — under FULL, a schedule crawl paid one fsync per episode. Safe under WAL: an OS crash can lose the last transactions but never corrupts.

**Logging**: `LOGGER_LEVEL` env (compose sets `ERROR`) controls the root level; `DEBUG=true` overrides to DEBUG. `app.log` uses a `RotatingFileHandler` (10MB × 3 backups). Per-row SQL logging in `db/repository.py` is at DEBUG — keep it there; at INFO a single crawl once grew `app.log` to 267MB.
