import sqlite3
import threading
import logging

logger = logging.getLogger(__name__)

_DB_PATH = "./data/myrargb.db"


class MyRargbDB:
    """SQLite wrapper with per-thread connections.

    DDL (table creation + migrations) runs once on the main thread in __init__.
    All data operations use a thread-local connection+cursor so concurrent
    Flask requests and Kafka consumer threads never share cursor state.
    """

    _local = threading.local()

    def __init__(self):
        init_conn = sqlite3.connect(_DB_PATH)
        init_cur = init_conn.cursor()
        init_cur.execute("PRAGMA journal_mode=WAL")
        init_cur.execute("PRAGMA synchronous=NORMAL")
        init_cur.execute("PRAGMA busy_timeout=5000")
        self._create_tables(init_cur)
        self._migrate(init_cur)
        init_conn.commit()
        init_conn.close()

    # ------------------------------------------------------------------
    # Thread-local connection/cursor
    # ------------------------------------------------------------------

    def _open_thread_conn(self):
        conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL skips the per-commit fsync (WAL only fsyncs on checkpoint);
        # crawl_schedule commits per row, so FULL costs hundreds of fsyncs per run.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @property
    def conn(self):
        if not hasattr(self._local, "conn"):
            self._local.conn = self._open_thread_conn()
            self._local.cur = self._local.conn.cursor()
        return self._local.conn

    @property
    def cur(self):
        if not hasattr(self._local, "conn"):
            self._local.conn = self._open_thread_conn()
            self._local.cur = self._local.conn.cursor()
        return self._local.cur

    # ------------------------------------------------------------------
    # DDL helpers (run once during __init__ on the main thread)
    # ------------------------------------------------------------------

    def _create_tables(self, cur):
        cur.execute("""
            CREATE TABLE IF NOT EXISTS movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                size TEXT,
                title TEXT,
                url TEXT,
                score TEXT,
                genre TEXT,
                poster TEXT,
                marked TEXT default '00',
                title_accurate TEXT,
                trained_flag TEXT default '0',
                added text
            )
        """)
        cur.execute(
            " CREATE TABLE IF NOT EXISTS collected (start text, end text) "
        )
        cur.execute(
            """ CREATE TABLE IF NOT EXISTS config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE,
                value TEXT
            ) """
        )
        cur.execute(
            """ CREATE TABLE IF NOT EXISTS tv_shows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tvmaze_id INTEGER,
                name TEXT,
                premiered TEXT,
                ended TEXT,
                status TEXT,
                score TEXT,
                genres TEXT,
                poster TEXT,
                summary TEXT,
                network TEXT,
                language TEXT,
                follow_status TEXT,
                next_episode_id INTEGER,
                updated_at TEXT
            ) """
        )
        cur.execute(
            """ CREATE TABLE IF NOT EXISTS tv_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tv_show_id INTEGER,
                message TEXT,
                episode_code TEXT,
                airdate TEXT,
                read_flag TEXT DEFAULT '0',
                created_at TEXT
            ) """
        )
        cur.execute(
            """ CREATE TABLE IF NOT EXISTS tv_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                tvmaze_id INTEGER,
                name TEXT,
                poster TEXT,
                score TEXT,
                genres TEXT,
                network TEXT,
                language TEXT,
                episode_code TEXT,
                airdate TEXT,
                updated_at TEXT
            ) """
        )
        try:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_movies_url ON movies(url)"
            )
        except sqlite3.IntegrityError:
            logger.warning(
                "Could not create unique index on movies(url) — "
                "duplicate URLs exist. Run deduplication to clean them."
            )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tv_shows_tvmaze_id ON tv_shows(tvmaze_id)"
        )
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tv_schedule_date_show ON tv_schedule(date, tvmaze_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_tv_schedule_date ON tv_schedule(date)"
        )

    def _migrate(self, cur):
        table_migrations = {
            "movies": {
                "score": "ALTER TABLE movies ADD COLUMN score TEXT",
                "genre": "ALTER TABLE movies ADD COLUMN genre TEXT",
                "poster": "ALTER TABLE movies ADD COLUMN poster TEXT",
                "title_accurate": "ALTER TABLE movies ADD COLUMN title_accurate TEXT",
                "trained_flag": "ALTER TABLE movies ADD COLUMN trained_flag TEXT DEFAULT '0'",
                "marked": "ALTER TABLE movies ADD COLUMN marked TEXT DEFAULT '00'",
                "year": "ALTER TABLE movies ADD COLUMN year TEXT",
            },
            "tv_shows": {
                "language": "ALTER TABLE tv_shows ADD COLUMN language TEXT",
            },
            "tv_schedule": {
                "language": "ALTER TABLE tv_schedule ADD COLUMN language TEXT",
            },
        }
        for table, migrations in table_migrations.items():
            existing = {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}
            for col, sql in migrations.items():
                if col not in existing:
                    cur.execute(sql)
        cur.connection.commit()


db = MyRargbDB()

if __name__ == "__main__":
    pass
