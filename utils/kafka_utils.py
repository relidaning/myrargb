from confluent_kafka import Producer
import json

producer = Producer({"bootstrap.servers": "kafka:9092"})

task = {"site": "imdb", "url": "https://www.imdb.com/title/tt0816692/"}

producer.produce("crawl.imdb", json.dumps(task).encode("utf-8"))

producer.flush()
