"""Memory (ValueRAM) part extraction — kept separate from ssd_extractor.py
because memory compatibility cards work completely differently from SSD
product pages: inspecting real server-compatibility pages shows memory
cards carry **no dedicated Kingston product page link at all** (no
"Learn more" anchor — only a datasheet PDF, when one exists, and a
generic /en/support link). So there is no product page to follow; the
compatibility card itself, plus its free-text description line, is the
complete and authoritative source for a memory part's specifications.

Kingston doesn't expose memory attributes (speed, CAS latency, voltage,
ECC, module type, ...) as separate structured fields for these legacy
ValueRAM SKUs — they're only present as one free-text description line,
e.g. "DDR2 800MT/s ECC Unbuffered DIMM CL6 1.8V 240-pin". This module
tokenizes that line with a small set of well-known Kingston memory-spec
patterns; anything it can't confidently recognize is left in the
description text rather than guessed at.
"""

import json
import re
from collections import OrderedDict

from models.models import PartRecord
from config.config import BRAND_DEFAULT
from utils.logger import get_logger

logger = get_logger(__name__)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


# Attributes present on the card's own data-* markup that are genuinely
# useful specification context (as opposed to internal pricing/tracking
# plumbing like data-price/data-index/data-producttypeid/data-track-*,
# which carry no product information and are deliberately excluded).
_GENERIC_ATTR_LABELS = [
    ("data-date", "Release Date"),
    ("data-capacity", "Capacity (GB)"),
    ("data-mktsegment", "Market Segment"),
    ("data-group", "Product Family"),
    ("data-category", "Category"),
]

_TOKEN_PATTERNS = [
    ("Capacity", re.compile(r"\b(\d+(?:\.\d+)?\s?(?:GB|MB|TB))\b", re.IGNORECASE)),
    ("Memory Type", re.compile(r"\b(DDR\d?[LE]?)\b", re.IGNORECASE)),
    ("Speed", re.compile(r"\b(\d{3,5}\s?MT/s|\d{3,5}\s?MHz)\b", re.IGNORECASE)),
    ("CAS Latency", re.compile(r"\b(CL\d+(?:-\d+){0,3})\b", re.IGNORECASE)),
    ("Voltage", re.compile(r"\b(\d+(?:\.\d+)?\s?V)\b")),
    ("Module Type", re.compile(r"\b(SO-?DIMM|U-?DIMM|R-?DIMM|DIMM)\b", re.IGNORECASE)),
    ("Pin Count", re.compile(r"\b(\d{2,3})-pin\b", re.IGNORECASE)),
]

# Matched separately (order matters: check the negative form first so
# "Non-ECC" never also matches the plain "ECC" pattern).
_ECC_RE = re.compile(r"\b(Non-ECC|ECC)\b", re.IGNORECASE)
_REG_UNBUF_RE = re.compile(r"\b(Registered|Unbuffered)\b", re.IGNORECASE)


def _tokenize_description(description: str) -> "OrderedDict":
    """Best-effort structured fields pulled from a free-text memory
    description. Only ever adds a field when a pattern actually matched —
    never invents a value for a spec the text doesn't state."""
    specs = OrderedDict()
    if not description:
        return specs

    for label, pattern in _TOKEN_PATTERNS:
        m = pattern.search(description)
        if m:
            specs[label] = _clean(m.group(1))

    m = _ECC_RE.search(description)
    if m:
        specs["ECC"] = _clean(m.group(1))

    m = _REG_UNBUF_RE.search(description)
    if m:
        specs["Registered/Unbuffered"] = _clean(m.group(1))

    return specs


def _generic_attrs(li_tag) -> "OrderedDict":
    attrs = OrderedDict()
    for attr, label in _GENERIC_ATTR_LABELS:
        value = _clean(li_tag.get(attr, ""))
        if value and value.upper() != "ALL":
            attrs[label] = value
    return attrs


def _component_model(li_tag, part_number: str) -> str:
    group = _clean(li_tag.get("data-group", ""))
    if group:
        return group
    return part_number.split("/")[0].strip() if part_number else ""


