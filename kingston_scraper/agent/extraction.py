"""Server-level specification extraction.

Kingston's memory-configurator/system pages present per-system specs as a
row of collapsible "System Information" cards (Memory, Storage, Expansions,
CPU/Chipsets, Upgrade Path, ...) plus an "Important Configuration Notes"
card. Which cards exist varies system to system — a plain motherboard page
has no "Power Supply" or "Dimensions" card because Kingston simply doesn't
publish that for a memory-compatibility lookup.

Extraction is strictly two-pass:

1. Parse the page into `sections`: an ordered ``{category: {attribute:
   value}}`` dict, one entry per accordion card, keyed by that card's own
   heading (e.g. "Memory", "Storage") — never by attribute name. Two cards
   can both have a "Maximum" attribute (Memory Maximum vs Storage Maximum)
   without colliding, because the attribute lives *inside* its own card's
   dict, not in a flat global namespace. This dict is the source of truth
   and is preserved byte-for-byte as ``specifications_json``.
2. Only after `sections` is fully built do we derive the legacy flat
   ServerRecord columns (memory/storage/processor/...), by looking up each
   *category heading* in a small lookup table and copying that category's
   already-correctly-scoped data into the matching column. The lookup is
   applied to the heading, never to an attribute name — so a "Maximum"
   inside the Storage card can never end up copied into the memory column.
"""

import json
import re
from collections import OrderedDict

from models.models import ServerRecord
from config.config import BRAND_DEFAULT
from utils.logger import get_logger

logger = get_logger(__name__)

# Known accordion-card HEADINGS -> ServerRecord field. Matched
# case-insensitively against the card's own h3 text — this table is keyed
# by category, never by attribute name (see module docstring).
_HEADING_FIELD_MAP = {
    "memory": "memory",
    "storage": "storage",
    "expansions": "expansion",
    "expansion": "expansion",
    "cpu / chipsets": "processor",
    "cpu/chipsets": "processor",
    "cpu chipsets": "processor",
    "processor": "processor",
    "networking": "networking",
    "network": "networking",
    "upgrade path": "compatibility",
    "important configuration notes": "important_configuration_notes",
}

# Generic bucket key used only when a card's list items carry no attribute
# label at all (e.g. "CPU / Chipsets" is just free-form <p> text with no
# <h4>). Never invented per-attribute — this is a single catch-all label
# for genuinely unlabeled content, not a guess at what the label should be.
_DETAILS_KEY = "Details"

_TYPE_KEYWORDS = [
    "Motherboard",
    "Server",
    "Workstation",
    "Desktop",
    "Notebook",
    "Laptop",
    "Mini PC",
    "Blade Server",
    "System",
]

_BRAND_MODEL_RE = re.compile(r"^\s*([^-]+?)\s*-+\s*(.+?)\s*$")


def split_brand_model(name: str):
    """'ABIT- AN9 32X Motherboard' -> ('ABIT', 'AN9 32X Motherboard').
    Falls back to ('', name) when there's no separator, rather than
    guessing a brand that isn't actually there.
    """
    name = (name or "").strip()
    if not name:
        return "", ""
    match = _BRAND_MODEL_RE.match(name)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", name


def detect_server_type(name: str) -> str:
    name_l = (name or "").lower()
    for keyword in _TYPE_KEYWORDS:
        if name_l.endswith(keyword.lower()):
            return keyword
    return ""


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    # A literal double-quote character in extracted text (e.g. Kingston's
    # own "K2" kit naming) ends up, once this text is JSON-encoded (every
    # value here eventually flows into specifications_json, and some into
    # a JSON-array flat column too — see _format_value), as a doubly-
    # escaped sequence once the CSV writer *also* quotes the field
    # (literally `\""..."\""` in the raw file). That's fully valid,
    # unambiguous RFC4180 CSV — Python's own csv module round-trips it
    # perfectly — but real-world spreadsheet import (observed: Google
    # Sheets) can still misparse that specific nested pattern, silently
    # shifting every subsequent column in the row. Swapping it for a
    # plain apostrophe at the single point all text is extracted removes
    # the one character that triggers the risky pattern, everywhere it
    # could appear (both specifications_json and every flat column) — the
    # text is never dropped or reworded otherwise, just a different quote
    # glyph.
    return text.replace('"', "'")


