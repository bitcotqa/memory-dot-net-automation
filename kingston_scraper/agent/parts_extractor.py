"""Server parts/components extraction — dispatcher.

Kingston's "Compatible Upgrades For Your System" section is organized into
tabs (e.g. "ValueRAM" / memory, "Solid-state drives" / storage — whichever
categories actually apply to that system). Every compatible part is
rendered server-side as an `<li class="product-gallery-card">`.

Memory and SSD parts are extracted by two genuinely different workflows
(see agent/memory_extractor.py and agent/ssd_extractor.py respectively),
because inspecting real pages shows they work completely differently:

- Memory cards carry no dedicated Kingston product page — the
  compatibility card itself (plus its free-text description) is the
  complete data source, so extraction never leaves this page.
- SSD cards carry a real product-page link ("Learn more") whose target
  page exposes vastly more detail than the compatibility card alone: the
  full capacity x form-factor x part-number matrix and a complete
  specification table per form factor. Extraction follows that link,
  fetches the product page once per distinct product URL (cached across
  the whole run — see SsdProductCache), and emits one PartRecord per
  actual purchasable variant.

This module's job is purely: walk the compatibility cards, decide which
workflow each belongs to, and hand off — it holds no spec-parsing logic
of its own.
"""

import json
import re
from collections import OrderedDict

from agent.error_handler import PartStatus
from agent.memory_extractor import extract_memory_part
from agent.ssd_extractor import resolve_component_url
from config.config import BRAND_DEFAULT
from models.models import PartRecord
from utils.logger import get_logger

logger = get_logger(__name__)

_SSD_CATEGORY_MARKERS = ("solid-state", "ssd", "storage")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _tab_label_by_panel_id(soup) -> dict:
    """Map each tabpanel id (e.g. 'tabContent0_1') to its human tab label
    (e.g. 'Storage' from data-pgtab, falling back to the tab's visible
    text like 'Solid-state drives')."""
    mapping = {}
    for tab in soup.select("[data-pgtab][data-tab]"):
        panel_id = tab.get("data-tab", "")
        label = tab.get("data-pgtab", "") or _clean(tab.get_text())
        if panel_id:
            mapping[panel_id] = label
    return mapping


def _card_description(card) -> str:
    lines = []
    for li in card.select(".c-productCard4__details__content__longDesc li"):
        if li.find("a"):
            continue  # datasheet/download/learn-more links, not description text
        text = _clean(li.get_text(" "))
        if not text or text.lower().startswith("part number:"):
            continue
        lines.append(text)
    if not lines:
        short = card.select_one(".c-productCard4__details__shortDesc")
        if short:
            text = _clean(short.get_text())
            if text:
                lines.append(text)
    seen = set()
    unique = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return "; ".join(unique)


def _card_status(card) -> str:
    footer = card.select_one(".c-productCard4__footer__string")
    return _clean(footer.get_text(" ")) if footer else ""


def _card_datasheet_url(card) -> str:
    """Fallback datasheet link read directly off the *server compatibility*
    card — used when a component has no real product page to fetch its
    own (definitive) datasheet link from (memory parts; or an SSD card
    whose product-page fetch failed)."""
    link = card.select_one("a.downLoadPdf[href]")
    if not link:
        return ""
    href = link.get("href") or link.get("data-pdf-link") or ""
    return href if href and href != "#" else ""


def _card_component_url(card) -> str:
    """The real Kingston product-page link, when the card has one — only
    SSD/branded-product cards carry this (a.learn-more); memory
    (ValueRAM) cards never do (confirmed by inspecting real pages)."""
    link = card.select_one("a.learn-more[href]")
    return resolve_component_url(link.get("href")) if link else ""


def _card_spec_attrs(li_tag) -> "OrderedDict":
    attr_labels = [
        ("data-date", "Release Date"),
        ("data-capacity", "Capacity (GB)"),
        ("data-mktsegment", "Market Segment"),
        ("data-group", "Product Family"),
        ("data-category", "Category"),
    ]
    attrs = OrderedDict()
    for attr, label in attr_labels:
        value = _clean(li_tag.get(attr, ""))
        if value and value.upper() != "ALL":
            attrs[label] = value
    return attrs


def _component_name(li_tag, card) -> str:
    name = _clean(li_tag.get("data-name", ""))
    if name:
        return name
    name_el = card.select_one(".c-productCard4__header__link__name")
    return _clean(name_el.get_text()) if name_el else ""


def _component_model(li_tag, part_number: str) -> str:
    group = _clean(li_tag.get("data-group", ""))
    if group:
        return group
    return part_number.split("/")[0].strip() if part_number else ""


