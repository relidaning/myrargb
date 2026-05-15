import sqlite3
import logging

logger = logging.getLogger(__name__)


class MyRargbDB:
    def __init__(self):
        self.conn = sqlite3.connect("./data/myrargb.db", check_same_thread=False)
        self.cur = self.conn.cursor()
        self.cur.execute("PRAGMA journal_mode=WAL")
        self.cur.execute("PRAGMA busy_timeout=5000")
        self.cur.execute("""
            CREATE TABLE IF NOT EXISTS movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                size TEXT,
                title TEXT,        
                url TEXT,
                type TEXT,
                score TEXT,
                genre TEXT,
                poster TEXT,    
                marked TEXT default '00',
                title_accurate TEXT,
                trained_flag TEXT default '0',
                added text
            )
        """)
        # TYPE: 00: MOVIES, 01: TV SHOWS, etc.
        self.cur.execute(
            " create table if not exists collected (start text, end text) "
        )
        self.cur.execute(
            """ create table if not exists config (
                id integer primary key autoincrement,
                key text unique,
                value text
            ) """
        )
        try:
            self.cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_movies_url ON movies(url)"
            )
        except sqlite3.IntegrityError:
            logger.warning(
                "Could not create unique index on movies(url) — "
                "duplicate URLs exist. Run deduplication to clean them."
            )
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        existing = {row[1] for row in self.cur.execute("PRAGMA table_info(movies)")}
        migrations = {
            "score": "ALTER TABLE movies ADD COLUMN score TEXT",
            "genre": "ALTER TABLE movies ADD COLUMN genre TEXT",
            "poster": "ALTER TABLE movies ADD COLUMN poster TEXT",
            "title_accurate": "ALTER TABLE movies ADD COLUMN title_accurate TEXT",
            "trained_flag": "ALTER TABLE movies ADD COLUMN trained_flag TEXT DEFAULT '0'",
            "marked": "ALTER TABLE movies ADD COLUMN marked TEXT DEFAULT '00'",
            "year": "ALTER TABLE movies ADD COLUMN year TEXT",
        }
        for col, sql in migrations.items():
            if col not in existing:
                self.cur.execute(sql)
        self.conn.commit()

    def __del__(self):
        self.conn.close()


db = MyRargbDB()

if __name__ == "__main__":
    pass