def _dedupe(values: list) -> list:
    seen = set()
    unique = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _scalar_or_array(values: list):
    """The core single-vs-multi-value decision, applied uniformly to every
    attribute (not just the free-form "Details" bucket) so the behavior is
    dynamic across any Kingston specification, not hardcoded to Processor/
    Notes specifically: a genuinely single value stays a plain string;
    two or more distinct values become a real list. Never a "; "-joined
    string either way — that representation is exactly what let a
    multi-value spec's boundaries go ambiguous and, downstream, let values
    intended for one column smear across neighboring ones.
    """
    unique = _dedupe(values)
    return unique[0] if len(unique) == 1 else unique


def _parse_card(card) -> tuple:
    """Parse one `.c-configuratorResultsCard` into (heading, attrs) where
    attrs is an ordered {attribute: value} dict scoped to *this card only*.
    Handles both card layouts seen on Kingston's pages:
      - ul.u-list-unstyled > li > (h4 label + one-or-more p values)
      - the "Important Configuration Notes" card's .l-row > ul > li (no
        h4/p structure at all, just plain list items)
    Free-form list items with no h4 label are collected under a single
    "Details" bucket rather than invented per-item keys.

    A genuinely-labeled attribute's value (h4-labeled) is decided
    dynamically between a plain scalar (one value) and a list (more than
    one distinct value) by _scalar_or_array() — a card whose h4-labeled
    attributes are each genuinely distinct (e.g. Memory's "Standard" vs
    "Maximum") is untouched by this; it only applies *within* one key when
    the same label legitimately repeats.

    The unlabeled "Details" bucket (CPU/Chipsets' processor names,
    Important Configuration Notes' individual notes, ...) is always a
    list, even for a single item — unlike a labeled attribute, "Details"
    fundamentally represents "a list of independent items on this card",
    so its shape stays consistent regardless of how many happened to be
    on a given page, rather than sometimes being a bare string and
    sometimes an array depending on count.
    """
    heading_el = card.find("h3")
    heading = _clean(heading_el.get_text()) if heading_el else ""

    values_by_key = OrderedDict()
    detail_values = []

    list_items = card.select("ul.u-list-unstyled > li")
    if not list_items:
        list_items = card.select(".c-configuratorResultsCard__info .l-row li")

    for li in list_items:
        h4 = li.find("h4")
        paragraphs = [_clean(p.get_text()) for p in li.find_all("p") if _clean(p.get_text())]
        if h4 and paragraphs:
            key = _clean(h4.get_text())
            value = " / ".join(paragraphs)
            # Same attribute label repeated within one card (rare, but
            # collected as distinct values rather than silently
            # overwritten or string-joined) — resolved to scalar/array below.
            values_by_key.setdefault(key, []).append(value)
        elif paragraphs:
            detail_values.extend(paragraphs)
        else:
            text = _clean(li.get_text(" "))
            if text and text not in ("Show Bank Schema", "Hide Bank Schema"):
                detail_values.append(text)

    attrs = OrderedDict()
    for key, values in values_by_key.items():
        attrs[key] = _scalar_or_array(values)

    if detail_values:
        attrs[_DETAILS_KEY] = _dedupe(detail_values)  # always a list — see docstring above

    return heading, attrs


def _format_value(value) -> str:
    """Render one attribute's value for the flat, legacy CSV columns. A
    list becomes a JSON array string (e.g. '["a", "b"]') — a proper,
    losslessly-parseable single CSV field, quoted/escaped automatically by
    the csv module regardless of what characters (commas, quotes) the
    individual items contain — never a "; "-joined string, which is the
    exact ambiguity that let multi-value specs spill their apparent
    "boundaries" into what looked like extra columns. (Embedded double
    quotes are already normalized to apostrophes upstream in _clean() —
    see its docstring — so no further sanitizing is needed here.)"""
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _format_attrs(attrs: dict) -> str:
    """Render a card's {attribute: value} dict as the flat string the
    legacy CSV columns use (e.g. 'Standard: 0 MB (Removable); Maximum: 8
    GB'). This is purely a display join of already-correctly-scoped data
    — it never changes which category an attribute belongs to.

    A card whose only content is the synthetic _DETAILS_KEY bucket (i.e.
    the source page never actually labeled it — "CPU / Chipsets" is just
    free-form text with no <h4>, and may list several independent items,
    e.g. multiple supported processor names) is rendered as the bare
    value: a plain string when there's exactly one, a JSON array string
    when there are several (see _format_value) — never as 'Details:
    <value>', which would misattribute a label Kingston itself never used.
    specifications_json stores the same scalar-or-list under "Details" for
    structural consistency.
    """
    if list(attrs.keys()) == [_DETAILS_KEY]:
        return _format_value(attrs[_DETAILS_KEY])
    return "; ".join(f"{key}: {_format_value(value)}" for key, value in attrs.items())


