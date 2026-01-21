from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from crawl_rargb import crawl_rargb
from db import db
from finetuning import model 
from crawl_imdb import crawl_imdb
from workflow import Workflow
import logging as logger

app = Flask(__name__)
logger.basicConfig(level=logger.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
CORS(app)  # 允许跨域请求


@app.route('/', methods=['GET'])
def index():
    keyword = '2025'
    page = 8
    items = db.get_items(workflow=Workflow.NONE, limit=100, order_by='score DESC, marked asc')
    return render_template('index.html', items=items, keyword=keyword, page=page) 


@app.route('/crawl_more', methods=['GET'])
def crawl_more():
    keyword = request.args.get('keyword', '2025')
    page = request.args.get('page', 1, type=int)

    crawl_rargb(page, keyword)
    model.filter()
    crawl_imdb(keyword)
    
    return jsonify({"status": "success", "message": f"Crawling more items with keyword: {keyword}, and it's done!"})


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
    
    
@app.route('/movies/<int:item_id>/correct', methods=['PUT'])
def title_acurate(item_id):
    title_acurate = request.json.get('title_acurate', '')
    update_item = {
        "id": item_id,
        "title_acurate": title_acurate,
        "trained_flag": '0' # 0 for training, 1 for trained
    }
    db.update_item(update_item)
    return jsonify({"status": "success", "message": f"Movie {item_id} title was corrected."})
 


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