def is_ssd_category(component_type: str) -> bool:
    """Public so callers writing parts out (runner.py, splitting into
    kingston_memory_parts.csv / kingston_ssd_parts.csv) can classify a
    PartRecord the exact same way this module decided which extractor to
    use for it, instead of re-implementing the same category check."""
    type_l = (component_type or "").lower()
    return any(marker in type_l for marker in _SSD_CATEGORY_MARKERS)


# Backwards-compatible private alias for the one in-module call site below.
_is_ssd_category = is_ssd_category


def _extraction_section(extraction_status: str) -> dict:
    """Only added to a part's JSON when extraction didn't fully succeed —
    kept deliberately separate from the "Status" bucket, which always
    means Kingston's own availability text ("Available" / "Discontinued:
    ...") for both memory and SSD parts. Conflating the two would mean a
    perfectly-available product with a failed product-page fetch reports
    as if Kingston itself said "COMPONENT_URL_FAILED" — never true, and
    exactly the kind of fabricated-looking data this project avoids."""
    if extraction_status == PartStatus.SUCCESS:
        return {}
    return {"Extraction": {"Result": extraction_status}}


def _basic_ssd_fallback_record(
    li_tag, card, part_number, component_name, component_model, component_type,
    description, availability_status, datasheet_url, component_url, server_name, server_url,
    extraction_status,
):
    """Build a single SSD PartRecord straight from the compatibility card
    alone, with no product-page data — used when there is no product-page
    link to follow, or when following it failed. Never drops the
    component: the server/part relationship and whatever basic info the
    compatibility card itself carries are preserved either way."""

    spec_attrs = _card_spec_attrs(li_tag)
    part_sections = OrderedDict()
    general = OrderedDict()
    if part_number:
        general["Part Number"] = part_number
    if component_name:
        general["Component Name"] = component_name
    if component_model:
        general["Component Model"] = component_model
    if component_type:
        general["Component Type"] = component_type
    if general:
        part_sections["General"] = general
    if spec_attrs:
        part_sections["Specifications"] = spec_attrs
    if description:
        part_sections["Description"] = {"Description": description}
    compatibility = OrderedDict()
    if server_name:
        compatibility["Server"] = server_name
    if server_url:
        compatibility["Server URL"] = server_url
    if compatibility:
        part_sections["Compatibility"] = compatibility
    part_sections["Status"] = {"Status": availability_status}
    part_sections.update(_extraction_section(extraction_status))

    return PartRecord(
        brand=BRAND_DEFAULT,
        server_name=server_name,
        server_url=server_url,
        component_type=component_type,
        component_name=component_name,
        component_model=component_model,
        part_number=part_number,
        capacity=spec_attrs.get("Capacity (GB)", ""),
        form_factor="",
        component_url=component_url,
        datasheet_url=datasheet_url,
        description=description,
        specifications="; ".join(f"{k}: {v}" for k, v in spec_attrs.items()),
        compatibility=server_name,
        part_specifications_json=json.dumps(part_sections, ensure_ascii=False),
        status=availability_status,
    )


