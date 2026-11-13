"""Mixed catalog schema, release identity and TV enrichment regressions."""

import sqlite3
import unittest
import test_crawler
from utils.media import release_metadata, release_identity, release_label, series_year
from db.catalog_schema import migrate_catalog


class MetadataTests(unittest.TestCase):
    def test_shared_title_verification_key(self):
        from utils.titles import title_key
        from utils.bloom_utils import normalize
        for title in ('The.Matrix', 'THE MATRIX', 'thematrix', ' The.\tMatrix. '):
            self.assertEqual(title_key(title), 'thematrix')
            self.assertEqual(normalize(title), 'thematrix')
        self.assertEqual(title_key('Amélie'), 'amélie')
        self.assertEqual(title_key('Spider-Man'), 'spider-man')
        self.assertEqual(title_key(None), '')
        one = dict(title='The.Matrix', year='1999', filename='The.Matrix.1999.1080p')
        two = dict(title='THE MATRIX', year='1999', filename='The.Matrix.1999.720p')
        self.assertEqual(release_identity(one), release_identity(two))
        two['year'] = '2020'
        self.assertNotEqual(release_identity(one), release_identity(two))

    def test_episode_ranges_packs_and_specials(self):
        for filename, scope, season, end, episode, episode_end in [
            ('Show.S02E03.1080p', 'episode', 2, 2, 3, 3),
            ('Show.S01E07-E09.720p', 'episode', 1, 1, 7, 9),
            ('Show.s02e01-02.1080p', 'episode', 2, 2, 1, 2),
            ('Show S09 E01-22 1080p', 'episode', 9, 9, 1, 22),
            ('Show.S01-S05.1080p', 'season', 1, 5, None, None),
            ('Show Season 01 1080p', 'season', 1, 1, None, None),
            ('Show Complete Series Seasons 1 to 7', 'series', 1, 7, None, None),
            ('Show S00E01 720p', 'episode', 0, 0, 1, 1),
            ('Show.2x03.720p', 'episode', 2, 2, 3, 3),
        ]:
            with self.subTest(filename=filename):
                meta = release_metadata(filename)
                self.assertEqual(meta['media_type'], 'tv')
                self.assertEqual(tuple(meta[k] for k in ('release_scope','season','season_end','episode','episode_end')),
                                 (scope,season,end,episode,episode_end))
        self.assertEqual(release_metadata('Film.2020.1080p')['media_type'], 'movie')
        self.assertEqual(release_metadata('Formula.1.2026x109.Brazil')['media_type'], 'unknown')
        self.assertEqual(release_metadata('Sheriff.S02E05 Fire.S05E05.720p')['release_scope'], 'mixed')
        self.assertEqual(release_metadata('Show.S01E07-S02E09.720p')['release_scope'], 'mixed')
        self.assertEqual(series_year('Show 2010 Complete Series'), '2010')
        self.assertEqual(series_year('Show.S02E01.2026.1080p'), None)
        self.assertEqual(series_year('Show (2009) S04 1080p'), '2009')
        self.assertEqual(release_label(dict(filename='Show.S01E07-E09.720p')), 'S01E07–E09')

    def test_additive_migration_retains_existing_data_and_is_idempotent(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE movies(id INTEGER PRIMARY KEY,filename TEXT,title TEXT,title_accurate TEXT,trained_flag TEXT,marked TEXT)')
            db.execute("INSERT INTO movies VALUES(1,'Community (2009) S04 1080p','Community','Community','0','02')")
            result = migrate_catalog(db)
            self.assertEqual(result['classified'], {'tv': 1})
            self.assertEqual(db.execute('SELECT title_accurate,trained_flag,marked,media_type,season,release_scope FROM items').fetchone(),
                             ('Community','0','02','tv',4,'season'))
            self.assertEqual(migrate_catalog(db)['added_columns'], [])

    def test_different_media_episodes_ranges_packs_and_remakes_have_different_keys(self):
        identities = [release_identity(dict(title='Example',year=year,filename=name)) for name,year in [
            ('Example.S01E01.720p', None), ('Example.S01E02.720p', None),
            ('Example.S02E01.720p', None), ('Example.S01.720p', None),
            ('Example.S01E01-E02.720p', None), ('Example.1999.1080p', '1999'),
            ('Example.2020.1080p', '2020'),
        ]]
        self.assertEqual(len(set(identities)), len(identities))
        self.assertEqual(release_identity(dict(title='Example',filename='Example.S01E01.720p')),
                         release_identity(dict(title='Example',filename='Example.S01E01.1080p')))


class MixedCatalogTests(unittest.TestCase):
    def run_isolated(self, code):
        test_crawler.IsolatedCrawlerTests().run_isolated(code)

    def test_pipeline_preserves_episodes_and_passes_metadata_through_kafka(self):
        self.run_isolated('''
from unittest.mock import patch
from db.service import MovieService
from db_model import Movie
from workflow import Workflow
from db.db import db
service=MovieService()
with patch('db.service._producer.produce') as produce, patch('db.service._bloom.hasItem',return_value=True):
    for i,filename in enumerate(['Example.S01E01.720p','Example.S01E02.720p','Example.S01E01.1080p']):
        movie=Movie(filename=filename,url=f'fixture-{i}')
        movie.id=service.movieRepository.insert(movie)
        with patch('db.service.model.predict',return_value=Movie(id=movie.id,title='Example')):
            service.predict(movie)
    assert service.count_items(Workflow.NONE)==2
    assert produce.call_count==2
    assert produce.call_args.args[1]['movie']['episode']==2
    assert produce.call_args.args[1]['movie']['media_type']=='tv'
# Existing records with unparsed metadata also get safe identity comparisons.
assert service.deduplicate()==0
db.conn.close()
''')

    def test_tv_matching_skips_movies_and_ignores_episode_release_year(self):
        self.run_isolated('''
from unittest.mock import patch
from crawler import ImdbCrawler
from db_model import Movie

def candidate(title,year,kind):
    return f'<li class="ipc-metadata-list-summary-item"><h3 class="ipc-title__text">{title}</h3><li class="ipc-inline-list__item">{year}</li><span>{kind}</span></li>'
html='<ul class="ipc-metadata-list--base">'+candidate('Wrong movie','2026','Movie')+candidate('Correct show','2009–2015','TV Series')+'</ul>'
with patch('crawler.DriverFactory.create_driver') as factory:
    factory.return_value.fetch.return_value=html
    crawler=ImdbCrawler()
    result=crawler.crawl(Movie(id=1,title='Example S04E02',filename='Example.S04E02.2026.1080p',year='2026',media_type='tv'))
    assert result.title=='Correct show'
    assert 'q=Example&' in factory.return_value.fetch.call_args.args[0]
    factory.return_value.fetch.return_value='<ul class="ipc-metadata-list--base">'+candidate('Correct show','2009–2015','TV Series')+candidate('Wrong movie','2026','Movie')+'</ul>'
    result=crawler.crawl(Movie(id=2,title='Example',filename='Example.2026.1080p',year='2026',media_type='movie'))
    assert result.title=='Wrong movie'
''')

    def test_source_category_and_episode_data_are_preserved(self):
        self.run_isolated('''
from unittest.mock import patch
from crawler import RargbCrawler

def page(filename,category):
    return f'<table class="lista2t"><tr><th>Header</th></tr><tr><td>Icon</td><td><a href="/torrent/fixture">{filename}</a></td><td>{category}</td><td>2026-11-12</td><td>1 GB</td><td>12</td><td>3</td></tr></table>'
with patch('crawler.DriverFactory.create_driver') as factory:
    crawler=RargbCrawler()
    factory.return_value.fetch.return_value=page('Example.S01E07-E09.720p','TV')
    item=crawler.crawl({'page':1})[0]
    assert item.media_type=='tv' and item.episode_end==9 and item.seed==12
    assert item.model_dump()['release_scope']=='episode'
    factory.return_value.fetch.return_value=page('Undated Film 1080p','Movies')
    assert crawler.crawl({'page':1})[0].media_type=='movie'
''')

    def test_table_filters_counts_and_labels(self):
        self.run_isolated('''
import app
from db_model import Movie
from utils.media import release_metadata
from workflow import Workflow
from db.db import db
for i,(title,filename) in enumerate([('Example Show','Example.Show.S01E07-E09.720p'),('Example Film','Example.Film.2020.1080p')]):
    app.movieRepository.insert(Movie(title=title,filename=filename,url=f'fixture-{i}',**release_metadata(filename)))
client=app.app.test_client()
response=client.get('/?media=tv&q=Example&sort=seed')
assert response.status_code==200
assert b'Example Show' in response.data and b'Example Film' not in response.data
assert 'S01E07–E09' in response.get_data(as_text=True)
assert app.service.count_items(Workflow.NONE,media_type='tv')==1
assert len(app.service.get_items(Workflow.NONE,media_type='movie'))==1
assert client.get('/?media=invalid').status_code==200
db.conn.close()
''')


if __name__ == '__main__':
    unittest.main()