def build_sections(soup) -> "OrderedDict":
    """Parse every System Information card into an ordered
    {category_heading: {attribute: value}} dict — the authoritative,
    hierarchy-preserving representation of everything on the page. Only
    categories/attributes actually present are included; nothing is
    invented and nothing is merged across cards.
    """
    sections = OrderedDict()
    for card in soup.select(".c-configuratorResultsCard"):
        heading, attrs = _parse_card(card)
        if not heading or not attrs:
            continue
        if heading in sections:
            # Same heading appearing twice on one page (not seen in
            # practice, but don't let the second occurrence clobber the
            # first): merge attribute-wise instead of overwriting.
            sections[heading].update(attrs)
        else:
            sections[heading] = attrs
    return sections


def _looks_like_json_array(value: str) -> bool:
    return isinstance(value, str) and value.startswith("[") and value.endswith("]")


def _merge_field_value(existing: str, new_value: str) -> str:
    """Combine a newly-formatted heading's value into a ServerRecord flat
    column that may already hold a value from an *earlier* heading mapped
    to the same field — rare (two differently-spelled headings both
    resolving to the same field on one page), but if both sides are JSON
    array strings (see _format_value), naive "; "-string-concatenation
    would produce '["a"]; ["b"]' — neither valid JSON nor a clean list.
    Parse-and-merge into one real array instead; fall back to the
    original plain-text join for anything that isn't a JSON array.
    """
    if not existing:
        return new_value
    if _looks_like_json_array(existing) and _looks_like_json_array(new_value):
        try:
            return json.dumps(_dedupe(json.loads(existing) + json.loads(new_value)), ensure_ascii=False)
        except (ValueError, TypeError):
            pass  # not actually parseable JSON despite looking like it — fall through
    return f"{existing}; {new_value}".strip("; ")


def extract_server_specs(soup, server_name: str, server_url: str) -> ServerRecord:
    """Build a ServerRecord from the parsed page. Never raises — a page
    with zero recognizable cards still yields a record (mostly blank) so
    the caller can decide success/partial/failure based on how much came
    back, rather than an extraction bug crashing the whole run.
    """
    record = ServerRecord()
    record.brand = BRAND_DEFAULT
    record.server_name = server_name
    record.server_url = server_url

    input_brand, input_model = split_brand_model(server_name)
    record.server_model = input_model or server_name
    record.server_type = detect_server_type(server_name)

    h1 = soup.find("h1")
    record.product_name = _clean(h1.get_text()) if h1 else server_name

    sections = build_sections(soup)
    if not sections:
        logger.warning("No System Information cards found for %s", server_url)

    # Second pass: derive the legacy flat columns strictly from `sections`
    # (i.e. from each card's own heading) — never re-inspect attribute
    # names to decide where a value goes. A heading with no mapped field
    # (e.g. a card type this page happens to have that none of our known
    # headings cover) has no flat-column home, but nothing is lost: every
    # heading — mapped or not — is unconditionally captured in `sections`
    # below and preserved in full in specifications_json regardless.
    for heading, attrs in sections.items():
        heading_key = heading.lower().strip()
        formatted = _format_attrs(attrs)
        if not formatted:
            continue
        field = _HEADING_FIELD_MAP.get(heading_key)
        if field:
            existing = getattr(record, field, "")
            setattr(record, field, _merge_field_value(existing, formatted))

    record.specifications_json = json.dumps(sections, ensure_ascii=False)
    record.status = "extracted"
    return record
