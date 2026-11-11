"""Regressions for remote fixes integrated with the src layout and diagnostics."""

import unittest
import test_crawler


class MergeTests(unittest.TestCase):
    def run_isolated(self, code):
        test_crawler.IsolatedCrawlerTests().run_isolated(code)

    def test_prediction_redelivery_false_positives_and_deduplication(self):
        self.run_isolated('''
from unittest.mock import patch
from db.service import MovieService
from db_model import Movie
from db.db import db
service = MovieService()
item = Movie(filename='Example.2020.1080p', year='2020', url='fixture-one')
item.id = service.movieRepository.insert(item)
with patch('db.service.model.predict', return_value=Movie(id=item.id, title='Example')) as predict, \\
     patch('db.service._bloom.hasItem', return_value=True), \\
     patch('db.service._producer.produce') as produce:
    service.predict(item)
    assert service.movieRepository.find_one(item.id).title == 'Example'
    assert produce.call_args.args[1]['movie']['year'] == '2020'
    service.predict(item)
    assert predict.call_count == 1
    assert produce.call_count == 1
other = Movie(filename='Another.2020', year='2020', url='fixture-two')
other.id = service.movieRepository.insert(other)
with patch('db.service.model.predict', return_value=Movie(id=other.id, title='EXAMPLE')), \\
     patch('db.service._bloom.hasItem', return_value=True):
    service.predict(other)
assert service.movieRepository.count() == 1
service.movieRepository.insert(Movie(title='Example', url='fixture-three'))
assert service.deduplicate() == 1
assert service.deduplicate() == 0
assert service.movieRepository.find_one(item.id).title == 'Example'
db.conn.close()
''')

    def test_interrupted_catchup_walks_past_known_urls(self):
        self.run_isolated('''
from unittest.mock import Mock, patch
from browserdriver.errors import BrowserChallengeError
from db.service import MovieService
from db_model import Movie
from db.db import db
service = MovieService()
service._update_collected_range('2020-01-01', '2020-01-10')
new = Movie(filename='Newest.2020', url='newest', added='2020-01-13')
missed = Movie(filename='Missed.2020', url='missed', added='2020-01-12')
old = Movie(filename='Old.2020', url='old', added='2020-01-10')
fake = Mock()
fake.crawl.side_effect = [[new], BrowserChallengeError('Blocked')]
with patch('db.service._producer.available', return_value=True), \\
     patch('db.service._producer.produce'), patch('db.service.RargbCrawler', return_value=fake):
    try:
        service.crawl_rargb()
        raise AssertionError('Expected failure')
    except BrowserChallengeError:
        pass
assert service._get_collected_range() == ('2020-01-01', '2020-01-10')
fake.crawl.side_effect = [[new], [missed], [old], []]
with patch('db.service._producer.available', return_value=True), \\
     patch('db.service._producer.produce'), patch('db.service.RargbCrawler', return_value=fake):
    assert service.crawl_rargb()
assert service.movieRepository.exists_by_url('missed')
assert service._get_collected_range() == ('2020-01-01', '2020-01-13')
db.conn.close()
''')

    def test_imdb_year_fallback_and_exact_match_priority(self):
        self.run_isolated('''
from unittest.mock import patch
from crawler import ImdbCrawler, clean_search_title
from db_model import Movie
assert clean_search_title('Example.2020.1080p.WEBRip', '2020') == 'Example'
def candidate(title, year):
    return f'<li class="ipc-metadata-list-summary-item"><h3 class="ipc-title__text">{title}</h3><li class="ipc-inline-list__item">{year}</li></li>'
with patch('crawler.DriverFactory.create_driver') as factory:
    crawler = ImdbCrawler()
    factory.return_value.fetch.return_value = '<ul class="ipc-metadata-list--base">'+candidate('Near', 2019)+candidate('Exact', 2020)+'</ul>'
    assert crawler.crawl(Movie(title='Example.2020.1080p', year='2020')).title == 'Exact'
    assert 'q=Example&' in factory.return_value.fetch.call_args.args[0]
    factory.return_value.fetch.return_value = '<ul class="ipc-metadata-list--base">'+candidate('Near', 2019)+'</ul>'
    assert crawler.crawl(Movie(title='Example', year='2020')).title == 'Near'
''')

    def test_kafka_polling_queue_retry_and_commit_recovery(self):
        self.run_isolated('''
from unittest.mock import Mock, patch
from confluent_kafka import KafkaException
from utils.kafka_utils import ProducerUtil, ConsumerUtil
broker = Mock()
broker.produce.side_effect = [BufferError(), None]
with patch('utils.kafka_utils.Producer', return_value=broker):
    producer = ProducerUtil()
    producer.produce('fixture', {'value': 1})
    assert broker.produce.call_count == 2
    assert [c.args[0] for c in broker.poll.call_args_list] == [1, 0]
message = Mock()
message.error.return_value = None
message.value.return_value = b'fixture'
consumer = Mock()
consumer.poll.side_effect = [message, message, KeyboardInterrupt()]
consumer.commit.side_effect = [KafkaException(), None]
callback = Mock()
with patch('utils.kafka_utils.Consumer', return_value=consumer):
    ConsumerUtil().spawn('fixture', ['fixture'], callback)
assert callback.call_count == 2
assert consumer.commit.call_count == 2
consumer.close.assert_called_once()
''')


if __name__ == '__main__':
    unittest.main()
