import logging
import time

import requests

logger = logging.getLogger(__name__)

TVMAZE_RATE_LIMIT_DELAY = 0.3


class TvMazeClient:
    """Thin JSON client for the TVMaze API (https://www.tvmaze.com/api).

    No auth/browser needed — plain REST+JSON, unlike RargbCrawler/ImdbCrawler.
    A requests.Session picks up HTTP_PROXY/HTTPS_PROXY from the environment
    the same way the browser drivers do.
    """

    BASE = "https://api.tvmaze.com"

    def __init__(self):
        self._session = requests.Session()

    def _get(self, path: str, params: dict | None = None) -> dict | list | None:
        try:
            resp = self._session.get(f"{self.BASE}{path}", params=params, timeout=10)
            time.sleep(TVMAZE_RATE_LIMIT_DELAY)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"[x] TVMaze request failed for {path}: {e}")
            return None

    def schedule(self, date: str, web: bool = False) -> list[dict]:
        """Episodes airing on `date` (YYYY-MM-DD). `web` selects streaming/web
        channel schedule (/schedule/web) instead of the TV schedule (/schedule).
        """
        path = "/schedule/web" if web else "/schedule"
        data = self._get(path, params={"date": date})
        return data if isinstance(data, list) else []

    def show(self, tvmaze_id: int, embed: str = "nextepisode") -> dict | None:
        return self._get(f"/shows/{tvmaze_id}", params={"embed": embed})

    def search(self, query: str) -> list[dict]:
        data = self._get("/search/shows", params={"q": query})
        return data if isinstance(data, list) else []