def _extract_ssd_parts(li_tag, card, server_name, server_url, agent, ssd_cache) -> list:
    """One PartRecord per actual purchasable variant of the linked SSD
    product (see agent/ssd_extractor.py), or a single basic-info fallback
    record when there's no product link to follow or following it fails —
    the component is never silently dropped either way."""

    part_number = _clean(li_tag.get("data-partnumber", ""))
    component_name = _component_name(li_tag, card)
    component_model = _component_model(li_tag, part_number)
    component_type = _clean(li_tag.get("data-category", "")) or "Solid-State Drives"
    description = _card_description(card)
    availability_status = _card_status(card) or "Available"
    card_datasheet_url = _card_datasheet_url(card)
    component_url = _card_component_url(card)

    if not component_url or agent is None or ssd_cache is None:
        # No product link at all, or we're running without a browser
        # (e.g. unit tests exercising the dispatcher directly) — fall back
        # to the compatibility card's own basic info rather than dropping
        # the component.
        extraction_status = PartStatus.SUCCESS if component_url else PartStatus.COMPONENT_URL_FAILED
        return [
            _basic_ssd_fallback_record(
                li_tag, card, part_number, component_name, component_model, component_type,
                description, availability_status, card_datasheet_url, component_url, server_name, server_url,
                extraction_status,
            )
        ]

    product = ssd_cache.get_or_fetch(agent, component_url, fallback_name=component_name)

    if product["status"] != PartStatus.SUCCESS and not product["variants"]:
        logger.warning(
            "SSD product page for %s (%s) yielded no usable data [%s]: %s",
            component_name, component_url, product["status"], product["error_message"],
        )
        return [
            _basic_ssd_fallback_record(
                li_tag, card, part_number, component_name, component_model, component_type,
                description, availability_status, card_datasheet_url or product["datasheet_url"], component_url,
                server_name, server_url, product["status"],
            )
        ]

    datasheet_url = product["datasheet_url"] or card_datasheet_url
    product_name = product["product_name"] or component_model or component_name

    records = []
    for variant in product["variants"]:
        variant_sections = OrderedDict()
        variant_sections["Product"] = OrderedDict(
            [("Name", product_name), ("Component Type", component_type)]
        )
        variant_sections["Variant"] = OrderedDict(
            [
                ("Form Factor", variant["form_factor"]),
                ("Capacity", variant["capacity"]),
                ("Part Number", variant["part_number"]),
            ]
        )
        if variant["specifications"]:
            variant_sections["Specifications"] = variant["specifications"]
        compatibility = OrderedDict()
        if server_name:
            compatibility["Server"] = server_name
        if server_url:
            compatibility["Server URL"] = server_url
        if compatibility:
            variant_sections["Compatibility"] = compatibility
        variant_sections["Status"] = {"Status": availability_status}
        variant_sections.update(_extraction_section(product["status"]))

        records.append(
            PartRecord(
                brand=BRAND_DEFAULT,
                server_name=server_name,
                server_url=server_url,
                component_type=component_type,
                component_name=product_name,
                component_model=product_name,
                part_number=variant["part_number"],
                capacity=variant["capacity"],
                form_factor=variant["form_factor"],
                component_url=component_url,
                datasheet_url=datasheet_url,
                description=description,
                specifications="; ".join(f"{k}: {v}" for k, v in variant["specifications"].items()),
                compatibility=server_name,
                part_specifications_json=json.dumps(variant_sections, ensure_ascii=False),
                status=availability_status,
            )
        )

    if not records:
        # Product page loaded and even reported SUCCESS, but somehow no
        # variants matched this card's own part number scope — extremely
        # unlikely given fetch_product_variants' own fallbacks, but never
        # silently drop the component if it does happen.
        return [
            _basic_ssd_fallback_record(
                li_tag, card, part_number, component_name, component_model, component_type,
                description, availability_status, datasheet_url, component_url, server_name, server_url,
                PartStatus.VARIANT_EXTRACTION_FAILED,
            )
        ]

    return records


def extract_parts(soup, server_name: str, server_url: str, agent=None, ssd_cache=None) -> list:
    """Return a list of PartRecord for every compatible-part card found on
    the page, across every tab (Memory, Storage, ...). Returns an empty
    list (not an error) when a system genuinely has no listed parts —
    callers treat that as a valid, if sparse, result.

    `agent` + `ssd_cache` are optional: when provided, SSD cards follow
    their real product-page link for complete variant/specification data
    (see agent/ssd_extractor.py); when omitted (e.g. a unit test with no
    browser), SSD cards fall back to their compatibility-card-only basic
    info instead of raising.
    """
    panel_labels = _tab_label_by_panel_id(soup)
    parts = []
    seen_part_numbers = set()

    cards = soup.select("li.product-gallery-card")
    for li_tag in cards:
        card = li_tag.select_one(".c-productCard4")
        if card is None:
            continue

        part_number = _clean(li_tag.get("data-partnumber", ""))
        component_name_for_dedup = _component_name(li_tag, card)
        dedup_key = part_number or component_name_for_dedup
        if dedup_key and dedup_key in seen_part_numbers:
            continue  # same part surfaced twice (e.g. filter re-render) on one page
        if dedup_key:
            seen_part_numbers.add(dedup_key)

        panel = li_tag.find_parent(id=lambda v: v and v.startswith("tabContent"))
        panel_id = panel.get("id") if panel else ""
        tab_label = panel_labels.get(panel_id, "")
        component_type = _clean(li_tag.get("data-category", "")) or tab_label or "Component"

        if _is_ssd_category(component_type):
            parts.extend(_extract_ssd_parts(li_tag, card, server_name, server_url, agent, ssd_cache))
            continue

        component_name = _component_name(li_tag, card)
        description = _card_description(card)
        status = _card_status(card) or "Available"
        datasheet_url = _card_datasheet_url(card)

        parts.append(
            extract_memory_part(
                li_tag,
                card,
                part_number,
                component_name,
                description,
                status,
                datasheet_url,
                server_name,
                server_url,
                component_type,
            )
        )

    if not cards:
        logger.info("No compatible-upgrade cards found for %s", server_url)

    return parts
