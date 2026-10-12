"""SSD / storage product-page extraction.

This is deliberately a *separate* extraction workflow from
memory_extractor.py — SSD product pages carry a completely different (and
much richer) data model than a memory compatibility card, discovered by
inspecting real pages (KC600, A400) rather than assumed:

1. Every Kingston branded-SSD product page embeds a small piece of JS —
   ``KCMS.AddToCart.initialize("#...", {"Filters": [...]})`` — whose JSON
   argument lists every Capacity option and every Form Factor option, each
   carrying its own ``PartNumbers`` array. The valid (capacity, form
   factor) combinations Kingston actually sells are exactly the ones where
   a capacity's PartNumbers and a form factor's PartNumbers share exactly
   one part number in common — e.g. KC600's 2048GB capacity only lists
   "SKC600/2048G", which never appears in mSATA's PartNumbers list, so
   2048GB+mSATA is correctly never generated. No clicking/interaction is
   needed to discover this — it's static, server-rendered JSON.

2. The *complete* specification table for every form factor is likewise
   already server-rendered, as one ``<table>`` per form-factor tab (or a
   single table with no tabs, for single-form-factor products like A400).
   Some rows carry one value that applies to every capacity of that form
   factor (e.g. Interface, Controller); others carry a *capacity-scoped*
   value, written as one "<capacity> — <value>" line per capacity
   (separated by ``<br>``), sometimes as a capacity range
   ("512GB–2048GB — ..."). Both must be preserved distinctly per capacity
   — see _parse_spec_table / _resolve_for_capacity.

Net effect: a full, accurate capacity×form-factor×part-number matrix with
correctly-scoped specifications can be built from one page fetch, with no
browser interaction beyond loading the page — confirmed against two real
product pages with different shapes (KC600: two form factors, tabbed;
A400: one form factor, no tabs) before writing this parser.
"""

import json
import re
from collections import OrderedDict
from urllib.parse import urljoin, urlsplit

from agent.error_handler import ScrapeError, PartStatus
from agent.navigation import open_product_page
from utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://www.kingston.com"

_CATALOG_MARKER = "KCMS.AddToCart.initialize("

