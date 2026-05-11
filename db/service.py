import logging
import json
from db.repository import MovieRepository, CollectedRepository
from db_model import Movie, Collected
from utils.kafka_utils import ProducerUtil
from workflow import Workflow
from typing import List
from crawler import ImdbCrawler, RargbCrawler
from model.model import model
from utils.bloom_utils import BloomUtils

logger = logging.getLogger(__name__)


class MovieService:
    def __init__(self):
        self.collectedRepository = CollectedRepository()
        self.movieRepository = MovieRepository()

    def save_items(self, items):
        collected = Collected(id=1, start="", end="")
        for item in items:
            collectedlist = self.collectedRepository.getMany()
            if not collectedlist or len(collectedlist) < 1:
                collected.start, collected.end = item["added"], item["added"]
                self.collectedRepository.insert(collected)
            else:
                collected = collectedlist[0]
                if item["added"] > collected.start and item["added"] < collected.end:
                    continue

                if item["added"] < collected.start:
                    collected.start = item["added"]
                else:
                    collected.end = item["added"]

                self.movieRepository.insert(Movie(**item))
                self.collectedRepository.update(collected)

    def get_items(
        self, workflow: Workflow, sql="", limit=1000, order_by="id DESC"
    ) -> List[Movie]:
        exe_sql = " select id, filename, size, title, url, score, genre, poster, marked, title_accurate, trained_flag from movies where 1=1 "

        if workflow == Workflow.PREDICT:  # for prediction
            exe_sql += " and (title is null or title = '' ) and (trained_flag != '1') "
        elif workflow == Workflow.TRAINING:  # for finetuning
            exe_sql += " and title_accurate is not null and trained_flag = '0' "
        elif workflow == Workflow.QUERYING:  # for browsing
            exe_sql += (
                " and score is not null and score != '' and score != 'unmatched' "
            )
        elif workflow == Workflow.SCORING:  # for searching in imdb
            exe_sql += " and ( score is null or score = '' ) and ( title is not null or title_accurate is not null ) "
        elif workflow == Workflow.DEDUPLICATION:  # for searching in imdb
            exe_sql += " and title is not null and title != '' "
        elif workflow == Workflow.NONE:
            pass

        if sql:
            exe_sql += " " + sql + " "

        exe_sql += f" ORDER BY {order_by} "
        exe_sql += f" LIMIT {limit} "
        movies = self.movieRepository.execute_sql(exe_sql)
        return movies

    def crawl_rargb(self, keyword, page) -> bool:
        try:
            crawler = RargbCrawler()
            items = crawler.crawl(
                {
                    "keyword": keyword,
                    "page": page,
                }
            )
            if not items or len(items) < 1:
                logger.error(f"[x] Found nothing based on {keyword} & {page}")
                return False

            assert items is not None
            util = ProducerUtil()
            for item in items:
                self.movieRepository.insert(item)
                util.produce(
                    "xyz.lidaning.myrargb.topics.predict",
                    {"keyword": keyword, "page": page, "movie": item.model_dump()},
                )
        except Exception as e:
            logger.error(f"[x] Error on crawling:\n{e}")

        return True

    def crawl_imdb(self, m: Movie, keyword: str):
        crawler = ImdbCrawler()
        updated_m = crawler.crawl(m, keyword)
        if not updated_m:
            return
        self.movieRepository.update(updated_m)

    def predict(self, movie: Movie, keyword: str, page: str):
        bf = BloomUtils()
        util = ProducerUtil()
        predicted_m = model.predict(movie)
        if not predicted_m:
            return

        if not predicted_m.title:
            return

        hasItem = bf.hasItem(predicted_m.title)
        if hasItem:
            logger.info(
                f"[x] Found existing items: {predicted_m.title} in DB, skipping update."
            )
            self.movieRepository.delete(predicted_m.id)
            return

        self.movieRepository.update(predicted_m)
        util.produce(
            "xyz.lidaning.myrargb.topics.crawl_imdb",
            {"keyword": keyword, "page": page, "movie": predicted_m.model_dump()},
        )
