import json
import logging
from typing import Callable, List

from confluent_kafka import Consumer, Producer, KafkaException

logger = logging.getLogger(__name__)


class ProducerUtil:
    _available_cache: bool | None = None

    def __init__(self):
        self.producer = Producer({"bootstrap.servers": "kafka:9092"})

    def available(self) -> bool:
        if ProducerUtil._available_cache is None:
            try:
                self.producer.list_topics(timeout=3)
                ProducerUtil._available_cache = True
                logger.info("[v] Kafka broker reachable.")
            except KafkaException:
                ProducerUtil._available_cache = False
                logger.warning("[!] Kafka broker unreachable, processing inline.")
        return ProducerUtil._available_cache

    @staticmethod
    def _on_delivery(err, msg):
        if err is not None:
            logger.error(f"[x] Kafka delivery to {msg.topic()} failed: {err}")

    def produce(self, topic: str, task: dict):
        value = json.dumps(task).encode("utf-8")
        try:
            try:
                self.producer.produce(topic, value, on_delivery=self._on_delivery)
            except BufferError:
                # Local queue full: serve delivery reports to free slots, retry once.
                self.producer.poll(1)
                self.producer.produce(topic, value, on_delivery=self._on_delivery)
            # Serve delivery reports; librdkafka only frees a delivered message
            # once its report is polled, so without this the queue fills up for good.
            self.producer.poll(0)
        except KafkaException:
            ProducerUtil._available_cache = None
            raise

    def __del__(self):
        self.producer.flush(5)


class ConsumerUtil:
    def spawn(self, group_id: str, topics: List[str], callback: Callable[[str], None]):
        consumer = Consumer(
            {
                "bootstrap.servers": "kafka:9092",
                "group.id": group_id,
                "auto.offset.reset": "earliest",  # 没 offset 时从头读
                "enable.auto.commit": False,  # 生产建议关闭自动提交
            }
        )
        consumer.subscribe(topics)
        try:
            while True:
                msg = consumer.poll(1.0)  # 1秒超时
                if msg is None:
                    continue
                if msg.error():
                    logger.error(f"Kafka error: {msg.error()}")
                    continue

                val = msg.value()
                if val is None:
                    continue
                data = val.decode("utf-8")
                logger.info(f"[v] Consumer in {group_id} received: {data}")
                callback(data)
                consumer.commit(msg)

        except KeyboardInterrupt:
            print("stopping consumer")

        finally:
            consumer.close()
