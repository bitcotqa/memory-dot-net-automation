"""
Shared scraping/parsing helpers for the gigaipc.com product catalog automation.

Used by scrape_products.py (build the full catalog CSV) and
validate_products.py (audit an existing catalog CSV against the live site).
"""

import re                                  # regex, used to turn <br> tags into newlines before text extraction
import time                                # time.sleep() for the polite delay between requests
import html as html_module                 # decodes HTML entities like &reg; / &amp; back into normal characters
from urllib.parse import urljoin           # resolves a possibly-relative image URL against the site's base URL

import requests                            # HTTP client used to fetch pages
from bs4 import BeautifulSoup              # HTML parser used to query elements with CSS selectors
from requests.adapters import HTTPAdapter  # lets us attach a retry policy to requests.Session
from urllib3.util.retry import Retry       # the retry policy itself (how many retries, which errors, backoff)

BASE_URL = "https://www.gigaipc.com"                   # site root, used to build every other URL
PRODUCTS_LIST_URL = f"{BASE_URL}/en/products"           # paginated catalog listing page
USER_AGENT = (                                          # identifies this bot to the server instead of using
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "   # the default python-requests UA, which
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 gigaipc-catalog-bot"  # some sites block by default
)

# Column order every output CSV in this project uses, shared by both scripts so their files line up.
CSV_FIELDS = ["model_name", "category", "url", "specifications", "features_summary", "image_url"]


def make_session():
    session = requests.Session()                       # reuse one TCP connection pool across all requests
    session.headers.update({"User-Agent": USER_AGENT})  # send our identifying UA on every request from this session
    retry = Retry(
        total=4,                                        # retry a failed request up to 4 times before giving up
        backoff_factor=1.5,                              # wait 1.5s, 3s, 6s, 12s... longer after each retry
        status_forcelist=[429, 500, 502, 503, 504],      # only retry on "try again later" style HTTP errors
        allowed_methods=["GET"],                         # we only ever GET, so only retry GET requests
    )
    adapter = HTTPAdapter(max_retries=retry)             # wraps the retry policy into something requests can use
    session.mount("https://", adapter)                   # apply the retry policy to all https:// requests
    session.mount("http://", adapter)                    # ...and to http:// requests too, just in case
    return session


def fetch(session, url, timeout=20):
    resp = session.get(url, timeout=timeout)   # issue the GET request, give up after `timeout` seconds
    resp.raise_for_status()                    # raise an exception if the server returned an error status
    return resp.text                           # hand back the raw HTML for the caller to parse


def _tag_lines(tag):
    """Render a tag's inner HTML, treating <br> as a line break, and return
    the decoded, stripped, non-empty text lines."""
    if tag is None:                                          # nothing to parse if the caller found no element
        return []
    raw = str(tag)                                           # get this tag's HTML, including its child tags
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)        # turn every <br>, <br/>, <br /> into a real newline
    text = BeautifulSoup(raw, "lxml").get_text()             # re-parse and strip all remaining tags, keep text
    lines = [html_module.unescape(line).strip() for line in text.split("\n")]  # decode entities, trim whitespace
    return [line for line in lines if line]                  # drop any lines that ended up empty


def list_product_urls(session, delay=0.4, max_pages=None, log=None):
    """Crawl the paginated /en/products listing and return a sorted list of
    unique product-detail URLs (canonical form, no trailing slash)."""
    urls = set()          # collects every product URL found so far; a set avoids duplicates across pages
    page = 1               # the listing page we're currently requesting
    last_page = 1           # updated from the pagination widget once we see page 1; loop stops once we pass it
    while True:
        page_url = f"{PRODUCTS_LIST_URL}?page={page}"   # e.g. .../en/products?page=3
        html = fetch(session, page_url)                  # download that listing page
        soup = BeautifulSoup(html, "lxml")               # parse it so we can query elements

        items = soup.select(".relevantItem a.link[href]")   # each product card's link on this listing page
        if not items and page > 1:                          # an empty page past page 1 means we've run past the end
            break

        for a in items:
            href = a.get("href", "").strip()             # the product-detail URL from this card
            if "/products-detail/" in href:               # sanity check: only keep genuine product-detail links
                urls.add(href.rstrip("/"))                 # normalize by dropping any trailing slash, then store

        if page == 1:                                                  # the total page count only needs reading once
            last_btn = soup.select_one("a.lastBtn[data-page]")          # the "last page" link in the pager
            if last_btn and last_btn.get("data-page", "").isdigit():
                last_page = int(last_btn["data-page"])                  # e.g. 23, so we know when to stop looping

        if log:                                            # optional progress callback (main scripts pass `print`)
            log(f"  listing page {page}/{last_page}: {len(items)} products ({len(urls)} unique so far)")

        page += 1                            # move on to the next listing page
        if max_pages and page > max_pages:    # test/debug guard: stop early if the caller capped the page count
            break
        if page > last_page:                  # normal stop condition: we've now covered every page in the pager
            break
        time.sleep(delay)                     # pause between requests so we don't hammer the server

    return sorted(urls)   # a stable, deterministic order for downstream code and for diffing between runs