_CAP_TOKEN = r"\d+(?:\.\d+)?\s?[GMT]B"
_CAP_LINE_RE = re.compile(
    rf"^\s*(?P<cap1>{_CAP_TOKEN})\s*(?:[-–—]\s*(?P<cap2>{_CAP_TOKEN}))?"
    rf"\s*[-–—]\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _norm_capacity(token: str) -> str:
    """'512 GB' / '512gb' / '512GB' all compare equal, but the caller's own
    canonical spelling (from the page's own "Capacities" row) is always
    what actually gets stored — this is only used for matching."""
    return re.sub(r"\s+", "", (token or "")).upper()


def base_product_url(url: str) -> str:
    """Strip query string/fragment so the same product always caches under
    one canonical key regardless of which capacity/form-factor query
    params happened to be on the link that led here."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def resolve_component_url(href: str) -> str:
    if not href:
        return ""
    return urljoin(BASE_URL, href.strip())


# --- Catalog JSON (capacity/form-factor/part-number matrix) ----------------


def _extract_catalog_json(html: str) -> dict:
    """Pull the JSON object out of KCMS.AddToCart.initialize("#id", {...});
    by brace-matching (not a regex) since the object is large and can
    itself contain nested braces/strings with escaped quotes."""
    idx = html.find(_CATALOG_MARKER)
    if idx == -1:
        return None
    start = html.find("{", idx)
    if start == -1:
        return None

    depth = 0
    in_str = False
    str_char = ""
    escape = False
    end = None
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == str_char:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            str_char = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        return None
    try:
        return json.loads(html[start:end])
    except (ValueError, TypeError) as exc:
        logger.debug("Could not parse SSD catalog JSON: %s", exc)
        return None


def _variant_part_numbers(catalog: dict) -> list:
    """Return [{"capacity": name, "form_factor": name, "part_number": pn}]
    for every combination Kingston's own catalog JSON says is real — the
    intersection of a capacity's PartNumbers and a form factor's
    PartNumbers. A combination whose intersection is empty (unsupported,
    e.g. KC600 2048GB + mSATA) is simply never produced. Products with no
    Form Factor axis at all (single physical form) yield one variant per
    capacity with form_factor="".
    """
    if not catalog or not isinstance(catalog.get("Filters"), list):
        return []

    capacity_filter = None
    form_factor_filter = None
    for f in catalog["Filters"]:
        name = (f.get("Name") or "").strip().lower()
        if name == "capacity":
            capacity_filter = f
        elif name == "form factor":
            form_factor_filter = f

    if capacity_filter is None:
        return []

    capacity_items = capacity_filter.get("Items") or []

    if form_factor_filter is None:
        variants = []
        for cap in capacity_items:
            pns = cap.get("PartNumbers") or []
            if len(pns) == 1:
                variants.append({"capacity": cap.get("Name", ""), "form_factor": "", "part_number": pns[0]})
            elif pns:
                logger.warning(
                    "SSD catalog capacity %r has ambiguous part numbers with no form-factor axis: %s",
                    cap.get("Name"),
                    pns,
                )
        return variants

    variants = []
    for cap in capacity_items:
        cap_pns = set(cap.get("PartNumbers") or [])
        if not cap_pns:
            continue
        for ff in form_factor_filter.get("Items") or []:
            ff_pns = set(ff.get("PartNumbers") or [])
            common = cap_pns & ff_pns
            if not common:
                continue  # unsupported combination — never generated
            if len(common) > 1:
                logger.warning(
                    "SSD catalog capacity %r x form factor %r matched multiple part numbers %s — "
                    "including all rather than guessing",
                    cap.get("Name"),
                    ff.get("Name"),
                    sorted(common),
                )
            for pn in sorted(common):
                variants.append({"capacity": cap.get("Name", ""), "form_factor": ff.get("Name", ""), "part_number": pn})
    return variants


# --- Specification tables ----------------------------------------------------


def _table_rows(table_tag) -> list:
    """[(label, [value_line, ...]), ...] for every 2-column row, in order."""
    rows = []
    for tr in table_tag.select("tr"):
        cells = tr.find_all("td")
        if len(cells) != 2:
            continue
        label_td, value_td = cells[0], cells[1]
        for sup in label_td.find_all("sup"):
            sup.decompose()  # footnote markers ("Capacities [2]"), not part of the label
        label = _clean(label_td.get_text())
        for br in value_td.find_all("br"):
            br.replace_with("\n")
        lines = [_clean(line) for line in value_td.get_text().split("\n")]
        lines = [line for line in lines if line]
        if label and lines:
            rows.append((label, lines))
    return rows


def _parse_spec_table(table_tag) -> tuple:
    """Parse one form-factor's specification table.

    Returns (ordered_capacities, spec_rows) where spec_rows preserves the
    table's own row order and each value is either a plain string (applies
    to every capacity) or an OrderedDict {capacity: value} (capacity-scoped
    — built from lines like "256GB — 150TB" or "512GB–2048GB — ...").
    """
    rows = _table_rows(table_tag)

    ordered_capacities = []
    for label, lines in rows:
        if label.lower().startswith("capacities"):
            ordered_capacities = [c.strip() for c in ", ".join(lines).split(",") if c.strip()]
            break
    norm_capacities = {_norm_capacity(c): c for c in ordered_capacities}

    def _expand_range(cap1: str, cap2: str) -> list:
        n1, n2 = _norm_capacity(cap1), _norm_capacity(cap2)
        if n1 not in norm_capacities or n2 not in norm_capacities:
            return []
        i1 = ordered_capacities.index(norm_capacities[n1])
        i2 = ordered_capacities.index(norm_capacities[n2])
        lo, hi = min(i1, i2), max(i1, i2)
        return ordered_capacities[lo : hi + 1]

    spec_rows = OrderedDict()
    for label, lines in rows:
        # Some rows carry a non-capacity-scoped "preamble" line before the
        # per-capacity ones (A400's "Baseline Performance" row: "Data
        # transfer (ATTO)" followed by one line per capacity) — collect
        # those and prefix them onto every resolved value rather than
        # letting one unparseable line blow up the whole row's
        # decomposition (that would silently lose the capacity-specific
        # A400 read/write numbers, exactly the kind of data loss #11
        # warns against).
        preamble = []
        parsed = OrderedDict()
        for line in lines:
            m = _CAP_LINE_RE.match(line)
            targets = []
            value = None
            if m:
                cap1, cap2, value = m.group("cap1"), m.group("cap2"), m.group("value")
                targets = _expand_range(cap1, cap2) if cap2 else (
                    [norm_capacities[_norm_capacity(cap1)]] if _norm_capacity(cap1) in norm_capacities else []
                )
            if not targets:
                preamble.append(line)
                continue
            full_value = value if not preamble else f"{' | '.join(preamble)} | {value}"
            for cap in targets:
                parsed[cap] = full_value

        if parsed:
            spec_rows[label] = parsed
        else:
            spec_rows[label] = " | ".join(lines)  # never drop data we couldn't decompose

    return ordered_capacities, spec_rows


def _resolve_for_capacity(spec_rows: "OrderedDict", capacity: str) -> "OrderedDict":
    resolved = OrderedDict()
    for label, value in spec_rows.items():
        if isinstance(value, dict):
            if capacity in value:
                resolved[label] = value[capacity]
            # else: this label genuinely has no stated value for this
            # capacity on the page — omitted, never invented.
        else:
            resolved[label] = value
    return resolved


def _parse_all_spec_tables(soup) -> dict:
    """Return {form_factor_label: (ordered_capacities, spec_rows)} across
    every form-factor tab, or a single "" key when the product has no tabs
    (one form factor, e.g. A400)."""
    panel = soup.select_one(".s-productDetails__additional__specificationPanel")
    if panel is None:
        return {}

    tabs = panel.select(".l-tabView__tabs__tab")
    if tabs:
        result = {}
        for tab in tabs:
            label = _clean(tab.get_text())
            panel_id = tab.get("aria-controls") or tab.get("data-tab")
            table_panel = panel.select_one(f"#{panel_id}") if panel_id else None
            table = table_panel.select_one("table") if table_panel else None
            if table is not None and label:
                result[label] = _parse_spec_table(table)
        return result

    table = panel.select_one("table")
    if table is None:
        return {}
    ordered_capacities, spec_rows = _parse_spec_table(table)
    form_factor_label = ""
    for label, value in spec_rows.items():
        if label.lower().startswith("form factor") and isinstance(value, str):
            form_factor_label = value
            break
    return {form_factor_label: (ordered_capacities, spec_rows)}


# --- Datasheet / product name -----------------------------------------------


def _datasheet_url(soup) -> str:
    # Tried in priority order (not as one combined selector) — a
    # placeholder `a.downLoadPdf[href="#"]` trigger element can appear
    # earlier in the DOM than the real `a.datasheet-link`, and a combined
    # CSS selector's select_one() would grab whichever comes first in
    # document order regardless of which class we'd actually prefer.
    for selector in ("a.datasheet-link[href]", "a.downLoadPdf[href]"):
        for link in soup.select(selector):
            href = (link.get("href") or "").strip()
            if href and href != "#":
                return urljoin(BASE_URL, href)
    return ""


def _product_name(soup, fallback: str = "") -> str:
    el = soup.select_one("[data-track-product-name]")
    name = _clean(el.get("data-track-product-name", "")) if el else ""
    return name or fallback


# --- Top-level: fetch + parse one product page into its full variant list --


def fetch_product_variants(agent, product_url: str, debug_label: str, fallback_name: str = "") -> dict:
    """Fetch one SSD product page and return:

        {
          "status": PartStatus.SUCCESS / .PRODUCT_PAGE_FAILED / .VARIANT_EXTRACTION_FAILED
                     / .SPECIFICATION_EXTRACTION_FAILED,
          "error_message": str,
          "product_name": str,
          "datasheet_url": str,
          "component_url": str,          # canonical, query-stripped product URL
          "variants": [
              {"capacity": str, "form_factor": str, "part_number": str,
               "specifications": OrderedDict},
              ...
          ],
        }

    Never raises — a failure at any stage is reported in "status" so the
    caller (parts_extractor.py) can log it, keep the server/part
    relationship, and move on to the next component instead of aborting
    the whole server or run.
    """
    canonical_url = base_product_url(product_url)
    result = {
        "status": PartStatus.SUCCESS,
        "error_message": "",
        "product_name": fallback_name,
        "datasheet_url": "",
        "component_url": canonical_url,
        "variants": [],
    }

    try:
        soup = open_product_page(agent, canonical_url, debug_label=debug_label)
    except ScrapeError as exc:
        result["status"] = PartStatus.PRODUCT_PAGE_FAILED
        result["error_message"] = f"[{exc.failure_type}] {exc.reason}"
        logger.warning("SSD product page failed for %s: %s", canonical_url, exc.reason)
        return result

    result["product_name"] = _product_name(soup, fallback_name)
    result["datasheet_url"] = _datasheet_url(soup)

    catalog = _extract_catalog_json(str(soup))
    variant_matrix = _variant_part_numbers(catalog)
    if not variant_matrix:
        result["status"] = PartStatus.VARIANT_EXTRACTION_FAILED
        result["error_message"] = "No capacity/form-factor/part-number catalog found on product page"
        logger.warning("SSD variant matrix extraction failed for %s", canonical_url)
        return result

    spec_tables = _parse_all_spec_tables(soup)
    if not spec_tables:
        result["status"] = PartStatus.SPECIFICATION_EXTRACTION_FAILED
        result["error_message"] = "No specification table found on product page"
        # Still return the variants we *do* know exist (part numbers are
        # real data too) — just with empty specifications, never dropped.
        for v in variant_matrix:
            result["variants"].append(
                {"capacity": v["capacity"], "form_factor": v["form_factor"], "part_number": v["part_number"], "specifications": OrderedDict()}
            )
        return result

    for v in variant_matrix:
        table_entry = spec_tables.get(v["form_factor"])
        if table_entry is None and len(spec_tables) == 1:
            table_entry = next(iter(spec_tables.values()))  # single-form-factor product, label may differ slightly
        if table_entry is None:
            specifications = OrderedDict()
        else:
            _, spec_rows = table_entry
            specifications = _resolve_for_capacity(spec_rows, v["capacity"])
        result["variants"].append(
            {
                "capacity": v["capacity"],
                "form_factor": v["form_factor"],
                "part_number": v["part_number"],
                "specifications": specifications,
            }
        )

    return result


class SsdProductCache:
    """The same Kingston SSD product commonly shows up as a compatible
    part on many different servers (e.g. KC600 fits dozens of
    motherboards) — without caching, each server's parts_extractor pass
    would re-fetch and re-parse that identical product page from scratch.
    This cache makes fetch_product_variants() run at most once per
    distinct product URL for the whole scrape run; every server that
    links to it afterward gets the same parsed variant list instantly,
    with each server's own PartRecord rows still built and written
    separately (the cache holds product data, never server/part
    relationships — those stay entirely in parts_extractor.py).
    """

    def __init__(self):
        self._cache = {}

    def get_or_fetch(self, agent, product_url: str, fallback_name: str = "") -> dict:
        canonical_url = base_product_url(product_url)
        if canonical_url in self._cache:
            return self._cache[canonical_url]

        debug_label = f"ssd_product_{len(self._cache)}"
        result = fetch_product_variants(agent, canonical_url, debug_label=debug_label, fallback_name=fallback_name)
        self._cache[canonical_url] = result
        return result

    def __len__(self):
        return len(self._cache)
