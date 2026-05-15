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
import time

DEBUG = os.getenv("DEBUG", "false").lower() == "true"

logger_level = "DEBUG" if DEBUG else os.getenv("LOGGER_LEVEL", "INFO").upper()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s.%(funcName)s:%(lineno)s: %(message)s",
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
    from utils.pager_utils import page_offset, has_next_page, PER_PAGE

    keyword = request.args.get("keyword", "")
    page = request.args.get("page", 1, type=int)
    order_by = "score DESC, marked asc"

    offset = page_offset(page)
    movies = service.get_items(
        workflow=Workflow.NONE, limit=PER_PAGE, offset=offset, order_by=order_by
    )
    total = service.count_items(workflow=Workflow.NONE)

    finetunable = True
    movies_to_train = service.get_items(
        workflow=Workflow.TRAINING, limit=100, order_by="id DESC"
    )
    count = len(movies_to_train)
    if count <= 1:
        finetunable = False

    return render_template(
        "index.html",
        items=movies,
        keyword=keyword,
        page=page,
        has_next=has_next_page(total, page),
        finetunable=finetunable,
    )


@app.route("/crawl_rargb", methods=["GET"])
def crawl_from_rargb():
    keyword = request.args.get("keyword", "")
    page = request.args.get("page", 1, type=int)
    incremental = request.args.get("incremental", "false").lower() == "true"
    service.crawl_rargb(keyword, page, incremental=incremental)
    return jsonify(
        {
            "status": "success",
            "message": "Crawling completed.",
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


@app.route("/predict", methods=["GET"])
def predict_all():
    items = service.get_items(Workflow.PREDICT)
    total = len(items)
    for i, item in enumerate(items):
        service.predict(item)
        if i % 10 == 0:
            logger.info(f"[v] Predict progress: {i + 1}/{total}")
            time.sleep(1)  # brief pause to avoid hammering the model
    return jsonify(
        {"status": "success", "message": f"Predicted {total} items."}
    )


@app.route("/crawl_imdb", methods=["GET"])
def crawl_imdb_all():
    items = service.get_items(Workflow.SCORING)
    total = len(items)
    for i, item in enumerate(items):
        service.crawl_imdb(item)
        if i % 5 == 0:
            logger.info(f"[v] IMDb progress: {i + 1}/{total}")
            time.sleep(2)  # rate-limit IMDb requests
    return jsonify(
        {"status": "success", "message": f"IMDb crawl attempted for {total} items."}
    )


@app.route("/produce/predict", methods=["GET"])
def produce_predict_backlog():
    count = service.produce_predict_backlog()
    if count < 0:
        return jsonify({"status": "error", "message": "Kafka is unreachable."}), 503
    return jsonify(
        {"status": "success", "message": f"Produced predict tasks for {count} items."}
    )


@app.route("/produce/imdb", methods=["GET"])
def produce_imdb_backlog():
    count = service.produce_imdb_backlog()
    if count < 0:
        return jsonify({"status": "error", "message": "Kafka is unreachable."}), 503
    return jsonify(
        {"status": "success", "message": f"Produced IMDb tasks for {count} items."}
    )


@app.route("/deduplicate", methods=["GET"])
def deduplicate():
    bf = BloomUtils()
    items = service.get_items(workflow=Workflow.DEDUPLICATION)
    count = 0
    for item in items:
        title = item.title
        if not title:
            continue
        if bf.hasItem(title):
            movieRepository.delete(item.id)
            logger.info(f"Duplicate item found and removed: {title}")
            count += 1
        else:
            bf.add(title)
            logger.info(f"Item added to Bloom filter: {title}")
    return jsonify(
        {"status": "success", "message": f"Deduplication completed, {count} removed."}
    )


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
