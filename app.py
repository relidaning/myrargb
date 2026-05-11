import logging
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from db.repository import MovieRepository
from db.service import MovieService
from db_model import Movie
from model.model import model
from utils.bloom_utils import BloomUtils
from utils.kafka_utils import ConsumerUtil
from workflow import Workflow
from threading import Thread
from handler import handle_crawl_rargb, handle_predict, handle_crawl_imdb

DEBUG = os.getenv("DEBUG", "false").lower() == "true"

logger_level = "DEBUG" if DEBUG else os.getenv("LOGGER_LEVEL", "INFO").upper()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s.%(lineno)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("app.log")],
)

load_dotenv()

app = Flask(__name__)
CORS(app)  # 允许跨域请求
logger = logging.getLogger(__name__)

service = MovieService()
movieRepository = MovieRepository()


@app.route("/", methods=["GET"])
def index():
    keyword = "2026"
    page = 1
    movies = service.get_items(
        workflow=Workflow.NONE, limit=100, order_by="score DESC, marked asc"
    )

    finetunable = True
    movies_to_train = service.get_items(
        workflow=Workflow.TRAINING, limit=100, order_by="id DESC"
    )
    count = len(movies_to_train)
    if count <= 1:
        finetunable = False

    return render_template(
        "index.html", items=movies, keyword=keyword, page=page, finetunable=finetunable
    )


@app.route("/crawl_rargb", methods=["GET"])
def crawl_from_rargb():
    keyword = request.args.get("keyword", "2025")
    page = request.args.get("page", 1, type=int)
    service.crawl_rargb(keyword, page)
    return jsonify(
        {
            "status": "success",
            "message": f"Crawling more items with keyword: {keyword}, and it's done!",
        }
    )


@app.route("/movies/<int:item_id>/abandon", methods=["GET"])
def abandon_movie(item_id):
    movieRepository.update(Movie(id=item_id, marked="01"))
    return jsonify(
        {"status": "success", "message": f"Movie {item_id} marked as abandoned."}
    )


@app.route("/movies/<int:item_id>/watched", methods=["GET"])
def watched_movie(item_id):
    movieRepository.update(Movie(id=item_id, marked="02"))
    return jsonify(
        {"status": "success", "message": f"Movie {item_id} marked as watched."}
    )


@app.route("/movies/<int:item_id>/correct", methods=["PUT"])
def title_accurate(item_id):
    title_accurate = request.json.get("title_accurate", "")
    movieRepository.update(
        Movie(id=item_id, title_accurate=title_accurate, trained_flag="0")
    )
    return jsonify(
        {"status": "success", "message": f"Movie {item_id} title was corrected."}
    )


@app.route("/model/train", methods=["POST"])
def train():
    items = service.get_items(Workflow.TRAINING)
    model.train(items)
    for item in items:
        movieRepository.update(Movie(id=item.id, trained_flag="1"))
    return jsonify({"status": "success", "message": "Training have been done!"})


def deduplicate():
    bf = BloomUtils()
    items = service.get_items(workflow=Workflow.DEDUPLICATION)
    for item in items:
        title = item.title  # title is the 4th column
        if not title:
            continue
        if bf.hasItem(title):
            movieRepository.delete(item.id)  # item_id is the 1st column
            logger.info(f"Duplicate item found and removed: {title}")
        else:
            bf.add(title)
            logger.info(f"Item added to Bloom filter: {title}")
    return jsonify({"status": "success", "message": "Deduplication completed."})


if __name__ == "__main__":
    util = ConsumerUtil()
    Thread(
        target=util.spawn,
        kwargs={
            "group_id": "xyz.lidaning.myrargb.consumers.crawl_rargb",
            "topics": ["xyz.lidaning.myrargb.topics.crawl_rargb"],
            "callback": handle_crawl_rargb,
        },
        daemon=True,
    ).start()

    Thread(
        target=util.spawn,
        kwargs={
            "group_id": "xyz.lidaning.myrargb.consumers.predict",
            "topics": ["xyz.lidaning.myrargb.topics.predict"],
            "callback": handle_predict,
        },
        daemon=True,
    ).start()

    for i in range(5):
        Thread(
            target=util.spawn,
            kwargs={
                "group_id": "xyz.lidaning.myrargb.consumers.crawl_imdb",
                "topics": ["xyz.lidaning.myrargb.topics.crawl_imdb"],
                "callback": handle_crawl_imdb,
            },
            daemon=True,
        ).start()
    app.run(host="0.0.0.0", port=5000, debug=DEBUG)
