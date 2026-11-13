"""Release metadata and identity shared by crawling, enrichment and deduplication."""

import re
from utils.titles import title_key

EPISODE = re.compile(r'\bS(?P<s>\d{1,2})[ ._-]*E(?P<e>\d{1,3})(?:\s*[-–]\s*(?:S(?P<end_s>\d{1,2}))?E?(?P<end>\d{1,3}))?\b', re.I)
SEASONS = re.compile(r'\b(?:S(?P<s>\d{1,2})(?:\s*[-–]\s*S?(?P<end>\d{1,2}))?|Seasons?\s+(?P<word>\d{1,2})(?:\s*(?:to|[-–])\s*(?P<word_end>\d{1,2}))?)\b', re.I)
CROSS_EPISODE = re.compile(r'\b(?P<s>\d{1,2})x(?P<e>\d{2})(?:-(?P<end>\d{2}))?\b', re.I)
YEAR = re.compile(r'\b(?:19|20)\d{2}\b')
EVENT = re.compile(r'^(?:Formula[ .]1|WWE\b|UFC\b|TNA\b|AEW\b|One Championship\b)', re.I)
COMPLETE = re.compile(r'\bcomplete[ ._-]+series\b|\bfull[ ._-]+series\b', re.I)
FIELDS = ('media_type', 'release_scope', 'season', 'season_end', 'episode', 'episode_end')


def release_metadata(filename, category=None):
    filename = filename or ''
    result = dict(media_type='unknown', release_scope='unknown', season=None,
                  season_end=None, episode=None, episode_end=None)
    episodes = list(EPISODE.finditer(filename)) or list(CROSS_EPISODE.finditer(filename))
    if episodes:
        result['media_type'] = 'tv'
        if len(episodes) > 1:
            result['release_scope'] = 'mixed'
            return result
        match = episodes[0]
        season, episode = int(match['s']), int(match['e'])
        end = int(match['end']) if match['end'] else episode
        if end < episode or (match.re is EPISODE and match['end_s'] and int(match['end_s']) != season):
            result['release_scope'] = 'mixed'
            return result
        result.update(release_scope='episode', season=season, season_end=season,
                      episode=episode, episode_end=end)
        return result
    match = SEASONS.search(filename)
    if match:
        season = int(match['s'] or match['word'])
        end = int(match['end'] or match['word_end'] or season)
        result.update(media_type='tv', release_scope='series' if COMPLETE.search(filename) else 'season',
                      season=season, season_end=end)
        if end < season:
            result.update(release_scope='mixed', season=None, season_end=None)
        return result
    if COMPLETE.search(filename):
        result.update(media_type='tv', release_scope='series')
    elif category and re.search(r'\btv\b|television|series', category, re.I):
        result['media_type'] = 'tv'
    elif category and re.search(r'movies?|film', category, re.I):
        result.update(media_type='movie', release_scope='movie')
    elif YEAR.search(filename) and not EVENT.search(filename):
        result.update(media_type='movie', release_scope='movie')
    return result


def value(row, key):
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def effective_metadata(row):
    inferred = release_metadata(value(row, 'filename'))
    kind = value(row, 'media_type')
    if kind in ('movie', 'tv'):
        inferred['media_type'] = kind
        if kind == 'movie':
            inferred['release_scope'] = 'movie'
    for key in FIELDS[1:]:
        field = value(row, key)
        if field is not None and field != 'unknown':
            inferred[key] = field
    return inferred


def normalized_title(title):
    return title_key(title)


def series_year(filename):
    """Only a year before the season code is a series-start hint, not an airdate."""
    filename = filename or ''
    match = EPISODE.search(filename) or SEASONS.search(filename) or CROSS_EPISODE.search(filename) or COMPLETE.search(filename)
    if not match or EVENT.search(filename):
        return None
    years = YEAR.findall(filename[:match.start()])
    return years[-1] if years else None


def release_identity(row):
    meta = effective_metadata(row)
    title = normalized_title(value(row, 'title_accurate') or value(row, 'title'))
    if not title:
        return None
    if meta['media_type'] == 'movie':
        return ('movie', title, value(row, 'year'))
    if meta['media_type'] == 'tv' and meta['release_scope'] in ('episode', 'season', 'series'):
        return ('tv', title, series_year(value(row, 'filename')), *(meta[key] for key in FIELDS[1:]))
    # Unspecified TV releases, mixed packs and unknown media cannot be safely
    # collapsed by title. URL uniqueness remains the first line of defense.
    filename = normalized_title(value(row, 'filename'))
    return ('unclassified', title, filename) if filename else None


def release_label(row):
    meta = effective_metadata(row)
    if meta['media_type'] != 'tv':
        return ''
    if meta['release_scope'] == 'mixed':
        return 'Mixed release'
    season, end = meta['season'], meta['season_end']
    if season is None:
        return 'Complete series' if meta['release_scope'] == 'series' else 'Unspecified release'
    label = f'S{season:02d}'
    if end is not None and end != season:
        label += f'–S{end:02d}'
    if meta['episode'] is not None:
        label += f'E{meta["episode"]:02d}'
        if meta['episode_end'] is not None and meta['episode_end'] != meta['episode']:
            label += f'–E{meta["episode_end"]:02d}'
    elif meta['release_scope'] == 'series':
        label += ' · Complete series'
    else:
        label += ' · Season pack'
    return label


def strip_episode_tags(title):
    match = EPISODE.search(title or '') or SEASONS.search(title or '') or CROSS_EPISODE.search(title or '')
    return (title[:match.start()].strip(' ._-') if match and match.start() else title)
