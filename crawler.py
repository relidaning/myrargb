from bs4 import BeautifulSoup
import argparse
import re
import time
from urllib.parse import quote_plus
from browserdriver.driver import DriverFactory
import logging
from db_model import Movie
from typing import List

logger = logging.getLogger(__name__)

IMDB_MAX_ATTEMPTS = 3
IMDB_RETRY_DELAY_SECONDS = 2

# Release tags the T5 model often leaves in a predicted title
# ("Rambo III.1988.1080p", "Tower Block (2012) WEBRip"). IMDb's search finds
# nothing for such a string, so the row ends up "unmatched" for good.
_RELEASE_TAG_RE = re.compile(
    r"\b(2160p|1080p|720p|480p|4K|UHD|BluRay|BDRip|BRRip|BDRemux|REMUX|WEBRip|WEB-DL"
    r"|HDTV|HDRip|DVDRip|Upscaled|x264|x265|HEVC|H264)\b",
    re.IGNORECASE,
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

    def crawl(self, param: dict) -> List:
        page = param["page"]
        if page == 1:
            url = "https://rargb.to/movies/"
        else:
            url = f"https://rargb.to/movies/{page}/"

        html = self._driver.fetch(url)

        soup = BeautifulSoup(html, "html.parser")
        # BS4 trees are full of parent/sibling reference cycles, reclaimable
        # only by the cyclic GC; decompose on every exit path so each page is
        # freed by refcounting instead of piling up across a long crawl.
        try:
            table = soup.find("table", {"class": "lista2t"})

            if not table:
                logger.error(
                    "❌ Could not find result table. Cloudflare may need more delay.\n {html}"
                )
                return []

            movies = []
            rows = table.find_all("tr")[1:]

            for r in rows:
                cols = r.find_all("td")

                a = cols[1].find("a")
                assert a is not None
                movie = Movie(
                    filename=a.text.strip(),
                    url=f"https://rargb.to{a['href']}",
                    size=cols[4].text.strip(),
                    added=cols[3].text.strip(),
                )
                logger.debug(f"[v] Found item: {movie.filename}")
                movies.append(movie)

            return movies
        finally:
            soup.decompose()


class ImdbCrawler:
    def __init__(self):
        self._driver = DriverFactory().create_driver()

    def crawl(self, item: Movie) -> Movie | None:
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

        title = item.title_accurate if item.title_accurate else item.title
        if not title:
            logger.info(f"[x] item: {item} has no title yet.")
            return None

        # Only a model-predicted title needs cleaning; a user-corrected one is searched as typed.
        query = title if item.title_accurate else clean_search_title(title, item.year)
        url = f"https://m.imdb.com/find/?q={quote_plus(query)}&ref_=chttvtp_nv_srb_sm"

        for attempt in range(1, IMDB_MAX_ATTEMPTS + 1):
            try:
                html = self._driver.fetch(url)
                if not html:
                    logger.info(f"[x] Haven't found html in fetching (attempt {attempt}/{IMDB_MAX_ATTEMPTS}).")
                    raise ValueError("empty html")

                soup = BeautifulSoup(html, "html.parser")
                # Same cycle-severing rule as RargbCrawler.crawl(): decompose
                # on every exit path (return, break, raise) so a fast loop
                # over a large crawl_imdb backlog can't balloon RSS.
                try:
                    ul = soup.find("ul", {"class": "ipc-metadata-list--base"})
                    if not ul:
                        logger.info(f"[x] Could not find result list (attempt {attempt}/{IMDB_MAX_ATTEMPTS}).")
                        raise ValueError("result list not found")

                    lis = ul.find_all("li", {"class": "ipc-metadata-list-summary-item"})
                    if not lis or len(lis) == 0:
                        logger.info(f"[x] IMDb returned zero results for '{title}'.")
                        break  # genuine empty result set — not worth retrying

                    for li in lis:
                        li_img = li.find("img", {"class": "ipc-image"})
                        poster = li_img["src"] if li_img else None
                        li_title = li.find("h3", {"class": "ipc-title__text"})
                        found_title = str(li_title.string) if li_title and li_title.string else None
                        li_score = li.find("span", {"class": "ipc-rating-star--rating"})
                        score = str(li_score.string) if li_score and li_score.string else None
                        li_year = li.find("li", {"class": "ipc-inline-list__item"})
                        year = str(li_year.string) if li_year and li_year.string else None
                        if item.year and year != item.year:
                            logger.info(f"[x] Year mismatch: IMDb={year}, expected={item.year}, trying next result.")
                            continue

                        return Movie(
                            id=item.id,
                            poster=poster,
                            title=found_title,
                            score=score,
                        )

                    logger.info(f"[x] No year-matching IMDb result for '{title}' (expected {item.year}).")
                    break  # scanned every candidate — genuine no-match, not worth retrying
                finally:
                    soup.decompose()

            except Exception as e:
                logger.error(f" Error processing item {item} (attempt {attempt}/{IMDB_MAX_ATTEMPTS}): {e}")
                if attempt < IMDB_MAX_ATTEMPTS:
                    time.sleep(IMDB_RETRY_DELAY_SECONDS)

        return Movie(id=item.id, score="unmatched")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl RARBG for movies or TV shows.")
    parser.add_argument("--type", choices=["movies", "tvshows"], default="movies")
    parser.add_argument("--page", type=int, default=1)
    args = parser.parse_args()
