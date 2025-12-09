from flask import Flask, jsonify
from flask_cors import CORS
from crawl_rargb import crawl_rargb
from db import db

app = Flask(__name__)
CORS(app)  # 允许跨域请求


@app.route('/', methods=['GET'])
def get_movies():
    items = db.get_items()
    return jsonify(items)


@app.route('/crawl/rargb', methods=['GET'])
def crawl_rargb():
    items = crawl_rargb()
    db.save_items(items)
    return jsonify({"status": "success", "message": f"Added {len(movies)} items."})
    
    
if __name__ == '__main__':
    app.run(port=5000, debug=False)