import json
import logging

from db.service import MovieService
from db_model import Movie

logger = logging.getLogger(__name__)


def handle_crawl_rargb(msg: str):
    try:
        service = MovieService()
        service.crawl_rargb(incremental=True)
    except Exception:
        logger.exception("[x] crawl_rargb handler failed")


def handle_predict(msg: str):
    try:
        data: dict = json.loads(msg)
        movie: Movie = Movie(**data["movie"])
        service = MovieService()
        service.predict(movie)
    except Exception:
        logger.exception("[x] predict handler failed")


def handle_crawl_imdb(msg: str):
    try:
        data: dict = json.loads(msg)
        movie: Movie = Movie(**data["movie"])
        service = MovieService()
        service.crawl_imdb(movie)
    except Exception:
        logger.exception("[x] crawl_imdb handler failed")
