from flask import Flask, jsonify, render_template
from flask_cors import CORS
from crawl_rargb import crawl_rargb
from db import db

app = Flask(__name__)
CORS(app)  # 允许跨域请求


@app.route('/', methods=['GET'])
def index():
    keyword = '2025'
    items = db.get_items(sql='and score is not null', limit=100, order_by='score DESC, marked asc')
    return render_template('index.html', items=items, keyword=keyword) 


def crawl_rargb():
    items = crawl_rargb()
    db.save_items(items)
    return jsonify({"status": "success", "message": f"Added {len(movies)} items."})


def crawl_imdb():
    crawl_imdb()
    return jsonify({"status": "success", "message": "IMDB crawl initiated."})


@app.route('/movies/<int:item_id>/abandon', methods=['GET'])
def abandon_movie(item_id):
    update_item = {
        "id": item_id,
        "marked": '01'  # '01' for abandoned
    }
    db.update_item(update_item)
    return jsonify({"status": "success", "message": f"Movie {item_id} marked as abandoned."})


@app.route('/movies/<int:item_id>/watched', methods=['GET'])
def watched_movie(item_id):
    update_item = {
        "id": item_id,
        "marked": '02'  # '02' for watched
    }
    db.update_item(update_item)
    return jsonify({"status": "success", "message": f"Movie {item_id} marked as watched."})
    
    
if __name__ == '__main__':
    app.run(port=5000, debug=True)