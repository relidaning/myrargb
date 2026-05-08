import sqlite3
from workflow import Workflow
import logging


logger = logging.getLogger(__name__)


class MyRargbDB:
    def __init__(self):
        self.conn = sqlite3.connect("myrargb.db", check_same_thread=False)
        self.cur = self.conn.cursor()
        self.cur.execute("""
            CREATE TABLE IF NOT EXISTS movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT,
                size TEXT,
                title TEXT,        
                url TEXT,
                type TEXT,
                socre TEXT,
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
        self._migrate()
        self.conn.commit()

    def save_items(self, items):
        start, end = None, None
        for item in items:
            self.cur.execute(" select start, end from collected ")
            rows = self.cur.fetchall()
            if not rows:
                start, end = item["added"], item["added"]
                self.cur.execute(
                    " insert into collected (start, end) values (?, ?) ",
                    (start, end),
                )
            else:
                start, end = rows[0]
                if item["added"] > start and item["added"] < end:
                    continue

            self.cur.execute(
                """
                    INSERT INTO movies (filename, size, url, type, added, trained_flag) VALUES (?, ?, ?, ?, ?, '0')
                """,
                (
                    item["filename"],
                    item["size"],
                    item["url"],
                    item["type"],
                    item["added"],
                ),
            )
            if item["added"] < start:
                start = item["added"]
            else:
                end = item["added"]
            self.cur.execute(" update collected set start = ?, end = ? ", (start, end))
            self.conn.commit()

    def get_items(
        self, workflow: Workflow, type="movies", sql="", limit=1000, order_by="id DESC"
    ):
        exe_sql = " select id, filename, size, title, url, type, score, genre, poster, marked, title_accurate, trained_flag from movies where 1=1 "

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

        if type == "movies":
            exe_sql += " and type = '00' "

        if sql:
            exe_sql += " " + sql + " "

        exe_sql += f" ORDER BY {order_by} "
        exe_sql += f" LIMIT {limit} "
        logger.debug(f"Executing SQL: {exe_sql}")
        self.cur.execute(exe_sql)
        rows = self.cur.fetchall()

        items = []
        for row in rows:
            items.append(
                {
                    "id": row[0],
                    "filename": row[1],
                    "size": row[2],
                    "title": row[3],
                    "url": row[4],
                    "type": row[5],
                    "score": row[6],
                    "genre": row[7],
                    "poster": row[8],
                    "marked": row[9],
                    "title_accurate": row[10],
                    "trained_flag": row[11],
                }
            )

        return items

    def update_item(self, item):
        # id must exist
        if "id" not in item:
            raise ValueError("item must contain 'id'")

        allowed_fields = [
            "title",
            "score",
            "poster",
            "marked",
            "genre",
            "filename",
            "size",
            "url",
            "type",
            "title_accurate",
            "trained_flag",
        ]
        fields = []
        values = []

        for key in allowed_fields:
            if key in item:
                fields.append(f"{key} = ?")
                values.append(item[key])

        # Nothing to update
        if not fields:
            return False

        # Add id at the end for WHERE id = ?
        values.append(item["id"])

        sql = f"UPDATE movies SET {', '.join(fields)} WHERE id = ?"
        self.cur.execute(sql, values)
        self.conn.commit()
        return True

    def del_item(self, item_id):
        self.cur.execute("DELETE FROM movies WHERE id = ?", (item_id,))
        self.conn.commit()

    def batch_replace(self):
        logger.info("# Excuting batch replacement...")
        items = self.get_items(Workflow.NONE)

        for item in items:
            if "." in item["title"] or "_" in item["title"]:
                logger.info(f"# Found it, updating ID {item['id']}: {item['title']}")
                new_title = item["title"].replace(".", " ").replace("_", " ")
                self.cur.execute(
                    "UPDATE movies SET title = ? WHERE id = ?", (new_title, item["id"])
                )
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
