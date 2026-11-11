import logging
import json
import re
import sqlite3
import threading
from db.repository import MovieRepository, CollectedRepository
from db_model import Movie, Collected
from utils.kafka_utils import ProducerUtil
from workflow import Workflow
from typing import List
from crawler import ImdbCrawler, RargbCrawler
from browserdriver.errors import BrowserFetchError
from model.model import model
from utils.bloom_utils import BloomUtils

# Module-level singletons — shared state across requests without repeated init cost.
_bloom = BloomUtils()
_producer = ProducerUtil()
_imdb_crawler: "ImdbCrawler | None" = None
# Guards _imdb_crawler's single Playwright page/browser — the crawl_imdb Kafka
# consumer thread and a Flask request thread (lazy detail-popover fetch) can
# both reach for it; concurrent navigation on one page would interleave.
_imdb_crawler_lock = threading.Lock()


def _get_imdb_crawler() -> "ImdbCrawler":
    global _imdb_crawler
    if _imdb_crawler is None:
        _imdb_crawler = ImdbCrawler()
    return _imdb_crawler
from utils.pager_utils import validate_order_by, PER_PAGE

logger = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"\b(19\d{2}|20[0-2]\d)\b")


def extract_year(filename: str) -> str | None:
    """Extract a movie release year from a filename like 'The.Matrix.1999.1080p...'"""
    if not filename:
        return None
    m = _YEAR_RE.search(filename)
    return m.group(1) if m else None


