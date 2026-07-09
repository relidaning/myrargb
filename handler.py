import json
import logging

from db.service import MovieService
from db.tv_service import TvService
from db_model import Movie

logger = logging.getLogger(__name__)

_service = MovieService()
_tv_service = TvService()


def handle_crawl_rargb(msg: str):
    try:
        _service.crawl_rargb(incremental=True)
    except Exception:
        logger.exception("[x] crawl_rargb handler failed")


def handle_predict(msg: str):
    try:
        data: dict = json.loads(msg)
        movie: Movie = Movie(**data["movie"])
        _service.predict(movie)
    except Exception:
        logger.exception("[x] predict handler failed")


def handle_crawl_imdb(msg: str):
    try:
        data: dict = json.loads(msg)
        movie: Movie = Movie(**data["movie"])
        _service.crawl_imdb(movie)
    except Exception:
        logger.exception("[x] crawl_imdb handler failed")


def handle_crawl_tvmaze(msg: str):
    try:
        _tv_service.crawl_schedule()
        _tv_service.check_followed_episodes()
    except Exception:
        logger.exception("[x] crawl_tvmaze handler failed")
