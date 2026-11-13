import json
import logging
import time
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from utils.settings import settings
from utils.job_metrics import record_job
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
    @staticmethod
    def _run_job(callback, data, group):
        started = time.monotonic()
        outcome = 'failure'
        try:
            result = callback(data)
            outcome = 'failure' if result is False else 'success'
            return result
        finally:
            record_job(group, outcome, time.monotonic() - started)

    def spawn(self, group_id: str, topics: List[str], callback: Callable[[str], None], workers=1):
        if type(workers) is not int or workers < 1:
            raise ValueError('workers must be a positive integer')
        consumer = Consumer(
            {
                "bootstrap.servers": settings.kafka_bootstrap_servers,
                "group.id": group_id,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
                "enable.auto.offset.store": False,
                "max.poll.interval.ms": settings.kafka_max_poll_interval_ms,
            }
        )
        consumer.subscribe(topics)
        logger.info('Consumer started: group=%s workers=%s', group_id, workers)
        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='kafka-job') as executor:
                while True:
                    # Bounded batches allow parallel processing within a partition.
                    # Only this thread touches the Kafka consumer. Commit in delivery
                    # order after callbacks finish, so no unfinished offset is skipped.
                    if workers == 1:
                        message = consumer.poll(1.0)
                        messages = [message] if message is not None else []
                    else:
                        messages = consumer.consume(num_messages=workers, timeout=1.0)
                    pending = []
                    for msg in messages:
                        if msg.error():
                            logger.error('Kafka error: %s', msg.error())
                            continue
                        if msg.value() is None:
                            continue
                        data = msg.value().decode('utf-8')
                        pending.append((msg, executor.submit(self._run_job, callback, data, group_id)))
                    for msg, future in pending:
                        future.result()
                        try:
                            consumer.commit(message=msg, asynchronous=False)
                        except KafkaException as error:
                            logger.warning('Commit failed in %s: %s', group_id, error)
        except KeyboardInterrupt:
            logger.info('Consumer stopping: group=%s', group_id)
        finally:
            consumer.close()