class MovieService:
    def __init__(self):
        self.collectedRepository = CollectedRepository()
        self.movieRepository = MovieRepository()

    def _get_collected_range(self) -> tuple[str | None, str | None]:
        """Return (start, end) — oldest and newest 'added' dates seen so far."""
        rows = self.collectedRepository.getMany()
        if rows:
            return rows[0].start, rows[0].end
        return None, None

    def _update_collected_range(self, start: str, end: str):
        existing = self.collectedRepository.getMany()
        if existing:
            c = existing[0]
            c.start = start
            c.end = end
            self.collectedRepository.update(c)
        else:
            self.collectedRepository.insert(Collected(start=start, end=end))

    def get_items(
        self,
        workflow: Workflow,
        sql="",
        limit=1000,
        offset=0,
        order_by="id DESC",
        search: str | None = None,
    ) -> List[Movie]:
        order_by = validate_order_by(order_by)

        exe_sql = " select id, filename, size, title, url, score, genre, poster, marked, title_accurate, trained_flag, added, year, seed, leech, summary from movies where 1=1 "

        if workflow == Workflow.PREDICT:  # for prediction
            exe_sql += " and (title is null or title = '' ) and (trained_flag != '1') "
        elif workflow == Workflow.TRAINING:  # for finetuning
            exe_sql += " and title_accurate is not null and trained_flag = '0' "
        elif workflow == Workflow.QUERYING:  # for browsing
            exe_sql += (
                " and score is not null and score != '' and score != 'unmatched' "
            )
        elif workflow == Workflow.SCORING:  # for searching in imdb
            exe_sql += " and ( score is null or score = '' ) and ( title is not null or title_accurate is not null ) "
        elif workflow == Workflow.DEDUPLICATION:  # for searching in imdb
            exe_sql += " and title is not null and title != '' "
        elif workflow == Workflow.NONE:
            pass

        if sql:
            exe_sql += " " + sql + " "

        params: list = []
        if search:
            exe_sql += " and (title LIKE ? OR title_accurate LIKE ? OR filename LIKE ?) "
            like = f"%{search}%"
            params.extend([like, like, like])

        exe_sql += f" ORDER BY {order_by} "
        exe_sql += " LIMIT ? OFFSET ? "
        params.extend([limit, offset])
        movies = self.movieRepository.execute_sql(exe_sql, tuple(params))
        return movies

    def count_items(self, workflow: Workflow, sql="", search: str | None = None) -> int:
        where = " 1=1 "

        if workflow == Workflow.PREDICT:
            where += " and (title is null or title = '' ) and (trained_flag != '1') "
        elif workflow == Workflow.TRAINING:
            where += " and title_accurate is not null and trained_flag = '0' "
        elif workflow == Workflow.QUERYING:
            where += " and score is not null and score != '' and score != 'unmatched' "
        elif workflow == Workflow.SCORING:
            where += " and ( score is null or score = '' ) and ( title is not null or title_accurate is not null ) "
        elif workflow == Workflow.DEDUPLICATION:
            where += " and title is not null and title != '' "
        elif workflow == Workflow.NONE:
            pass

        if sql:
            where += " " + sql + " "

        params: list = []
        if search:
            where += " and (title LIKE ? OR title_accurate LIKE ? OR filename LIKE ?) "
            like = f"%{search}%"
            params.extend([like, like, like])

        return self.movieRepository.count(where, tuple(params))

    def crawl_rargb(self, incremental=True) -> bool:
        """Crawl rargb.to/movies/ for movie torrents.

        In incremental mode, uses the ``collected`` table's date range to skip
        already-crawled items. The range expands as new items are found. When
        every item on a page falls inside the collected range, the crawl stops
        — we've caught up with previously crawled territory.

        Non-incremental mode crawls the single requested page with no range
        checks (manual UI use).
        """
        try:
            if not _producer.available():
                logger.error("[x] Kafka is unreachable — aborting crawl.")
                return False

            range_start, range_end = (
                self._get_collected_range() if incremental else (None, None)
            )
            current_page = 1
            page_start = range_start
            page_end = range_end

            crawler = RargbCrawler()
            try:
                while True:
                    items = crawler.crawl({"page": current_page})
                    if not items:
                        logger.info(f"[v] No items on page {current_page}, stopping.")
                        break

                    new_on_page = 0
                    for item in items:
                        if self.movieRepository.exists_by_url(item.url):
                            logger.debug(f"[v] Skipping known URL: {item.url}")
                            continue

                        # Skip items inside the already-collected date range
                        if (
                            range_start
                            and range_end
                            and item.added
                            and item.added > range_start
                            and item.added < range_end
                        ):
                            logger.debug(
                                f"[v] Item {item.added} inside collected range "
                                f"({range_start} ~ {range_end}), skipping."
                            )
                            continue

                        year = extract_year(item.filename)
                        if year:
                            item.year = year

                        try:
                            item.id = self.movieRepository.insert(item)
                        except sqlite3.IntegrityError:
                            logger.debug(f"[v] Duplicate URL skipped: {item.url}")
                            continue

                        try:
                            _producer.produce(
                                "xyz.lidaning.myrargb.topics.predict",
                                {"movie": item.model_dump()},
                            )
                        except Exception as e:
                            # Item is already inserted — leave it for produce_predict_backlog()
                            # to pick up later rather than aborting the whole crawl over one
                            # failed produce call.
                            logger.error(f"[x] Failed to produce predict task for item {item.id}: {e}")
                        new_on_page += 1

                        if item.added:
                            if not page_start or item.added < page_start:
                                page_start = item.added
                            if not page_end or item.added > page_end:
                                page_end = item.added

                    logger.info(
                        f"[v] Page {current_page}: {new_on_page} new items."
                    )

                    # Persist range after each page so a crash doesn't lose progress
                    if incremental and page_start and page_end:
                        self._update_collected_range(page_start, page_end)

                    if new_on_page == 0:
                        break

                    current_page += 1
            finally:
                crawler.close()

        except BrowserFetchError:
            raise
        except Exception as e:
            logger.error('[x] Crawl failed (%s).', type(e).__name__)
            raise BrowserFetchError('Crawl failed; check the application log.') from None

        return True

    def produce_predict_backlog(self) -> int:
        """Produce Kafka messages for all items still needing title prediction."""
        if not _producer.available():
            logger.error("[x] Kafka is unreachable — cannot produce backlog.")
            return -1
        items = self.get_items(Workflow.PREDICT)
        for item in items:
            _producer.produce(
                "xyz.lidaning.myrargb.topics.predict",
                {"movie": item.model_dump()},
            )
        logger.info(f"[v] Produced predict tasks for {len(items)} items.")
        return len(items)

    def produce_imdb_backlog(self) -> int:
        """Produce Kafka messages for all items still needing IMDb scoring."""
        if not _producer.available():
            logger.error("[x] Kafka is unreachable — cannot produce backlog.")
            return -1
        items = self.get_items(Workflow.SCORING)
        for item in items:
            _producer.produce(
                "xyz.lidaning.myrargb.topics.crawl_imdb",
                {"movie": item.model_dump()},
            )
        logger.info(f"[v] Produced IMDb tasks for {len(items)} items.")
        return len(items)

    def produce_summary_backlog(self) -> int:
        """Produce Kafka messages for movies matched to IMDb before the plot-summary
        scrape existed — re-running crawl_imdb re-fetches the same match and this
        time also captures the synopsis. Excludes 'unmatched' rows since re-searching
        those is Workflow.SCORING's job, not this backfill's.
        """
        if not _producer.available():
            logger.error("[x] Kafka is unreachable — cannot produce backlog.")
            return -1
        items = self.get_items(
            Workflow.NONE,
            sql="and score is not null and score != '' and score != 'unmatched' "
                "and (summary is null or summary = '')",
        )
        for item in items:
            _producer.produce(
                "xyz.lidaning.myrargb.topics.crawl_imdb",
                {"movie": item.model_dump()},
            )
        logger.info(f"[v] Produced summary backfill tasks for {len(items)} items.")
        return len(items)

    def crawl_imdb(self, m: Movie):
        with _imdb_crawler_lock:
            updated_m = _get_imdb_crawler().crawl(m)
        if not updated_m:
            return
        self.movieRepository.update(updated_m)

    def get_movie_detail(self, movie_id: int) -> Movie | None:
        """Full movie row for the hover popover. Served straight from the DB
        when summary is already cached; on a miss (matched before the
        summary scrape existed) re-run the IMDb crawl once and persist —
        the same lazy-fill-on-miss pattern as TvService.get_show_detail().
        """
        movie = self.movieRepository.find_one(movie_id)
        if movie and not movie.summary and movie.score and movie.score != "unmatched":
            with _imdb_crawler_lock:
                updated_m = _get_imdb_crawler().crawl(movie)
            if updated_m:
                self.movieRepository.update(updated_m)
                movie = self.movieRepository.find_one(movie_id)
        return movie

    def predict(self, movie: Movie):
        predicted_m = model.predict(movie)
        if not predicted_m or not predicted_m.title:
            return

        if _bloom.hasItem(predicted_m.title):
            logger.info(
                f"[x] Duplicate title '{predicted_m.title}', removing row."
            )
            self.movieRepository.delete(predicted_m.id)
            return

        self.movieRepository.update(predicted_m)
        _bloom.add(predicted_m.title)
        _producer.produce(
            "xyz.lidaning.myrargb.topics.crawl_imdb",
            {"movie": predicted_m.model_dump()},
        )
