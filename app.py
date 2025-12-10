from flask import Flask, jsonify, render_template
from flask_cors import CORS
from crawl_rargb import crawl_rargb
from db import db

app = Flask(__name__)
CORS(app)  # 允许跨域请求


@app.route('/', methods=['GET'])
def index():
    items = db.get_items()
    return render_template('index.html', items=items) 


@app.route('/crawl/rargb', methods=['GET'])
def crawl_rargb():
    items = crawl_rargb()
    db.save_items(items)
    return jsonify({"status": "success", "message": f"Added {len(movies)} items."})


def crawl_imdb():
    crawl_imdb()
    return jsonify({"status": "success", "message": "IMDB crawl initiated."})
    
    
if __name__ == '__main__':
    app.run(port=5000, debug=True)