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


@table("items")
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
    year: str | None = None
    seed: int | None = None
    leech: int | None = None
    summary: str | None = None
    media_type: str | None = None
    release_scope: str | None = None
    season: int | None = None
    season_end: int | None = None
    episode: int | None = None
    episode_end: int | None = None

    @property
    def release_label(self):
        from utils.media import release_label
        return release_label(self)


@table("collected")
class Collected(DbModel):
    start: str | None = None
    end: str | None = None


@table("config")
class Config(DbModel):
    key: str | None = None
    value: str | None = None


@table("tv_shows")
class TvShow(DbModel):
    tvmaze_id: int | None = None
    name: str | None = None
    premiered: str | None = None
    ended: str | None = None
    status: str | None = None
    score: str | None = None
    genres: str | None = None
    poster: str | None = None
    summary: str | None = None
    network: str | None = None
    language: str | None = None
    follow_status: str | None = None
    next_episode_id: int | None = None
    updated_at: str | None = None


@table("tv_notifications")
class TvNotification(DbModel):
    tv_show_id: int | None = None
    message: str | None = None
    episode_code: str | None = None
    airdate: str | None = None
    read_flag: str | None = None
    created_at: str | None = None


@table("tv_schedule")
class TvScheduleEntry(DbModel):
    date: str | None = None
    tvmaze_id: int | None = None
    name: str | None = None
    poster: str | None = None
    score: str | None = None
    genres: str | None = None
    network: str | None = None
    language: str | None = None
    episode_code: str | None = None
    airdate: str | None = None
    updated_at: str | None = None
