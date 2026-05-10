import json
from db.service import MovieService
from utils.kafka_utils import ProducerUtil


def handle_crawl_rargb(msg: str):
    data: dict = json.loads(msg)
    keyword: str = data["keyword"]
    page: str = data["page"]
    service = MovieService()
    service.crawl_rargb(keyword, page)
    util = ProducerUtil()
    util.produce(
        "xyz.lidaning.myrargb.topics.predict", {"keyword": keyword, "page": page}
    )


def handle_predict(msg: str):
    data: dict = json.loads(msg)
    keyword: str = data["keyword"]
    page: str = data["page"]
    service = MovieService()
    service.predict()
    util = ProducerUtil()
    util.produce(
        "xyz.lidaning.myrargb.topics.crawl_imdb", {"keyword": keyword, "page": page}
    )


def handle_crawl_imdb(msg: str):
    data: dict = json.loads(msg)
    keyword: str = data["keyword"]
    service = MovieService()
    service.crawl_imdb(keyword)