def extract_memory_part(
    li_tag,
    card,
    part_number: str,
    component_name: str,
    description: str,
    status: str,
    datasheet_url: str,
    server_name: str,
    server_url: str,
    component_type: str,
) -> PartRecord:
    """Build one PartRecord for a memory compatibility card. Unlike SSD
    parts, this never involves a second page fetch — memory parts have no
    product page to follow (see module docstring); everything comes from
    the card already present on the server's compatibility page.
    """
    generic_attrs = _generic_attrs(li_tag)
    # Capacity most often only appears in the card's name/title (e.g. "1GB
    # DDR2 800MT/s ECC Unbuffered DIMM"), not in the separate description
    # line — tokenize both and let the description's tokens win on overlap
    # (it's the more detailed/authoritative of the two).
    derived = _tokenize_description(component_name)
    derived.update(_tokenize_description(description))

    specifications = OrderedDict()
    specifications.update(generic_attrs)
    specifications.update(derived)  # description-derived fields win on overlap (more specific)

    capacity = derived.get("Capacity", generic_attrs.get("Capacity (GB)", ""))
    form_factor = derived.get("Module Type", "")

    part_sections = OrderedDict()
    general = OrderedDict()
    if part_number:
        general["Part Number"] = part_number
    if component_name:
        general["Component Name"] = component_name
    component_model = _component_model(li_tag, part_number)
    if component_model:
        general["Component Model"] = component_model
    if component_type:
        general["Component Type"] = component_type
    if general:
        part_sections["General"] = general

    if specifications:
        part_sections["Specifications"] = specifications

    if description:
        part_sections["Description"] = {"Description": description}

    compatibility = OrderedDict()
    if server_name:
        compatibility["Server"] = server_name
    if server_url:
        compatibility["Server URL"] = server_url
    if compatibility:
        part_sections["Compatibility"] = compatibility

    if status:
        part_sections["Status"] = {"Status": status}

    return PartRecord(
        brand=BRAND_DEFAULT,
        server_name=server_name,
        server_url=server_url,
        component_type=component_type,
        component_name=component_name,
        component_model=component_model,
        part_number=part_number,
        capacity=capacity,
        form_factor=form_factor,
        component_url="",  # memory parts have no dedicated Kingston product page
        datasheet_url=datasheet_url,
        description=description,
        specifications="; ".join(f"{k}: {v}" for k, v in specifications.items()),
        compatibility=server_name,
        part_specifications_json=json.dumps(part_sections, ensure_ascii=False),
        status=status,
    )


# --- CSV row shape for kingston_memory_parts.csv -----------------------------
#
# Unlike the SSD parts CSV (fixed PART_CSV_COLUMNS — unchanged, per the
# task that added it), the memory parts CSV gives every specification key
# its own column instead of bottling them into one flat `specifications`
# string, because different memory parts genuinely carry different
# attribute sets (one has CAS Latency, another has Rank/DRAM Density, ...)
# — see kingston_scraper's task history. build_csv_row() derives that row
# straight from the PartRecord's own fields plus a re-parse of its own
# part_specifications_json, so the CSV can never drift from the JSON: they
# are two views of the exact same data, never two independently-built
# ones.

# Sections flattened in this fixed order — matches the task's own
# requested column order (identity columns, then Compatible
# Server/Server URL, then the dynamic specification columns, then
# Status). A key already used by an earlier section gets its section
# name prefixed rather than silently overwriting the earlier value (e.g.
# two sections both having a "Type" key would become "General Type" /
# "Specifications Type").
_FLATTEN_SECTION_ORDER = ["General", "Description", "Compatibility", "Specifications", "Status"]

# Kingston's own terminology is preserved as-is for every dynamic
# specification key — only Compatibility's two keys get renamed, since
# "Server"/"Server URL" alone would be ambiguous next to the record's own
# server_name/server_url columns (which describe the *same* server, not a
# different, genuinely "compatible" one — see task: "Compatible Server" /
# "Compatible Server URL").
_COMPATIBILITY_RENAMES = {"Server": "Compatible Server", "Server URL": "Compatible Server URL"}


