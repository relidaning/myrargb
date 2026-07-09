import logging
import os
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from db.repository import MovieRepository
from db.service import MovieService
from db.tv_service import TvService
from db_model import Movie
from model.model import model
from db.service import _bloom
from utils.kafka_utils import ConsumerUtil
from workflow import Workflow
from threading import Event, Thread
from handler import (
    handle_crawl_rargb,
    handle_predict,
    handle_crawl_imdb,
    handle_crawl_tvmaze,
)
import time

TVMAZE_POLL_INTERVAL_SECONDS = 6 * 60 * 60

DEBUG = os.getenv("DEBUG", "false").lower() == "true"

logger_level = "DEBUG" if DEBUG else os.getenv("LOGGER_LEVEL", "INFO").upper()
logging.basicConfig(
    level=logger_level,
    format="%(asctime)s [%(levelname)s] %(name)s.%(funcName)s:%(lineno)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler("app.log", maxBytes=10 * 1024 * 1024, backupCount=3),
    ],
)

load_dotenv()

app = Flask(__name__)
# Without this, Jinja caches templates when debug is off — bind-mounted
# template edits (docker-compose.override.yml) wouldn't show until restart.
app.config["TEMPLATES_AUTO_RELOAD"] = True
CORS(app)  # 允许跨域请求
logger = logging.getLogger(__name__)

service = MovieService()
movieRepository = MovieRepository()
tv_service = TvService()


@app.route("/", methods=["GET"])
def index():
    from utils.pager_utils import page_offset, has_next_page, total_pages, page_range, PER_PAGE

    page = request.args.get("page", 1, type=int)
    query = request.args.get("q", "", type=str).strip()
    order_by = "score DESC, marked asc"

    offset = page_offset(page)
    movies = service.get_items(
        workflow=Workflow.NONE,
        limit=PER_PAGE,
        offset=offset,
        order_by=order_by,
        search=query or None,
    )
    total = service.count_items(workflow=Workflow.NONE, search=query or None)
    pages = page_range(page, total_pages(total))

    movies_to_train = service.get_items(
        workflow=Workflow.TRAINING, limit=100, order_by="id DESC"
    )
    trainable_count = len(movies_to_train)
    finetunable = trainable_count >= 20

    return render_template(
        "index.html",
        items=movies,
        page=page,
        q=query,
        has_next=has_next_page(total, page),
        total_pages=total_pages(total),
        pages=pages,
        finetunable=finetunable,
        trainable_count=trainable_count,
    )


@app.route("/crawl_rargb", methods=["GET"])
def crawl_from_rargb():
    incremental = request.args.get("incremental", "false").lower() == "true"
    service.crawl_rargb(incremental=incremental)
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
    items = service.get_items(workflow=Workflow.DEDUPLICATION)
    count = 0
    for item in items:
        title = item.title
        if not title:
            continue
        if _bloom.hasItem(title):
            movieRepository.delete(item.id)
            logger.info(f"Duplicate removed: {title}")
            count += 1
        else:
            _bloom.add(title)
    return jsonify(
        {"status": "success", "message": f"Deduplication completed, {count} removed."}
    )


@app.route("/tv", methods=["GET"])
def tv_index():
    from datetime import date, timedelta
    from utils.pager_utils import page_offset, has_next_page, total_pages, page_range, PER_PAGE

    tab = request.args.get("tab", "calendar", type=str)
    if tab not in ("calendar", "following", "abandoned"):
        tab = "calendar"

    if tab == "calendar":
        start_str = request.args.get("start", "", type=str)
        try:
            requested = date.fromisoformat(start_str) if start_str else date.today()
        except ValueError:
            requested = date.today()
        # Snap to the Sunday on/before the requested date, so columns always
        # read as a real week (Sun, Mon, Tue, ...) instead of a rolling
        # window that starts on whatever weekday "today" happens to be.
        start_date = requested - timedelta(days=(requested.weekday() + 1) % 7)
        calendar_days = tv_service.get_calendar(start_date)
        for day in calendar_days:
            day["weekday"] = date.fromisoformat(day["date"]).strftime("%a")
        return render_template(
            "tv.html",
            tab=tab,
            calendar_days=calendar_days,
            prev_start=(start_date - timedelta(days=7)).isoformat(),
            next_start=(start_date + timedelta(days=7)).isoformat(),
            today=date.today().isoformat(),
        )

    page = request.args.get("page", 1, type=int)
    offset = page_offset(page)
    shows = tv_service.get_items(tab, limit=PER_PAGE, offset=offset)
    total = tv_service.count_items(tab)
    pages = page_range(page, total_pages(total))

    return render_template(
        "tv.html",
        tab=tab,
        items=shows,
        page=page,
        has_next=has_next_page(total, page),
        total_pages=total_pages(total),
        pages=pages,
    )


