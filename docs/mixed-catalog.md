# Movies and TV releases in one catalog

The catalog table is named `items`; startup migrates the former `movies` table in place. The `Movie` Python class remains a compatibility name
for the torrent catalog. The TVMaze calendar still uses its separate `tv_shows`
and `tv_schedule` tables; scraped TV torrents do not create followed calendar
shows automatically.

The catalog now stores:

| Column | Meaning |
| --- | --- |
| `media_type` | `movie`, `tv`, or `unknown` |
| `release_scope` | `movie`, `episode`, `season`, `series`, `mixed`, or `unknown` |
| `season`, `season_end` | Start/end season numbers, including season zero specials |
| `episode`, `episode_end` | Start/end episode numbers for an episode release |

New crawl entries use the source category and filename. Existing rows are
classified from their filenames: season/episode notation identifies TV releases,
while a release year without TV/event markers identifies likely movies.
Uncertain rows remain unclassified. Mixed-show filenames and ambiguous ranges
use `mixed`, avoiding accidental episode-level deduplication.

A shared parser in `src/utils/media.py` drives crawling, backfill, display,
deduplication and enrichment. Its rules are format invariants in code. They do
not require a replacement model or retraining. The title model still extracts
movie/show names; episode information is stored separately. Reviewed
`title_accurate` targets and `trained_flag` values remain unchanged.

## Matching and deduplication

Movie identity includes title and release year, so remakes stay separate.
TV identity includes the show name, a series-start year if explicitly present
before the season code, release scope and season/episode ranges. Different
episodes and season packs therefore survive prediction and the Deduplicate
button. Unclassified or mixed releases fall back to filename identity. Existing
reviewed title/training rows are preserved even if another release has the same
identity. The Bloom filter remains a hint; deletion is confirmed against the
full identity in SQLite. URL uniqueness remains unchanged.

IMDb matching skips film results for TV releases and skips TV results for known
movies. TV searches remove season/episode tags. A year after an episode code is
not used as the show's premiere year; a year explicitly before that code can
still disambiguate same-name series. TV result year ranges such as `2009–2015`
are interpreted by their start year.

## Browsing and migration

The catalog table includes **Type / Release** labels such as `TV · S02E03`,
`S01E07–E09`, and season packs. The media filter, search and sort are preserved
through pagination. The existing TV button continues to open the calendar.

To migrate an existing database explicitly:

```sh
.venv/bin/python scripts/migrate_catalog.py
```

This first creates and checks a SQLite backup under `data/backups/`, then adds
six columns, a media-type index, and backfills release metadata in a transaction.
The migration is idempotent. Use `--refresh` to reparse existing release metadata
after a parser update; known source-category classifications are retained when
the filename alone is inconclusive. Application startup also adds/backfills the fields
when it encounters an older schema. New metadata is added without rewriting
titles, ratings, posters, watch status or training targets. No rows are deleted
by the migration, and no training or recrawl is started. Restart the app to load
the new parsing, matching and deduplication code.

Records deleted by older title-only deduplication cannot be recovered merely by
adding these columns. Their recovery would require a separate, reviewed import
from saved crawl messages, logs or backups. This migration does not restore them
or re-score existing IMDb matches.


Manual correction: **Mismatch** searches IMDb with a lowercase, whitespace-free
query and `s=tt&exact=true&ref_=fn_ttl_ex`. It saves only a unique result with
the same title, media type and exact year when known. An unavailable provider,
ambiguous result or no match leaves the existing metadata intact.
**Delete duplicated** immediately removes the selected row based on your manual
decision, without automatic duplicate verification or a confirmation prompt.

Title verification uses one shared key for Bloom filter entries, database
duplicate checks, and exact IMDb comparisons: lowercase, remove whitespace,
and remove literal periods. `The.Matrix`, `THE MATRIX`, and `thematrix` all
produce `thematrix`. Other punctuation and Unicode letters are preserved.
Year, media type, and TV release boundaries still distinguish separate releases.
After changing this key, rebuild the Bloom filter using
`.venv/bin/python scripts/rebuild_bloom.py` while the app is stopped; the command
backs up the previous index and leaves catalog titles unchanged.

Training targets replace each period in the corrected title with a space
(`The.Matrix` → `The Matrix`) before dataset splitting and tokenization.
Source filenames and stored catalog titles retain their original values.