def _flatten_sections(sections: dict) -> "OrderedDict":
    flat = OrderedDict()

    def add_bucket(section_name, bucket):
        if not isinstance(bucket, dict):
            return
        for key, value in bucket.items():
            col = _COMPATIBILITY_RENAMES.get(key, key) if section_name == "Compatibility" else key
            if col in flat:
                col = f"{section_name} {key}"  # collision — disambiguate, never overwrite
            flat[col] = value

    for section_name in _FLATTEN_SECTION_ORDER:
        add_bucket(section_name, sections.get(section_name))
    # Future/unexpected sections (defensive — not seen in practice) still
    # make it into the row rather than being silently dropped.
    for section_name, bucket in sections.items():
        if section_name not in _FLATTEN_SECTION_ORDER:
            add_bucket(section_name, bucket)

    return flat


def build_csv_row(part: PartRecord) -> "OrderedDict":
    """Flatten one memory PartRecord into the dynamic-column row shape
    kingston_memory_parts.csv actually writes: fixed identity columns,
    then every General/Description/Compatibility/Status/Specifications
    key as its own column (Specifications' keys vary part to part — that
    variability is exactly the point), then the complete JSON verbatim as
    the authoritative record.
    """
    row = OrderedDict()
    row["Brand"] = part.brand
    row["Server Name"] = part.server_name
    row["Server URL"] = part.server_url
    row["Component Type"] = part.component_type
    row["Component Name"] = part.component_name
    row["Component Model"] = part.component_model
    row["Part Number"] = part.part_number
    row["Component URL"] = part.component_url
    row["Datasheet URL"] = part.datasheet_url
    row["Description"] = part.description

    try:
        sections = json.loads(part.part_specifications_json) if part.part_specifications_json else {}
    except (ValueError, TypeError) as exc:
        logger.warning("Could not parse part_specifications_json for %s: %s", part.part_number, exc)
        sections = {}

    flat = _flatten_sections(sections if isinstance(sections, dict) else {})
    # General/Description are already covered by the fixed identity
    # columns above (same values, just not re-derived from JSON) — only
    # the genuinely new columns (dynamic Specifications keys, renamed
    # Compatibility keys, Status) need adding here.
    for col in ("Part Number", "Component Name", "Component Model", "Component Type", "Description"):
        flat.pop(col, None)
    for col, value in flat.items():
        row[col] = value

    row["part_specifications_json"] = part.part_specifications_json
    return row


def migrate_legacy_row(row: dict) -> dict:
    """Upgrade one row loaded from an older kingston_memory_parts.csv —
    written under the fixed, all-lowercase/snake_case PART_CSV_COLUMNS
    schema this file used before it got dynamic per-specification-key
    columns — into the current shape.

    Without this, resuming onto an old file would leave old and new rows
    with two different, mismatched column sets (old lowercase "status"
    next to new "Status", old "server_url" next to new "Server URL", ...)
    — each column blank for whichever schema's rows didn't produce it.
    That's exactly the "Status column and data is missing" symptom this
    fixes: the data was never actually lost, it just landed in a
    same-meaning column under the wrong, differently-cased name.

    The old schema's part_specifications_json already used this exact
    same JSON shape (General/Specifications/Description/Compatibility/
    Status) — only the flat CSV projection of it changed — so an old row
    can be losslessly rebuilt by feeding its own identity fields + its own
    already-correct JSON back through build_csv_row(), the same function
    that builds every current row.

    A row already in the current shape (has a "Status" column) is
    returned unchanged — this only ever touches genuinely old rows.
    """
    if "Status" in row or "part_specifications_json" not in row:
        return row

    part = PartRecord(
        brand=row.get("brand", ""),
        server_name=row.get("server_name", ""),
        server_url=row.get("server_url", ""),
        component_type=row.get("component_type", ""),
        component_name=row.get("component_name", ""),
        component_model=row.get("component_model", ""),
        part_number=row.get("part_number", ""),
        component_url=row.get("component_url", ""),
        datasheet_url=row.get("datasheet_url", ""),
        description=row.get("description", ""),
        part_specifications_json=row.get("part_specifications_json", ""),
        status=row.get("status", ""),
    )
    return build_csv_row(part)
