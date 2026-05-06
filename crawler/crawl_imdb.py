from bs4 import BeautifulSoup
from db.db import db
from workflow import Workflow
from browserdriver.driver import DriverFactory
import logging


logger = logging.getLogger(__name__)


def crawl_imdb(keyword):
    items = db.get_items(workflow=Workflow.SCORING)
    logger.info(f"Found {len(items)} items to update from IMDb.")

    driver = DriverFactory().create_driver()
    for item in items:
        title = item["title_accurate"] if item["title_accurate"] else item["title"]
        url = f"https://m.imdb.com/find/?q={title}&ref_=chttvtp_nv_srb_sm"
        html = driver.fetch(url)

        soup = BeautifulSoup(html, "html.parser")
        ul = soup.find("ul", {"class": "ipc-metadata-list--base"})

        if not ul:
            logger.info(
                "❌ Could not find result table. Cloudflare may need more delay."
            )
            logger.info(html[:500])
            update_item = {
                "id": item["id"],
                "score": "unmatched",
            }
            db.update_item(update_item)
            continue

        try:
            lis = ul.find_all("li", {"class": "ipc-metadata-list-summary-item"})

            if lis is None or len(lis) == 0:
                logger.debug(
                    "❌ Could not find result table. Cloudflare may need more delay."
                )
                update_item = {
                    "id": item["id"],
                    "score": "unmatched",
                }
                db.update_item(update_item)
                continue

            for li in lis:
                poster = li.find("img", {"class": "ipc-image"})["src"]
                title = li.find("h3", {"class": "ipc-title__text"}).string
                score = li.find("span", {"class": "ipc-rating-star--rating"}).string
                year = li.find("li", {"class": "ipc-inline-list__item"}).string
                logger.debug(
                    f"Extracted data - Title: {title}, Year: {year}, Score: {score}"
                )
                if year != keyword:
                    continue

                update_item = {
                    "id": item["id"],
                    "poster": poster,
                    "score": score,
                    "title": title,
                }
                db.update_item(update_item)
                break

        except Exception as e:
            logger.error(f"❌ Error processing item {item['title']}: {e}")
            update_item = {
                "id": item["id"],
                "score": "unmatched",
            }
            db.update_item(update_item)
            continue

    return True


if __name__ == "__main__":
    results = crawl_imdb("2026")
