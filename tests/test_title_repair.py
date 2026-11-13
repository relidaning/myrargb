"""Title repairs must be reversible and preserve reviewed/concurrently edited data."""

import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import test_crawler

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('repair_titles', ROOT / 'scripts/repair_titles.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


class RepairTests(unittest.TestCase):
    def test_filename_boundaries_preserve_numbers_and_event_dates(self):
        for filename, expected in [
            ('Tuesday 2023 1080p WEB-DL HEVC x265', 'Tuesday'),
            ('Zero (2018) 1080p BluRay', 'Zero'),
            ('Jurassic Park III (2001) 2160p BluRay', 'Jurassic Park III'),
            ('2001 A Space Odyssey (1968) 1080p BluRay', '2001 A Space Odyssey'),
            ('Blade Runner 2049 (2017) 1080p WEBRip', 'Blade Runner 2049'),
            ('Community (2009) S04 1080p BluRay', 'Community'),
            ('Formula.1.2026.Brazil.Race.1080p.SS', 'Formula 1 2026 Brazil Race'),
        ]:
            with self.subTest(filename=filename):
                self.assertEqual(repair.source_title(filename), expected)
        self.assertIsNone(repair.source_title('Sheriff.Country.S02E05 Fire.Country.S05E05.720p.HDTV'))

    def test_review_gate_backup_and_concurrency_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'catalog.db'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE movies(id INTEGER PRIMARY KEY,filename TEXT,title TEXT,title_accurate TEXT,score TEXT,marked TEXT,trained_flag TEXT)')
                connection.executemany('INSERT INTO movies VALUES(?,?,?,?,?,?,?)', [
                    (1, 'Tuesday 2023 1080p WEB-DL', 'Thursday 2023 1080p', None, '7.2', '02', '1'),
                    (2, 'Zero (2018) 1080p', 'Zero (2017) 1080p', 'My correction', '6.0', '01', '1'),
                    (3, 'Tenet 2020 2160p', 'Tenet 2160p', None, '7.3', '00', '0'),
                ])
                plan = repair.propose(connection, {'Tuesday 2023 1080p WEB-DL': 'Thursday 2023 1080p'})
                self.assertEqual({entry['id'] for entry in plan}, {1, 3})
                self.assertFalse(any(entry['apply'] for entry in plan))
                for entry in plan:
                    entry['apply'] = True
                connection.execute("UPDATE movies SET title='A concurrent edit' WHERE id=3")
                connection.commit()
            result = repair.apply_plan(database, plan, root / 'backups', root / 'bf.bin', mark_training=True)
            self.assertEqual(result['applied'], 1)
            self.assertEqual(result['skipped'], [3])
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute('SELECT title,title_accurate,score,marked,trained_flag FROM movies WHERE id=1').fetchone(),
                                 ('Tuesday', 'Tuesday', '7.2', '02', '0'))
                self.assertEqual(connection.execute('SELECT title_accurate FROM movies WHERE id=2').fetchone()[0], 'My correction')
            with sqlite3.connect(result['backup']) as backup:
                self.assertEqual(backup.execute('SELECT title FROM movies WHERE id=1').fetchone()[0], 'Thursday 2023 1080p')
            self.assertEqual(len(json.loads(Path(result['audit']).read_text())['applied']), 1)

    def test_imdb_uses_current_titles_and_discards_inflight_stale_results(self):
        test_crawler.IsolatedCrawlerTests().run_isolated('''
from unittest.mock import Mock, patch
from db.service import MovieService
from db_model import Movie
from db.db import db
service = MovieService()
movie = Movie(filename='Tuesday 2023 1080p', title='Tuesday')
movie.id = service.movieRepository.insert(movie)
stale_message = Movie(id=movie.id, title='Thursday', filename=movie.filename)
crawler = Mock()
crawler.crawl.return_value = Movie(id=movie.id, title='Tuesday', score='7.0')
with patch('db.service._get_imdb_crawler', return_value=crawler):
    service.crawl_imdb(stale_message)
assert crawler.crawl.call_args.args[0].title == 'Tuesday'

def race(current):
    service.movieRepository.update(Movie(id=current.id, title='Newer correction'))
    return Movie(id=current.id, title='Stale result', score='1.0')
crawler.crawl.side_effect = race
with patch('db.service._get_imdb_crawler', return_value=crawler):
    service.crawl_imdb(stale_message)
assert service.movieRepository.find_one(movie.id).title == 'Newer correction'
assert service.movieRepository.find_one(movie.id).score == '7.0'
db.conn.close()
''')


if __name__ == '__main__':
    unittest.main()