def parse_product_detail(html, url):
    """Parse a product-detail page into a dict matching CSV_FIELDS."""
    soup = BeautifulSoup(html, "lxml")   # parse the product-detail page once, query it repeatedly below

    title_tag = soup.select_one("h1.articleTitle")                 # the product's model name is the page's H1
    model_name = title_tag.get_text(strip=True) if title_tag else ""  # empty string if the page has no H1 (unexpected)

    category = ""
    breadcrumb_links = soup.select("div.bread a[href]")   # HOME > Products > <Category> > <Sub-series> > (name)
    for a in breadcrumb_links:
        href = a["href"].rstrip("/")
        if href.endswith("/en/products") or href.endswith("/en/products/"):   # skip the "Products" breadcrumb link itself
            continue
        if "/en/products/" in href or href.endswith("/en/products"):          # the next link after it is the top-level category
            category = a.get_text(strip=True)
            break                                                              # stop at the first match; ignore the deeper sub-series link

    specifications = {}
    for item in soup.select("div.specTable > div.specItem"):   # each row of the "Specification" table on the page
        key_tag = item.select_one("h3.title")                  # e.g. "CPU", "Memory", "Form Factor"
        value_tag = item.select_one("p.text")                  # the spec's value, possibly multiple <br>-separated lines
        if not key_tag:                                        # skip anything that isn't a real spec row
            continue
        key = key_tag.get_text(strip=True)
        specifications[key] = _tag_lines(value_tag)             # store the value as a list of lines, one per <br>

    features_summary = set()
    for p in soup.select(".infoBox .infoInner .textEditor > p"):   # the "Key Features" paragraphs (summary + bullets)
        for line in _tag_lines(p):
            line = line.replace("\xa0", "").lstrip("•").strip()    # drop stray &nbsp; chars and any leading bullet glyph
            if line:                                                # ignore now-empty lines (e.g. a lone "&nbsp;" paragraph)
                features_summary.add(line)                          # a set, since the source CSV format stores these unordered

    image_url = ""
    og_image = soup.select_one('meta[property="og:image"]')    # the product's main photo, as used for social-share previews
    if og_image and og_image.get("content"):
        image_url = urljoin(BASE_URL, og_image["content"].strip())   # urljoin handles it whether it's relative or already absolute

    canonical_url = url.rstrip("/")                       # fall back to the URL we requested, without a trailing slash
    copy_link = soup.select_one("#copyLink")               # the page's own "copy link" input holds its canonical URL
    if copy_link and copy_link.get("value"):
        canonical_url = copy_link["value"].strip().rstrip("/")   # prefer that value when present; it's what the site considers canonical

    return {
        "model_name": model_name,
        "category": category,
        "url": canonical_url,
        "specifications": specifications,        # a dict: {spec name: [value lines]}
        "features_summary": features_summary,    # a set of feature bullet strings
        "image_url": image_url,
    }


def detail_url_from_slug(url_or_slug):
    if url_or_slug.startswith("http"):                     # already a full URL (e.g. loaded from a CSV) -> just normalize it
        return url_or_slug.rstrip("/") + "/"
    return f"{BASE_URL}/en/products-detail/{url_or_slug.strip('/')}/"   # otherwise treat it as a bare model slug and build the URL
