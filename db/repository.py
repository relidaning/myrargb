from typing import Generic, TypeVar, List, ClassVar, get_args
from db_model import Collected, Config, Movie, DbModel
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
        logger.info(f"[v] Executing query: {sql}")
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
        logger.info(f"[v] Executing query: {sql}")
        rows = db.cur.fetchall()
        columns = [col[0] for col in db.cur.description]
        return [self.model_class(**dict(zip(columns, row))) for row in rows]

    def update(self, model: T) -> bool:
        fields = [k for k, v in model.model_dump().items() if v is not None]
        values = [v for k, v in model.model_dump().items() if v is not None]
        values.append(model.id)

        set_clause = ", ".join([f"{k} = ?" for k in fields])

        sql = f"UPDATE {self.table_name} SET {set_clause} WHERE id = ?"
        logger.info(f"[v] Executing: {sql}, {values}")
        db.cur.execute(sql, values)
        db.conn.commit()
        return True

    def insert(self, model: T) -> bool:
        dump = {k: v for k, v in model.model_dump().items() if v is not None}
        keys = ", ".join(dump.keys())
        placeholders = ", ".join(["?"] * len(dump))
        sql = f"INSERT INTO {self.table_name} ({keys}) VALUES ({placeholders})"
        db.cur.execute(sql, list(dump.values()))
        db.conn.commit()
        logger.info(f"[v] Executing query: {sql}")
        return True

    def delete(self, id: int | None) -> bool:
        if not id:
            raise Exception(f"Deleting canceled: id of {self.table_name} is None.")
        sql = f"DELETE FROM {self.table_name} WHERE id = {id}"
        db.cur.execute(sql)
        logger.info(f"[v] Executing query: {sql}")
        db.conn.commit()
        return True

    def execute_sql(self, sql: str, params: tuple = ()) -> List[T]:
        db.cur.execute(sql, params)
        logger.info(f"[v] Executing: {sql} | params={params}")
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


class CollectedRepository(BaseRepository[Collected]): ...


class ConfigRepository(BaseRepository[Config]): ...


class MovieRepository(BaseRepository[Movie]):
    def exists_by_url(self, url: str) -> bool:
        if not url:
            return False
        sql = "SELECT 1 FROM movies WHERE url = ? LIMIT 1"
        db.cur.execute(sql, (url,))
        return db.cur.fetchone() is not None


if __name__ == "__main__":
    service = MovieRepository()
    movie = service.find_one(1)
    print(f"result: {movie}")
