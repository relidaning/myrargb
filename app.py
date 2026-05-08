from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from crawler.crawl_rargb import crawl_rargb
from db.db import db
from model.model import model
from crawler.crawl_imdb import crawl_imdb
from workflow import Workflow
import logging
import os
from dotenv import load_dotenv

DEBUG = os.getenv("DEBUG")

logger_level = "DEBUG" if DEBUG else os.getenv("LOGGER_LEVEL", "INFO").upper()
logging.basicConfig(
    level=logger_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("app.log", mode="a")],
)

load_dotenv()

app = Flask(__name__)
CORS(app)  # 允许跨域请求
logger = logging.getLogger(__name__)


@app.route("/", methods=["GET"])
def index():
    keyword = "2026"
    page = 1
    items = db.get_items(
        workflow=Workflow.NONE, limit=100, order_by="score DESC, marked asc"
    )

    finetunable = True
    items_traning = db.get_items(
        workflow=Workflow.TRAINING, limit=100, order_by="id DESC"
    )
    count = len(items_traning)
    if count <= 1:
        finetunable = False

    return render_template(
        "index.html", items=items, keyword=keyword, page=page, finetunable=finetunable
    )


@app.route("/crawl_rargb", methods=["GET"])
def crawl_from_rargb():
    keyword = request.args.get("keyword", "2025")
    page = request.args.get("page", 1, type=int)

    crawl_rargb(page, keyword)

    return jsonify(
        {
            "status": "success",
            "message": f"Crawling more items with keyword: {keyword}, and it's done!",
        }
    )


@app.route("/predict", methods=["GET"])
def predict():
    items = db.get_items(workflow=Workflow.PREDICT)
    model.predict(items)
    return jsonify(
        {
            "status": "success",
            "message": f"Done predicting!",
        }
    )


@app.route("/crawl_imdb", methods=["GET"])
def crawl_from_imdb():
    keyword = request.args.get("keyword", "2025")
    crawl_imdb(keyword)
    return jsonify(
        {
            "status": "success",
            "message": f"crwaling imdb, done!",
        }
    )


@app.route("/movies/<int:item_id>/abandon", methods=["GET"])
def abandon_movie(item_id):
    update_item = {
        "id": item_id,
        "marked": "01",  # '01' for abandoned
    }
    db.update_item(update_item)
    return jsonify(
        {"status": "success", "message": f"Movie {item_id} marked as abandoned."}
    )


@app.route("/movies/<int:item_id>/watched", methods=["GET"])
def watched_movie(item_id):
    update_item = {
        "id": item_id,
        "marked": "02",  # '02' for watched
    }
    db.update_item(update_item)
    return jsonify(
        {"status": "success", "message": f"Movie {item_id} marked as watched."}
    )


@app.route("/movies/<int:item_id>/correct", methods=["PUT"])
def title_accurate(item_id):
    title_accurate = request.json.get("title_accurate", "")
    update_item = {
        "id": item_id,
        "title_accurate": title_accurate,
        "trained_flag": "0",  # 0 for training, 1 for trained
    }
    db.update_item(update_item)
    return jsonify(
        {"status": "success", "message": f"Movie {item_id} title was corrected."}
    )


@app.route("/model/train", methods=["POST"])
def train():
    items = db.get_items(Workflow.TRAINING)
    model.train(items)
    for item in items:
        db.update_item({"id": item["id"], "trained_flag": "1"})  # Mark as trained

    return jsonify({"status": "success", "message": "Model training finished."})


from utils.bloom_utils import BloomUtils


@app.route("/bloom/deduplicate", methods=["GET"])
def deduplicate():
    bf = BloomUtils()
    items = db.get_items(workflow=Workflow.DEDUPLICATION, type="movies")
    for item in items:
        title = item["title"]  # title is the 4th column
        if title and bf.hasItem(title):
            db.del_item(item_id=item["id"])  # item_id is the 1st column
            logger.info(f"Duplicate item found and removed: {title}")
        else:
            bf.add(title)
            logger.info(f"Item added to Bloom filter: {title}")
    return jsonify({"status": "success", "message": "Deduplication completed."})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=DEBUG)
