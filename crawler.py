from bs4 import BeautifulSoup
import argparse
from browserdriver.driver import DriverFactory
import logging
from db_model import Movie
from typing import List

logger = logging.getLogger(__name__)


class RargbCrawler:
    def crawl(self, param: dict) -> List:
        page = param["page"]
        if page == 1:
            url = "https://rargb.to/movies/"
        else:
            url = f"https://rargb.to/movies/{page}/"

        driver = DriverFactory().create_driver()
        html = driver.fetch(url)

        soup = BeautifulSoup(html, "html.parser")
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
            logger.info(f"[v] Found a item: {movie}")
            assert a is not None
            movies.append(movie)

        return movies


class ImdbCrawler:
    def crawl(self, item: Movie) -> Movie | None:
        if not item:
            return None

        driver = DriverFactory().create_driver()
        title = item.title_accurate if item.title_accurate else item.title
        if not title:
            logger.info(f"[x] item: {item} has no title yet.")
            return None

        url = f"https://m.imdb.com/find/?q={title}&ref_=chttvtp_nv_srb_sm"
        try:
            html = driver.fetch(url)
            if not html:
                logger.info("[x] Havn't found html in fetching.")
                return None

            soup = BeautifulSoup(html, "html.parser")
            if not soup:
                logger.info(f"[x] error in parsing:\n{html}")
                return None

            ul = soup.find("ul", {"class": "ipc-metadata-list--base"})
            if not ul:
                logger.info(f"[x] Could not find result table:\n{soup}")
                return None

            lis = ul.find_all("li", {"class": "ipc-metadata-list-summary-item"})
            if not lis or len(lis) == 0:
                logger.info(f"[x] Could not find li:{ul}")
                return None

            for li in lis:
                li_img = li.find("img", {"class": "ipc-image"})
                poster = li_img["src"]
                li_title = li.find("h3", {"class": "ipc-title__text"})
                title = li_title.string
                li_score = li.find("span", {"class": "ipc-rating-star--rating"})
                score = li_score.string
                li_year = li.find("li", {"class": "ipc-inline-list__item"})
                year = li_year.string
                if item.year and year != item.year:
                    logger.info(f"[x] Year mismatch: IMDb={year}, expected={item.year}")
                    return None

                return Movie(
                    id=item.id,
                    poster=poster,
                    title=title,
                    score=score,
                )

        except Exception as e:
            logger.error(f" Error processing item {item}: {e}")
            return None

        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl RARBG for movies or TV shows.")
    parser.add_argument("--type", choices=["movies", "tvshows"], default="movies")
    parser.add_argument("--page", type=int, default=1)
    args = parser.parse_args()