@app.route("/tv/day/<date_str>", methods=["GET"])
def tv_day(date_str):
    offset = request.args.get("offset", 0, type=int)
    limit = request.args.get("limit", 10, type=int)
    shows = tv_service.get_day_shows(date_str, offset=offset, limit=limit)
    return jsonify({"status": "success", "shows": shows})


@app.route("/tv/show/<int:tvmaze_id>", methods=["GET"])
def tv_show_detail(tvmaze_id):
    detail = tv_service.get_show_detail(tvmaze_id)
    if detail is None:
        return jsonify({"status": "error", "message": f"Show {tvmaze_id} not found."}), 404
    return jsonify({"status": "success", "show": detail})


@app.route("/tv/search", methods=["GET"])
def tv_search():
    q = request.args.get("q", "", type=str).strip()
    if not q:
        return jsonify({"status": "success", "results": []})
    results = tv_service.search_shows(q)
    return jsonify({"status": "success", "results": results})


@app.route("/tv/<int:item_id>/follow", methods=["GET"])
def follow_tv(item_id):
    tv_service.follow(item_id)
    return jsonify({"status": "success", "message": f"TV show {item_id} followed."})


@app.route("/tv/<int:item_id>/abandon", methods=["GET"])
def abandon_tv(item_id):
    tv_service.abandon(item_id)
    return jsonify({"status": "success", "message": f"TV show {item_id} abandoned."})


@app.route("/tv/tvmaze/<int:tvmaze_id>/follow", methods=["POST"])
def follow_tv_by_tvmaze(tvmaze_id):
    try:
        tv = tv_service.follow_by_tvmaze_id(tvmaze_id)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 404
    return jsonify({"status": "success", "message": f"{tv.name} followed.", "id": tv.id})


@app.route("/tv/notifications", methods=["GET"])
def tv_notifications():
    notifications = tv_service.get_unread_notifications()
    return jsonify(
        {"status": "success", "notifications": [n.model_dump() for n in notifications]}
    )


@app.route("/tv/notifications/<int:notification_id>/read", methods=["POST"])
def read_tv_notification(notification_id):
    tv_service.mark_notification_read(notification_id)
    return jsonify({"status": "success", "message": f"Notification {notification_id} marked read."})


@app.route("/crawl_tvmaze", methods=["GET"])
def crawl_tvmaze():
    if not tv_service.trigger_crawl():
        return jsonify({"status": "error", "message": "Kafka is unreachable."}), 503
    return jsonify({"status": "success", "message": "TVMaze crawl scheduled."})


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

    Thread(
        target=util.spawn,
        kwargs={
            "group_id": "xyz.lidaning.myrargb.consumers.crawl_tvmaze",
            "topics": ["xyz.lidaning.myrargb.topics.crawl_tvmaze"],
            "callback": handle_crawl_tvmaze,
        },
        daemon=True,
    ).start()

    def _tvmaze_poll_loop():
        """TVMaze has no push mechanism, so periodically trigger a rescan
        instead of waiting for a manual /crawl_tvmaze call."""
        stop_event = Event()
        while not stop_event.is_set():
            try:
                if not tv_service.trigger_crawl():
                    logger.warning("[!] Kafka unreachable, skipping scheduled TVMaze poll.")
            except Exception:
                logger.exception("[x] Scheduled TVMaze poll failed to produce")
            stop_event.wait(TVMAZE_POLL_INTERVAL_SECONDS)

    Thread(target=_tvmaze_poll_loop, daemon=True).start()

    app.run(host="0.0.0.0", port=5000, debug=DEBUG)
