from bs4 import BeautifulSoup
from utils.titles import title_key
import argparse
import re
import time
from urllib.parse import quote_plus
from browserdriver.driver import DriverFactory
from utils.media import release_metadata, effective_metadata, series_year, strip_episode_tags
from browserdriver.errors import BrowserChallengeError, BrowserFetchError, UnexpectedPageError, challenge_reason
import logging
from db_model import Movie
from typing import List

logger = logging.getLogger(__name__)

IMDB_MAX_ATTEMPTS = 3
IMDB_RETRY_DELAY_SECONDS = 2
# A release's filename year and IMDb's year often differ by one (festival vs
# general release), so a candidate one year off is kept as a fallback.
IMDB_YEAR_TOLERANCE = 1

# Release tags the T5 model often leaves in a predicted title
# ("Rambo III.1988.1080p", "Tower Block (2012) WEBRip"). IMDb's search finds
# nothing for such a string, so the row ends up "unmatched" for good.
_RELEASE_TAG_RE = re.compile(
    r"\b(2160p|1080p|720p|480p|4K|UHD|BluRay|BDRip|BRRip|BDRemux|REMUX|WEBRip|WEB-DL"
    r"|HDTV|HDRip|DVDRip|Upscaled|x264|x265|HEVC|H264)\b",
    re.IGNORECASE,
)


def _year_near(imdb_year: str | None, expected: str) -> bool:
    return bool(
        imdb_year
        and imdb_year.isdigit()
        and expected.isdigit()
        and abs(int(imdb_year) - int(expected)) <= IMDB_YEAR_TOLERANCE
    )


def clean_search_title(title: str, year: str | None = None) -> str:
    """Reduce a predicted title to what IMDb's search can match.

    Cuts at the release year and at the first release tag, then turns
    dot/underscore separators into spaces. A cut that would leave nothing
    (e.g. "1917", "2001 A Space Odyssey" with year 2001) is skipped.
    """
    s = title.strip()
    cuts = [m.start() for m in [_RELEASE_TAG_RE.search(s)] if m]
    if year:
        m = re.search(rf"\b{re.escape(year)}\b", s)
        if m:
            cuts.append(m.start())
    for cut in sorted(cuts):
        head = s[:cut].strip(" .-_([{")
        if head:
            s = head
            break
    if " " not in s:
        s = re.sub(r"[._]+", " ", s)
    return s.strip(" .-_([{") or title.strip()


class RargbCrawler:
    def __init__(self):
        self._driver = DriverFactory().create_driver()

    def close(self):
        self._driver.close()

    @staticmethod
    def page_url(page: int) -> str:
        return f"https://rargb.to/torrents/{page}/?category[]=movies&category[]=tv"

    def crawl(self, param: dict) -> List:
        url = self.page_url(param["page"])

        html = self._driver.fetch(url)

        soup = BeautifulSoup(html, "html.parser")
        # BS4 trees are full of parent/sibling reference cycles, reclaimable
        # only by the cyclic GC; decompose on every exit path so each page is
        # freed by refcounting instead of piling up across a long crawl.
        try:
            table = soup.find("table", {"class": "lista2t"})

            if not table:
                reason = challenge_reason(html)
                if reason:
                    raise BrowserChallengeError(f'{reason} blocked the crawl.')
                raise UnexpectedPageError('The upstream page has no catalog table; its layout may have changed.')

            movies = []
            rows = table.find_all("tr")[1:]

            for r in rows:
                cols = r.find_all("td")

                a = cols[1].find("a")
                assert a is not None
                # Column layout (table.lista2t): Cat., File, Category, Added,
                # Size, S. (seeders), L. (leechers), Uploader.
                seed_text = cols[5].text.strip() if len(cols) > 5 else ""
                leech_text = cols[6].text.strip() if len(cols) > 6 else ""
                movie = Movie(
                    filename=a.text.strip(),
                    url=f"https://rargb.to{a['href']}",
                    size=cols[4].text.strip(),
                    added=cols[3].text.strip(),
                    seed=int(seed_text) if seed_text.isdigit() else None,
                    leech=int(leech_text) if leech_text.isdigit() else None,
                    **release_metadata(a.text.strip(), cols[2].get_text(" ", strip=True)),
                )
                logger.debug(f"[v] Found item: {movie.filename}")
                movies.append(movie)

            return movies
        finally:
            soup.decompose()


