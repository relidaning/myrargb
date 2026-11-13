from typing import Generic, TypeVar, List, ClassVar, get_args
from db_model import (
    Collected,
    Config,
    Movie,
    DbModel,
    TvShow,
    TvNotification,
    TvScheduleEntry,
)
from db.db import db
import logging


logger = logging.getLogger(__name__)


T = TypeVar("T", bound="DbModel")


class BaseRepository(Generic[T]):
    def __init__(self):
        orig_bases = getattr(self.__class__, "__orig_bases__", [])
        args = get_args(orig_bases[0]) if orig_bases else []
        self.model_class: type[T] = args[0] if args else None  # type: ignore[assignment]

    @property
    def table_name(self) -> str:
        return self.model_class.table_name  # 从类获取表名

    def find_one(self, id: int) -> T:
        sql = f"SELECT * FROM {self.table_name} WHERE id = ?"
        logger.debug(f"[v] Executing query: {sql}")
        db.cur.execute(sql, (id,))
        row = db.cur.fetchone()
        if not row:
            raise Exception("not found")
        columns = [col[0] for col in db.cur.description]
        data = dict(zip(columns, row))
        return self.model_class(**data)  # ✅ 用类构造实例

    def getMany(self) -> List[T]:
        sql = f"SELECT * FROM {self.table_name}"
        db.cur.execute(sql)
        logger.debug(f"[v] Executing query: {sql}")
        rows = db.cur.fetchall()
        columns = [col[0] for col in db.cur.description]
        return [self.model_class(**dict(zip(columns, row))) for row in rows]

    def update(self, model: T) -> bool:
        fields = [k for k, v in model.model_dump().items() if v is not None]
        values = [v for k, v in model.model_dump().items() if v is not None]
        values.append(model.id)

        set_clause = ", ".join([f"{k} = ?" for k in fields])

        sql = f"UPDATE {self.table_name} SET {set_clause} WHERE id = ?"
        logger.debug(f"[v] Executing: {sql}, {values}")
        db.cur.execute(sql, values)
        db.conn.commit()
        return True

    def insert(self, model: T) -> int:
        dump = {k: v for k, v in model.model_dump().items() if v is not None}
        keys = ", ".join(dump.keys())
        placeholders = ", ".join(["?"] * len(dump))
        sql = f"INSERT INTO {self.table_name} ({keys}) VALUES ({placeholders})"
        db.cur.execute(sql, list(dump.values()))
        db.conn.commit()
        logger.debug(f"[v] Executing query: {sql}")
        return db.cur.lastrowid

    def delete(self, id: int | None) -> bool:
        if not id:
            raise Exception(f"Deleting canceled: id of {self.table_name} is None.")
        sql = f"DELETE FROM {self.table_name} WHERE id = ?"
        db.cur.execute(sql, (id,))
        logger.debug(f"[v] Executing query: {sql} | id={id}")
        db.conn.commit()
        return True

    def execute_sql(self, sql: str, params: tuple = ()) -> List[T]:
        db.cur.execute(sql, params)
        logger.debug(f"[v] Executing: {sql} | params={params}")
        rows = db.cur.fetchall()
        if rows and len(rows) > 0:
            columns = [col[0] for col in db.cur.description]
            return [self.model_class(**dict(zip(columns, row))) for row in rows]
        else:
            return []

    def count(self, where_clause: str = "", params: tuple = ()) -> int:
        sql = f"SELECT COUNT(*) FROM {self.table_name}"
        if where_clause:
            sql += f" WHERE {where_clause}"
        db.cur.execute(sql, params)
        row = db.cur.fetchone()
        return row[0] if row else 0


class CollectedRepository(BaseRepository[Collected]):
    def getMany(self) -> List[Collected]:
        # The original collected schema has no explicit id column. Expose its
        # existing rowid to the model instead of requiring a storage migration.
        return self.execute_sql('SELECT rowid AS id, start, end FROM collected')

    def update(self, model: Collected) -> bool:
        db.cur.execute('UPDATE collected SET start = ?, end = ? WHERE rowid = ?',
                       (model.start, model.end, model.id))
        db.conn.commit()
        return True



class ConfigRepository(BaseRepository[Config]): ...


class MovieRepository(BaseRepository[Movie]):
    def update_if_title_current(self, updated: Movie, expected: Movie) -> bool:
        """Do not let an in-flight IMDb result overwrite a newer title correction."""
        values = {key: value for key, value in updated.model_dump().items()
                  if key != 'id' and value is not None}
        if not values:
            return False
        assignments = ', '.join(f'{key} = ?' for key in values)
        db.cur.execute(
            f'UPDATE items SET {assignments} WHERE id = ? AND filename IS ? '
            'AND title IS ? AND title_accurate IS ?',
            (*values.values(), expected.id, expected.filename, expected.title, expected.title_accurate),
        )
        changed = db.cur.rowcount == 1
        db.conn.commit()
        return changed

    def exists_by_url(self, url: str) -> bool:
        if not url:
            return False
        sql = "SELECT 1 FROM items WHERE url = ? LIMIT 1"
        db.cur.execute(sql, (url,))
        return db.cur.fetchone() is not None


class TvShowRepository(BaseRepository[TvShow]):
    def find_by_tvmaze_id(self, tvmaze_id: int) -> TvShow | None:
        sql = "SELECT * FROM tv_shows WHERE tvmaze_id = ? LIMIT 1"
        db.cur.execute(sql, (tvmaze_id,))
        row = db.cur.fetchone()
        if not row:
            return None
        columns = [col[0] for col in db.cur.description]
        return TvShow(**dict(zip(columns, row)))


class TvNotificationRepository(BaseRepository[TvNotification]): ...


class TvScheduleRepository(BaseRepository[TvScheduleEntry]):
    def find_by_date_and_tvmaze(self, day: str, tvmaze_id: int) -> TvScheduleEntry | None:
        sql = "SELECT * FROM tv_schedule WHERE date = ? AND tvmaze_id = ? LIMIT 1"
        db.cur.execute(sql, (day, tvmaze_id))
        row = db.cur.fetchone()
        if not row:
            return None
        columns = [col[0] for col in db.cur.description]
        return TvScheduleEntry(**dict(zip(columns, row)))

    def get_by_date(self, day: str, limit: int, offset: int) -> List[TvScheduleEntry]:
        # Followed shows sort first regardless of score, so a low-scored
        # followed show never gets pushed out of the top page by unfollowed
        # ones — otherwise it'd silently vanish from the calendar entirely.
        sql = (
            "SELECT ts.* FROM tv_schedule ts "
            "LEFT JOIN tv_shows sh ON sh.tvmaze_id = ts.tvmaze_id "
            "WHERE ts.date = ? "
            "ORDER BY CASE WHEN sh.follow_status = 'following' THEN 0 ELSE 1 END ASC, "
            "(ts.score IS NULL OR ts.score = '') ASC, CAST(ts.score AS REAL) DESC "
            "LIMIT ? OFFSET ?"
        )
        return self.execute_sql(sql, (day, limit, offset))

    def count_by_date(self, day: str) -> int:
        return self.count("date = ?", (day,))

    def delete_before(self, day: str):
        sql = "DELETE FROM tv_schedule WHERE date < ?"
        db.cur.execute(sql, (day,))
        db.conn.commit()


if __name__ == "__main__":
    service = MovieRepository()
    movie = service.find_one(1)
    print(f"result: {movie}")
