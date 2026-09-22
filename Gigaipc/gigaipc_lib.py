"""
Shared scraping/parsing helpers for the gigaipc.com product catalog automation.

Used by scrape_products.py (build the full catalog CSV) and
validate_products.py (audit an existing catalog CSV against the live site).
"""

import re
import time
import html as html_module
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://www.gigaipc.com"
PRODUCTS_LIST_URL = f"{BASE_URL}/en/products"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 gigaipc-catalog-bot"
)

CSV_FIELDS = ["model_name", "category", "url", "specifications", "features_summary", "image_url"]


def make_session():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch(session, url, timeout=20):
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _tag_lines(tag):
    """Render a tag's inner HTML, treating <br> as a line break, and return
    the decoded, stripped, non-empty text lines."""
    if tag is None:
        return []
    raw = str(tag)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    text = BeautifulSoup(raw, "lxml").get_text()
    lines = [html_module.unescape(line).strip() for line in text.split("\n")]
    return [line for line in lines if line]


def list_product_urls(session, delay=0.4, max_pages=None, log=None):
    """Crawl the paginated /en/products listing and return a sorted list of
    unique product-detail URLs (canonical form, no trailing slash)."""
    urls = set()
    page = 1
    last_page = 1
    while True:
        page_url = f"{PRODUCTS_LIST_URL}?page={page}"
        html = fetch(session, page_url)
        soup = BeautifulSoup(html, "lxml")

        items = soup.select(".relevantItem a.link[href]")
        if not items and page > 1:
            break

        for a in items:
            href = a.get("href", "").strip()
            if "/products-detail/" in href:
                urls.add(href.rstrip("/"))

        if page == 1:
            last_btn = soup.select_one("a.lastBtn[data-page]")
            if last_btn and last_btn.get("data-page", "").isdigit():
                last_page = int(last_btn["data-page"])

        if log:
            log(f"  listing page {page}/{last_page}: {len(items)} products ({len(urls)} unique so far)")

        page += 1
        if max_pages and page > max_pages:
            break
        if page > last_page:
            break
        time.sleep(delay)

    return sorted(urls)


def parse_product_detail(html, url):
    """Parse a product-detail page into a dict matching CSV_FIELDS."""
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.select_one("h1.articleTitle")
    model_name = title_tag.get_text(strip=True) if title_tag else ""

    category = ""
    breadcrumb_links = soup.select("div.bread a[href]")
    for a in breadcrumb_links:
        href = a["href"].rstrip("/")
        if href.endswith("/en/products") or href.endswith("/en/products/"):
            continue
        if "/en/products/" in href or href.endswith("/en/products"):
            category = a.get_text(strip=True)
            break

    specifications = {}
    for item in soup.select("div.specTable > div.specItem"):
        key_tag = item.select_one("h3.title")
        value_tag = item.select_one("p.text")
        if not key_tag:
            continue
        key = key_tag.get_text(strip=True)
        specifications[key] = _tag_lines(value_tag)

    features_summary = set()
    for p in soup.select(".infoBox .infoInner .textEditor > p"):
        for line in _tag_lines(p):
            line = line.replace("\xa0", "").lstrip("•").strip()
            if line:
                features_summary.add(line)

    image_url = ""
    og_image = soup.select_one('meta[property="og:image"]')
    if og_image and og_image.get("content"):
        image_url = urljoin(BASE_URL, og_image["content"].strip())

    canonical_url = url.rstrip("/")
    copy_link = soup.select_one("#copyLink")
    if copy_link and copy_link.get("value"):
        canonical_url = copy_link["value"].strip().rstrip("/")

    return {
        "model_name": model_name,
        "category": category,
        "url": canonical_url,
        "specifications": specifications,
        "features_summary": features_summary,
        "image_url": image_url,
    }


def detail_url_from_slug(url_or_slug):
    if url_or_slug.startswith("http"):
        return url_or_slug.rstrip("/") + "/"
    return f"{BASE_URL}/en/products-detail/{url_or_slug.strip('/')}/"