class ImdbCrawler:
    def __init__(self):
        self._driver = DriverFactory().create_driver()

    def close(self):
        self._driver.close()

    def crawl(self, item: Movie, exact=False) -> Movie | None:
        """Search IMDb for ``item`` and return a Movie with poster/title/score.

        Transient failures (network hiccups, WAF/browser issues) are retried
        up to IMDB_MAX_ATTEMPTS times. Once retries are exhausted, or once a
        search genuinely completes with no year-matching candidate, this
        returns a terminal ``score="unmatched"`` Movie instead of None — a
        bare None looks identical to "never attempted" in the DB (both leave
        score/poster null forever), which silently grows an unrecoverable
        backlog. Marking it "unmatched" lets Workflow.SCORING's query exclude
        it going forward instead of retrying a search that will never match.
        """
        if not item:
            return None

        media = effective_metadata(item)['media_type']
        expected_year = series_year(item.filename) if media == 'tv' else item.year
        title = item.title_accurate if item.title_accurate else item.title
        if not title:
            logger.info(f"[x] item: {item} has no title yet.")
            return None

        # Only a model-predicted title needs cleaning; a user-corrected one is searched as typed.
        query = title if item.title_accurate else clean_search_title(title, expected_year)
        if media == 'tv':
            query = strip_episode_tags(query)
        url = f"https://m.imdb.com/find/?q={quote_plus(query)}&ref_=chttvtp_nv_srb_sm"

        if exact:
            query = title_key(query)
            url = f"https://www.imdb.com/find/?q={quote_plus(query)}&s=tt&exact=true&ref_=fn_ttl_ex"

        for attempt in range(1, IMDB_MAX_ATTEMPTS + 1):
            try:
                html = self._driver.fetch(url)
                if not html:
                    logger.info(
                        f"[x] Haven't found html in fetching (attempt {attempt}/{IMDB_MAX_ATTEMPTS})."
                    )
                    raise ValueError("empty html")

                soup = BeautifulSoup(html, "html.parser")
                # Same cycle-severing rule as RargbCrawler.crawl(): decompose
                # on every exit path (return, break, raise) so a fast loop
                # over a large crawl_imdb backlog can't balloon RSS.
                try:
                    ul = soup.find("ul", {"class": "ipc-metadata-list--base"})
                    if not ul:
                        logger.info(
                            f"[x] Could not find result list (attempt {attempt}/{IMDB_MAX_ATTEMPTS})."
                        )
                        raise UnexpectedPageError("IMDb search result list could not be parsed.")

                    lis = ul.find_all("li", {"class": "ipc-metadata-list-summary-item"})
                    if not lis or len(lis) == 0:
                        logger.info(f"[x] IMDb returned zero results for '{title}'.")
                        break  # genuine empty result set — not worth retrying

                    verified = []
                    near = None  # first candidate within IMDB_YEAR_TOLERANCE, used if no exact year
                    for li in lis:
                        description = li.get_text(' ', strip=True)
                        is_series = re.search(r'\bTV\s+(?:Mini[ -]?)?Series\b|\bMini[ -]Series\b', description, re.I)
                        is_episode = re.search(r'\bTV\s+Episode\b', description, re.I)
                        if media == 'tv' and (not is_series or is_episode):
                            continue
                        if media == 'movie' and (is_series or is_episode):
                            continue
                        li_img = li.find("img", {"class": "ipc-image"})
                        poster = li_img["src"] if li_img else None
                        li_title = li.find(class_="ipc-title__text")
                        found_title = (
                            li_title.get_text(' ', strip=True)
                            if li_title
                            else None
                        )
                        if not found_title:
                            raise UnexpectedPageError('IMDb result title could not be parsed.')
                        if exact and title_key(found_title) != query:
                            continue
                        li_score = li.find("span", {"class": "ipc-rating-star--rating"})
                        score = (
                            str(li_score.string)
                            if li_score and li_score.string
                            else None
                        )
                        li_year = li.find("li", {"class": "ipc-inline-list__item"})
                        year = (
                            str(li_year.string) if li_year and li_year.string else None
                        )
                        if media == 'tv' and year:
                            year_match = re.search(r'(?:19|20)\d{2}', year)
                            year = year_match.group(0) if year_match else None
                        if expected_year and year != expected_year:
                            logger.info(f"[x] Year mismatch: IMDb={year}, expected={item.year}, trying next result.")
                            if not exact and near is None and _year_near(year, expected_year):
                                near = Movie(id=item.id, poster=poster, title=found_title, score=score)
                            continue

                        if exact:
                            verified.append((poster, found_title, score, li_title))
                            continue

                        title_a = li_title.find_parent("a") if li_title else None
                        summary = (
                            self._fetch_summary(title_a["href"])
                            if title_a and title_a.get("href")
                            else None
                        )

                        return Movie(
                            id=item.id,
                            poster=poster,
                            title=found_title,
                            score=score,
                            summary=summary,
                        )

                    if exact and len(verified) == 1:
                        poster, found_title, score, heading = verified[0]
                        link = heading.find_parent('a')
                        summary = self._fetch_summary(link['href']) if link and link.get('href') else None
                        return Movie(id=item.id, poster=poster, title=found_title, score=score, summary=summary)
                    if near:
                        return near

                    logger.info(f"[x] No year-matching IMDb result for '{title}' (expected {item.year}).")
                    break  # scanned every candidate — genuine no-match, not worth retrying
                finally:
                    soup.decompose()

            except BrowserFetchError:
                # A blocked/unavailable provider is not a permanent title mismatch.
                raise
            except Exception as e:
                logger.error(
                    f" Error processing item {item} (attempt {attempt}/{IMDB_MAX_ATTEMPTS}): {e}"
                )
                if attempt < IMDB_MAX_ATTEMPTS:
                    time.sleep(IMDB_RETRY_DELAY_SECONDS)
                else:
                    raise UnexpectedPageError('IMDb search page could not be parsed; please retry.') from None

        return Movie(id=item.id, score="unmatched")

    def _fetch_summary(self, title_href: str) -> str | None:
        """Best-effort fetch of the plot synopsis from a matched title's IMDb page.

        A missing/failed summary must not fail the whole match — poster/title/
        score were already found, so any error here is logged and swallowed.
        """
        try:
            url = f"https://m.imdb.com{title_href}"
            html = self._driver.fetch(url)
            if not html:
                return None
            soup = BeautifulSoup(html, "html.parser")
            try:
                plot = soup.find(attrs={"data-testid": "plot-xl"})
                if not plot:
                    return None
                return str(plot.string) if plot.string else plot.get_text(strip=True)
            finally:
                soup.decompose()
        except Exception as e:
            logger.info(f"[x] Could not fetch plot summary from {title_href}: {e}")
            return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl RARBG for movies or TV shows.")
    parser.add_argument("--type", choices=["movies", "tvshows"], default="movies")
    parser.add_argument("--page", type=int, default=1)
    args = parser.parse_args()
