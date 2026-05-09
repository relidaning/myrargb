from pydantic import BaseModel
from typing import ClassVar


def table(table_name: str):
    def decorator(cls):
        cls.table_name = table_name
        return cls

    return decorator


class DbModel(BaseModel):
    id: int | None = None
    table_name: ClassVar[str] = ""


@table("movies")
class Movie(DbModel):
    filename: str | None = None
    size: str | None = None
    title: str | None = None
    url: str | None = None
    score: str | None = None
    genre: str | None = None
    poster: str | None = None
    marked: str | None = None
    title_accurate: str | None = None
    trained_flag: str | None = None
    added: str | None = None


@table("collected")
class Collected(DbModel):
    start: str | None = None
    end: str | None = None
