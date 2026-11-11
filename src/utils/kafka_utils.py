import json
import logging
import time
from threading import Lock
from utils.settings import settings
from typing import Callable, List

from confluent_kafka import Consumer, Producer, KafkaException

logger = logging.getLogger(__name__)


class ProducerUtil:
    def __init__(self):
        self._producer = None
        self._lock = Lock()
        self._available_cache = None
        self._checked_at = 0.0

    @property
    def producer(self):
        # UI-only startup does not spawn librdkafka threads or contact the broker.
        with self._lock:
            if self._producer is None:
                self._producer = Producer({"bootstrap.servers": settings.kafka_bootstrap_servers})
            return self._producer

    def available(self) -> bool:
        now = time.monotonic()
        if (self._available_cache is None
                or now - self._checked_at >= settings.kafka_probe_cache_seconds):
            try:
                self.producer.list_topics(timeout=settings.kafka_probe_timeout)
                self._available_cache = True
                logger.info("[v] Kafka broker reachable.")
            except KafkaException:
                self._available_cache = False
                logger.warning("[!] Kafka broker unreachable; background work unavailable.")
            self._checked_at = time.monotonic()
        return self._available_cache

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
            self._available_cache = None
            raise

    def __del__(self):
        if self._producer is not None:
            self._producer.flush(timeout=settings.kafka_probe_timeout)


class ConsumerUtil:
    def spawn(self, group_id: str, topics: List[str], callback: Callable[[str], None]):
        consumer = Consumer(
            {
                "bootstrap.servers": settings.kafka_bootstrap_servers,
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
                try:
                    consumer.commit(msg)
                except KafkaException as e:
                    # A failed commit (broker restart, rebalance) must not end
                    # this loop, or the consumer thread dies silently. The
                    # message may be redelivered, as it would be after a restart.
                    logger.warning(f"[!] Commit failed in {group_id}: {e}")

        except KeyboardInterrupt:
            print("stopping consumer")

        finally:
            consumer.close()
