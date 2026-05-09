from bs4 import BeautifulSoup
import argparse
from browserdriver.driver import DriverFactory
import logging
from db_model import Movie
from typing import List

logger = logging.getLogger(__name__)


class RargbCrawler:
    def crawl(self, param: dict) -> List:
        url = f"https://rargb.to/search/{param['page']}/?search={param['keyword']}&category[]=movies"

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
            assert a is not None
            movies.append(movie)
            logger.info(f"[v] Crawled: {movie}\n")

        return movies


class ImdbCrawler:
    def crawl(self, keyword: str, items: List):
        if not items or len(items) < 1:
            return []

        driver = DriverFactory().create_driver()
        updated_movies = []
        for item in items:
            title = item.title_accurate if item.title_accurate else item.title
            if not title:
                logger.error(f"❓item: {item} has no title yet.")
                continue

            url = f"https://m.imdb.com/find/?q={title}&ref_=chttvtp_nv_srb_sm"

            try:
                html = driver.fetch(url)
                if not html:
                    logger.error("error in fetching.")
                    continue

                soup = BeautifulSoup(html, "html.parser")
                if not soup:
                    logger.error(f"error in parsing.\n{html}")
                    continue

                ul = soup.find("ul", {"class": "ipc-metadata-list--base"})
                if not ul:
                    logger.error(f"❌ Could not find result table:\n{soup}")
                    continue

                lis = ul.find_all("li", {"class": "ipc-metadata-list-summary-item"})

                if not lis or len(lis) == 0:
                    logger.error(f"❌ Could not find li:{ul}")
                    continue

                for li in lis:
                    li_img = li.find("img", {"class": "ipc-image"})
                    # assert li_img is not None
                    poster = li_img["src"]
                    # assert poster is str
                    li_title = li.find("h3", {"class": "ipc-title__text"})
                    # assert li_title is not None
                    title = li_title.string
                    li_score = li.find("span", {"class": "ipc-rating-star--rating"})
                    # assert li_score is not None
                    score = li_score.string
                    li_year = li.find("li", {"class": "ipc-inline-list__item"})
                    # assert li_year is not None
                    year = li_year.string
                    if year != keyword:
                        continue

                    updated_movies.append(
                        Movie(
                            id=item.id,
                            poster=poster,
                            title=title,
                            score=score,
                        )
                    )
                    break

            except Exception as e:
                logger.error(f"❌ Error processing item {item}: {e}")
                continue

        return updated_movies


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl RARBG for movies or TV shows.")
    parser.add_argument("--type", choices=["movies", "tvshows"], default="movies")
    parser.add_argument("--page", type=int, default=1)
    args = parser.parse_args()
