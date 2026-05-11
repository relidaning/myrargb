import json
from db.service import MovieService
from db_model import Movie


def handle_crawl_rargb(msg: str):
    data: dict = json.loads(msg)
    keyword: str = data["keyword"]
    page: str = data["page"]
    service = MovieService()
    service.crawl_rargb(keyword, page)


def handle_predict(msg: str):
    data: dict = json.loads(msg)
    keyword: str = data["keyword"]
    page: str = data["page"]
    movie: Movie = Movie(**data["movie"])
    service = MovieService()
    service.predict(movie, keyword, page)


def handle_crawl_imdb(msg: str):
    data: dict = json.loads(msg)
    keyword: str = data["keyword"]
    movie: Movie = Movie(**data["movie"])
    service = MovieService()
    service.crawl_imdb(movie, keyword)
