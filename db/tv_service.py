import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import List

from confluent_kafka import KafkaException

from db.repository import TvShowRepository, TvNotificationRepository, TvScheduleRepository
from db_model import TvShow, TvNotification, TvScheduleEntry
from tv_crawler import TvMazeClient
from utils.kafka_utils import ProducerUtil

logger = logging.getLogger(__name__)

_producer = ProducerUtil()

TOPIC_CRAWL_TVMAZE = "xyz.lidaning.myrargb.topics.crawl_tvmaze"

CALENDAR_DAYS_DEFAULT = 7
DAY_PAGE_SIZE = 10
SCHEDULE_DAYS_BACK = 7
SCHEDULE_DAYS_FORWARD = 14

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str | None) -> str | None:
    return _TAG_RE.sub("", text) if text else text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TvService:
    def __init__(self):
        self.tvShowRepository = TvShowRepository()
        self.tvNotificationRepository = TvNotificationRepository()
        self.tvScheduleRepository = TvScheduleRepository()
        self._client = TvMazeClient()

    def _upsert_show(self, show: dict):
        tvmaze_id = show.get("id")
        if tvmaze_id is None:
            return

        image = show.get("image") or {}
        rating = (show.get("rating") or {}).get("average")
        network = (show.get("network") or {}).get("name") or (
            show.get("webChannel") or {}
        ).get("name")

        tv = TvShow(
            tvmaze_id=tvmaze_id,
            name=show.get("name"),
            premiered=show.get("premiered"),
            ended=show.get("ended"),
            status=show.get("status"),
            score=str(rating) if rating is not None else None,
            genres=",".join(show.get("genres") or []) or None,
            poster=image.get("medium") or image.get("original"),
            summary=_strip_html(show.get("summary")),
            network=network,
            language=show.get("language"),
            updated_at=_now_iso(),
        )

        existing = self.tvShowRepository.find_by_tvmaze_id(tvmaze_id)
        if existing:
            tv.id = existing.id
            self.tvShowRepository.update(tv)
        else:
            self.tvShowRepository.insert(tv)

    def _follow_map(self, tvmaze_ids: set[int]) -> dict[int, dict]:
        """tvmaze_id -> {'id': local row id, 'follow_status': ...} for known shows."""
        if not tvmaze_ids:
            return {}
        placeholders = ",".join("?" * len(tvmaze_ids))
        rows = self.tvShowRepository.execute_sql(
            f"SELECT * FROM tv_shows WHERE tvmaze_id IN ({placeholders})",
            tuple(tvmaze_ids),
        )
        return {r.tvmaze_id: {"id": r.id, "follow_status": r.follow_status} for r in rows}

    def _show_brief(self, show: dict, follow_map: dict, episode: dict | None = None) -> dict:
        """Flatten a raw TVMaze show (+ optional episode) into a JSON/template-friendly dict."""
        tvmaze_id = show.get("id")
        rating = (show.get("rating") or {}).get("average")
        image = show.get("image") or {}
        network = (show.get("network") or {}).get("name") or (
            show.get("webChannel") or {}
        ).get("name")
        local = follow_map.get(tvmaze_id) or {}

        episode_code = None
        if episode:
            season, number = episode.get("season"), episode.get("number")
            if season is not None and number is not None:
                episode_code = f"S{season:02d}E{number:02d}"

        return {
            "tvmaze_id": tvmaze_id,
            "id": local.get("id"),
            "name": show.get("name"),
            "poster": image.get("medium") or image.get("original"),
            "score": rating,
            "genres": show.get("genres") or [],
            "network": network,
            "language": show.get("language"),
            "premiered": show.get("premiered"),
            "episode_code": episode_code,
            "follow_status": local.get("follow_status"),
        }

    def _upsert_schedule_entry(self, day: str, show: dict, episode: dict):
        tvmaze_id = show.get("id")
        if tvmaze_id is None:
            return

        rating = (show.get("rating") or {}).get("average")
        image = show.get("image") or {}
        network = (show.get("network") or {}).get("name") or (
            show.get("webChannel") or {}
        ).get("name")
        season, number = episode.get("season"), episode.get("number")
        episode_code = (
            f"S{season:02d}E{number:02d}"
            if season is not None and number is not None
            else None
        )

        entry = TvScheduleEntry(
            date=day,
            tvmaze_id=tvmaze_id,
            name=show.get("name"),
            poster=image.get("medium") or image.get("original"),
            score=str(rating) if rating is not None else None,
            genres=",".join(show.get("genres") or []) or None,
            network=network,
            language=show.get("language"),
            episode_code=episode_code,
            airdate=episode.get("airdate"),
            updated_at=_now_iso(),
        )
        existing = self.tvScheduleRepository.find_by_date_and_tvmaze(day, tvmaze_id)
        if existing:
            entry.id = existing.id
            self.tvScheduleRepository.update(entry)
        else:
            self.tvScheduleRepository.insert(entry)

    def trigger_crawl(self) -> bool:
        """Schedule a crawl by producing to the crawl_tvmaze topic. The crawl
        itself takes minutes of rate-limited TVMaze calls, so callers must not
        run it inline — the Kafka consumer picks it up on its own thread.
        Returns False when Kafka is unreachable.
        """
        if not _producer.available():
            return False
        try:
            _producer.produce(TOPIC_CRAWL_TVMAZE, {})
            return True
        except KafkaException:
            logger.exception("[x] Failed to produce crawl_tvmaze trigger")
            return False

    def crawl_schedule(
        self, days_back: int = SCHEDULE_DAYS_BACK, days_forward: int = SCHEDULE_DAYS_FORWARD
    ) -> int:
        """Populate the tv_schedule cache from TVMaze's TV + web schedules for
        [-days_back, +days_forward] days. This is the periodic background job —
        the calendar view only ever reads from this cache, so page loads never
        wait on a TVMaze round trip.
        """
        today = date.today()
        count = 0
        for offset in range(-days_back, days_forward + 1):
            day = (today + timedelta(days=offset)).isoformat()
            seen_today: set[int] = set()
            for web in (False, True):
                for ep in self._client.schedule(day, web=web):
                    show = ep.get("show") or (ep.get("_embedded") or {}).get("show")
                    sid = show.get("id") if show else None
                    if sid is None or sid in seen_today:
                        continue
                    seen_today.add(sid)
                    self._upsert_schedule_entry(day, show, ep)
                    count += 1
        self.tvScheduleRepository.delete_before((today - timedelta(days=days_back)).isoformat())
        logger.info(f"[v] Cached {count} schedule entries.")
        return count

    def _entries_to_briefs(self, entries: List[TvScheduleEntry], follow_map: dict) -> list[dict]:
        out = []
        for e in entries:
            local = follow_map.get(e.tvmaze_id) or {}
            out.append({
                "tvmaze_id": e.tvmaze_id,
                "id": local.get("id"),
                "name": e.name,
                "poster": e.poster,
                "score": float(e.score) if e.score else None,
                "genres": e.genres.split(",") if e.genres else [],
                "network": e.network,
                "language": e.language,
                "episode_code": e.episode_code,
                "follow_status": local.get("follow_status"),
            })
        return out

    def get_calendar(
        self, start: date, days: int = CALENDAR_DAYS_DEFAULT, page_size: int = DAY_PAGE_SIZE
    ) -> list[dict]:
        """Top `page_size` cached shows per day for [start, start+days) — fast,
        DB-only read. Use get_day_shows() to page past page_size for a day.
        """
        calendar = []
        for offset in range(days):
            day = (start + timedelta(days=offset)).isoformat()
            entries = self.tvScheduleRepository.get_by_date(day, limit=page_size, offset=0)
            total = self.tvScheduleRepository.count_by_date(day)
            follow_map = self._follow_map({e.tvmaze_id for e in entries})
            calendar.append({
                "date": day,
                "shows": self._entries_to_briefs(entries, follow_map),
                "total": total,
            })
        return calendar

    def get_day_shows(self, day: str, offset: int, limit: int = DAY_PAGE_SIZE) -> list[dict]:
        entries = self.tvScheduleRepository.get_by_date(day, limit=limit, offset=offset)
        follow_map = self._follow_map({e.tvmaze_id for e in entries})
        return self._entries_to_briefs(entries, follow_map)

    def search_shows(self, query: str) -> list[dict]:
        results = self._client.search(query)
        shows = [r["show"] for r in results if r.get("show")]
        follow_map = self._follow_map({s["id"] for s in shows if s.get("id") is not None})
        return [self._show_brief(show, follow_map) for show in shows]

    def get_show_detail(self, tvmaze_id: int) -> dict | None:
        """Full show info for the hover popover. Served from tv_shows when
        cached; calendar/search entries usually aren't there yet (tv_schedule
        has no summary), so on a miss fetch from TVMaze and upsert — the next
        hover is then DB-only.
        """
        tv = self.tvShowRepository.find_by_tvmaze_id(tvmaze_id)
        if tv is None or tv.summary is None:
            show = self._client.show(tvmaze_id)
            if show:
                self._upsert_show(show)
                tv = self.tvShowRepository.find_by_tvmaze_id(tvmaze_id)
        if tv is None:
            return None
        return {
            "tvmaze_id": tv.tvmaze_id,
            "id": tv.id,
            "name": tv.name,
            "poster": tv.poster,
            "summary": tv.summary,
            "premiered": tv.premiered,
            "ended": tv.ended,
            "status": tv.status,
            "score": float(tv.score) if tv.score else None,
            "genres": tv.genres.split(",") if tv.genres else [],
            "network": tv.network,
            "language": tv.language,
            "follow_status": tv.follow_status,
        }

    def follow_by_tvmaze_id(self, tvmaze_id: int) -> TvShow:
        """Follow a show discovered via search/calendar that may not be in tv_shows yet."""
        show = self._client.show(tvmaze_id, embed="nextepisode")
        if not show:
            raise ValueError(f"TVMaze show {tvmaze_id} not found")
        self._upsert_show(show)
        tv = self.tvShowRepository.find_by_tvmaze_id(tvmaze_id)
        tv.follow_status = "following"
        self.tvShowRepository.update(tv)
        self._baseline_next_episode(tv, show_data=show)
        return self.tvShowRepository.find_one(tv.id)

    def check_followed_episodes(self) -> int:
        """Poll each followed show's next episode; notify on change."""
        following = self.tvShowRepository.execute_sql(
            "SELECT * FROM tv_shows WHERE follow_status = ?", ("following",)
        )
        notified = 0
        for tv in following:
            if not tv.tvmaze_id:
                continue
            data = self._client.show(tv.tvmaze_id, embed="nextepisode")
            if not data:
                continue
            next_ep = (data.get("_embedded") or {}).get("nextepisode")
            if not next_ep or next_ep.get("id") == tv.next_episode_id:
                continue

            season, number = next_ep.get("season"), next_ep.get("number")
            episode_code = (
                f"S{season:02d}E{number:02d}"
                if season is not None and number is not None
                else None
            )
            airdate = next_ep.get("airdate")
            message = f"{tv.name} — {episode_code or 'a new episode'} airs {airdate or 'soon'}"

            self.tvNotificationRepository.insert(
                TvNotification(
                    tv_show_id=tv.id,
                    message=message,
                    episode_code=episode_code,
                    airdate=airdate,
                    read_flag="0",
                    created_at=_now_iso(),
                )
            )
            self.tvShowRepository.update(
                TvShow(id=tv.id, next_episode_id=next_ep.get("id"))
            )
            notified += 1
        logger.info(f"[v] check_followed_episodes: {notified} new-episode notifications.")
        return notified

    def _baseline_next_episode(self, tv: TvShow, show_data: dict | None = None):
        """Record the show's current next-episode id without notifying — called
        right after a follow so the first background poll doesn't fire a
        notification for an episode the user already knew about when following.
        """
        if not tv.tvmaze_id:
            return
        data = show_data if show_data is not None else self._client.show(
            tv.tvmaze_id, embed="nextepisode"
        )
        next_ep = (data or {}).get("_embedded", {}).get("nextepisode")
        if next_ep:
            self.tvShowRepository.update(
                TvShow(id=tv.id, next_episode_id=next_ep.get("id"))
            )

    def follow(self, tv_show_id: int):
        tv = self.tvShowRepository.find_one(tv_show_id)
        tv.follow_status = "following"
        self.tvShowRepository.update(tv)
        self._baseline_next_episode(tv)

    def abandon(self, tv_show_id: int):
        self.tvShowRepository.update(TvShow(id=tv_show_id, follow_status="abandoned"))

    def get_items(self, filter_: str, limit: int = 20, offset: int = 0) -> List[TvShow]:
        where = "follow_status = 'following'" if filter_ == "following" else "follow_status = 'abandoned'"
        sql = f"SELECT * FROM tv_shows WHERE {where} ORDER BY name ASC LIMIT ? OFFSET ?"
        return self.tvShowRepository.execute_sql(sql, (limit, offset))

    def count_items(self, filter_: str) -> int:
        where = "follow_status = 'following'" if filter_ == "following" else "follow_status = 'abandoned'"
        return self.tvShowRepository.count(where)

    def get_unread_notifications(self) -> List[TvNotification]:
        return self.tvNotificationRepository.execute_sql(
            "SELECT * FROM tv_notifications WHERE read_flag = '0' ORDER BY created_at DESC"
        )

    def mark_notification_read(self, notification_id: int):
        self.tvNotificationRepository.update(
            TvNotification(id=notification_id, read_flag="1")
        )
