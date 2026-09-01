import json

from bs4 import BeautifulSoup

from agent.extraction import split_brand_model, detect_server_type, extract_server_specs, build_sections
from agent.parts_extractor import extract_parts


SAMPLE_HTML = """
<html><body>
<h1 class="u-h3">ABIT - AN9 32X Motherboard</h1>
<div class="c-configuratorResultsCard">
  <div class="c-configuratorResultsCard__header"><h3 class="u-h5">Memory</h3></div>
  <div class="c-configuratorResultsCard__info">
    <ul class="u-list-unstyled">
      <li><h4 class="u-h6">Standard</h4><p>0 MB (Removable)</p></li>
      <li><h4 class="u-h6">Maximum</h4><p>8 GB</p></li>
    </ul>
  </div>
</div>
<div class="c-configuratorResultsCard">
  <div class="c-configuratorResultsCard__header"><h3 class="u-h5">Storage</h3></div>
  <div class="c-configuratorResultsCard__info">
    <ul class="u-list-unstyled">
      <li><h4 class="u-h6">Standard</h4><p>256 GB</p></li>
      <li><h4 class="u-h6">Maximum</h4><p>2 TB</p></li>
      <li><h4 class="u-h6">Interface</h4><p>NVMe</p></li>
    </ul>
  </div>
</div>
<div class="c-configuratorResultsCard">
  <div class="c-configuratorResultsCard__header"><h3 class="u-h5">CPU / Chipsets</h3></div>
  <div class="c-configuratorResultsCard__info">
    <ul class="u-list-unstyled"><li><p>AMD Athlon 64 (AM2) Nvidia nForce 590 SLI</p></li></ul>
  </div>
</div>
<div class="c-configuratorResultsCard c-configuratorResultsCard--additionalInfo">
  <div class="c-configuratorResultsCard__header"><h3 class="u-h5 u-txt-uppercase">Important Configuration Notes</h3></div>
  <div class="c-configuratorResultsCard__info">
    <div class="l-row"><div class="l-row__col"><ul><li>MODULES MUST BE ORDERED IN PAIRS.</li></ul></div></div>
  </div>
</div>

<div class="l-tabView">
  <ul class="l-tabView__tabs">
    <li data-pgtab="Memory" data-tab="tabContent0_0"><span>ValueRAM</span></li>
    <li data-pgtab="Storage" data-tab="tabContent0_1"><span>Solid-state drives</span></li>
  </ul>
  <div class="l-tabView__panels">
    <div class="l-tabView__panels__panel" id="tabContent0_0">
      <li class="product-gallery-card" data-partnumber="KVR800D2E6/1G" data-name="1GB DDR2 800MT/s ECC Unbuffered DIMM" data-date="05/02/2008" data-mktsegment="ALL">
        <div class="c-productCard4">
          <div class="c-productCard4__header"><a class="c-productCard4__header__link"><span class="c-productCard4__header__link__name">1GB DDR2 800MT/s ECC Unbuffered DIMM</span></a></div>
          <div class="c-productCard4__details">
            <div class="c-productCard4__details__content__longDesc">
              <ul>
                <li class="c-productCard4__details__content__longDesc__partNumber">Part Number: KVR800D2E6/1G</li>
                <li>DDR2 800MT/s ECC Unbuffered DIMM CL6 1.8V 240-pin</li>
                <li><a class="downLoadPdf" href="https://www.kingston.com/datasheets/KVR800D2E6_1G.pdf" data-pdf-link="https://www.kingston.com/datasheets/KVR800D2E6_1G.pdf">Spec Sheet PDF</a></li>
              </ul>
            </div>
            <p class="c-productCard4__details__shortDesc">DDR2 800MT/s ECC Unbuffered DIMM CL6 1.8V 240-pin</p>
          </div>
          <div class="c-productCard4__footer"><div class="c-productCard4__footer__string">Discontinued: <a>Get support</a></div></div>
        </div>
      </li>
    </div>
    <div class="l-tabView__panels__panel" id="tabContent0_1">
      <li class="product-gallery-card" data-partnumber="SA400S37/960G" data-name="960GB A400 SATA3 2.5 SSD" data-category="Solid-State Drives" data-group="SA400S37" data-capacity="960" data-date="02/22/2018">
        <div class="c-productCard4">
          <div class="c-productCard4__header"><a class="c-productCard4__header__link"><span class="c-productCard4__header__link__name">960GB A400 SATA3 2.5 SSD</span></a></div>
          <div class="c-productCard4__details">
            <div class="c-productCard4__details__content__longDesc">
              <ul><li class="c-productCard4__details__content__longDesc__partNumber">Part Number: SA400S37/960G</li></ul>
            </div>
            <p class="c-productCard4__details__shortDesc">960GB A400 SATA3 2.5 SSD</p>
          </div>
          <div class="c-productCard4__footer"></div>
        </div>
      </li>
    </div>
  </div>
</div>
</body></html>
"""


def test_split_brand_model():
    assert split_brand_model("ABIT- AN9 32X Motherboard") == ("ABIT", "AN9 32X Motherboard")
    assert split_brand_model("No Separator Here") == ("", "No Separator Here")
    assert split_brand_model("") == ("", "")


def test_detect_server_type():
    assert detect_server_type("ABIT- AN9 32X Motherboard") == "Motherboard"
    assert detect_server_type("Dell PowerEdge Server") == "Server"
    assert detect_server_type("Something Unclassified") == ""


def test_extract_server_specs_maps_known_and_unknown_cards():
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    record = extract_server_specs(soup, "ABIT- AN9 32X Motherboard", "https://example.com/1")

    assert record.brand == "Kingston"
    assert record.server_model == "AN9 32X Motherboard"
    assert record.server_type == "Motherboard"
    assert "8 GB" in record.memory
    assert "Standard: 0 MB" in record.memory
    assert "AMD Athlon 64" in record.processor
    # Important Configuration Notes gets its own dedicated column.
    assert "MODULES MUST BE ORDERED" in record.important_configuration_notes
    # power_supply/dimensions/weight/environment/warranty were removed
    # entirely — Kingston's compatibility pages never expose those
    # categories, confirmed across every real server checked in this
    # project — so there's no dead-weight always-blank column for them.
    assert not hasattr(record, "power_supply")
    assert not hasattr(record, "dimensions")
    assert not hasattr(record, "warranty")

    # Storage's own Standard/Maximum must land in the storage column and
    # NOT bleed into memory just because both cards use the same generic
    # attribute names.
    assert "256 GB" in record.storage
    assert "2 TB" in record.storage
    assert "NVMe" in record.storage
    assert "2 TB" not in record.memory
    assert "8 GB" not in record.storage


def test_specifications_json_preserves_category_hierarchy():
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    record = extract_server_specs(soup, "ABIT- AN9 32X Motherboard", "https://example.com/1")

    sections = json.loads(record.specifications_json)  # must be valid, parseable JSON

    assert sections["Memory"] == {"Standard": "0 MB (Removable)", "Maximum": "8 GB"}
    assert sections["Storage"] == {"Standard": "256 GB", "Maximum": "2 TB", "Interface": "NVMe"}
    # Same attribute name ("Maximum") under two different categories stays
    # distinct — this is the exact scenario the mapping must never collapse.
    assert sections["Memory"]["Maximum"] != sections["Storage"]["Maximum"]
    # "Details" (the free-form, unlabeled bucket) is always a list — even
    # for one item — because it fundamentally represents "a list of
    # independent items on this card" (processor names, notes, ...), so
    # its shape never depends on how many happened to be on this page.
    assert sections["CPU / Chipsets"]["Details"] == ["AMD Athlon 64 (AM2) Nvidia nForce 590 SLI"]
    assert "Important Configuration Notes" in sections


def test_build_sections_only_includes_categories_actually_present():
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    sections = build_sections(soup)
    # No fabricated categories (Power Supply, Warranty, ...) that aren't
    # actually on this page.
    assert "Power Supply" not in sections
    assert "Warranty" not in sections
    assert set(sections.keys()) == {"Memory", "Storage", "CPU / Chipsets", "Important Configuration Notes"}


def test_extract_parts_reads_both_tabs_with_attrs():
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    parts = extract_parts(soup, "ABIT- AN9 32X Motherboard", "https://example.com/1")

    assert len(parts) == 2
    memory_part = next(p for p in parts if p.part_number == "KVR800D2E6/1G")
    assert memory_part.component_type == "Memory"
    # Memory parts have no dedicated Kingston product page (confirmed by
    # inspecting real pages) — component_url must stay empty rather than
    # being the PDF, which belongs in datasheet_url instead.
    assert memory_part.component_url == ""
    assert memory_part.datasheet_url.endswith(".pdf")
    assert "Discontinued" in memory_part.status
    assert "ECC Unbuffered DIMM" in memory_part.description
    # Description-tokenized fields, not just the raw data-* attrs:
    assert "DDR2" in memory_part.specifications
    assert "1.8V" in memory_part.specifications

    # This fixture's SSD card has no "Learn more" product-page link, so it
    # must fall back to the compatibility-card's own basic info rather
    # than being dropped — component_url stays empty (no link to follow),
    # never the PDF either.
    ssd_part = next(p for p in parts if p.part_number == "SA400S37/960G")
    assert ssd_part.component_type == "Solid-State Drives"
    assert ssd_part.component_model == "SA400S37"
    assert ssd_part.component_url == ""
    assert "Capacity (GB): 960" in ssd_part.specifications
    # `status` always stays Kingston's own availability text — never
    # overwritten by our internal extraction-outcome codes (those live
    # separately, under part_specifications_json["Extraction"]).
    assert ssd_part.status == "Available"  # no footer string -> defaulted, not hallucinated spec

    part_sections = json.loads(memory_part.part_specifications_json)
    assert part_sections["General"]["Part Number"] == "KVR800D2E6/1G"
    assert part_sections["General"]["Component Type"] == "Memory"
    assert part_sections["Compatibility"]["Server"] == "ABIT- AN9 32X Motherboard"
    assert "Status" in part_sections and part_sections["Status"]["Status"]

    ssd_sections = json.loads(ssd_part.part_specifications_json)
    assert ssd_sections["Specifications"]["Capacity (GB)"] == "960"
    assert ssd_sections["General"]["Component Type"] == "Solid-State Drives"


def test_extract_parts_dedupes_repeated_part_numbers():
    dup_html = SAMPLE_HTML.replace(
        '<div class="l-tabView__panels__panel" id="tabContent0_1">',
        '<div class="l-tabView__panels__panel" id="tabContent0_2">',
        1,
    )
    soup = BeautifulSoup(dup_html + SAMPLE_HTML, "html.parser")
    parts = extract_parts(soup, "ABIT- AN9 32X Motherboard", "https://example.com/1")
    part_numbers = [p.part_number for p in parts]
    assert part_numbers.count("KVR800D2E6/1G") == 1


MULTI_ITEM_HTML = """
<html><body>
<h1 class="u-h3">ABIT - AB9 Wi-Fi Motherboard</h1>
<div class="c-configuratorResultsCard">
  <div class="c-configuratorResultsCard__header"><h3 class="u-h5">CPU / Chipsets</h3></div>
  <div class="c-configuratorResultsCard__info">
    <ul class="u-list-unstyled">
      <li><p>Intel Core 2 Duo Intel P965</p></li>
      <li><p>Intel Core 2 Extreme Intel P965</p></li>
      <li><p>Intel Core 2 Quad Intel P965</p></li>
    </ul>
  </div>
</div>
<div class="c-configuratorResultsCard c-configuratorResultsCard--additionalInfo">
  <div class="c-configuratorResultsCard__header"><h3 class="u-h5 u-txt-uppercase">Important Configuration Notes</h3></div>
  <div class="c-configuratorResultsCard__info">
    <div class="l-row"><div class="l-row__col"><ul>
      <li>MODULES MUST BE ORDERED IN PAIRS.</li>
      <li>MAXIMUM OF 4 MODULES SUPPORTED.</li>
    </ul></div></div>
  </div>
</div>
</body></html>
"""


def test_multiple_processor_names_stored_as_a_real_array_not_joined_string():
    """A CPU/Chipsets card listing several independent, unlabeled
    processor names must produce a genuine JSON array in both
    specifications_json and the flat `processor` CSV column — never a
    "; "-joined string, which makes item boundaries ambiguous (e.g.
    indistinguishable from one processor name that happens to contain
    "; ") and is exactly the kind of value that can look like it's
    spilling into neighboring columns.
    """
    soup = BeautifulSoup(MULTI_ITEM_HTML, "html.parser")
    record = extract_server_specs(soup, "ABIT - AB9 Wi-Fi Motherboard", "https://example.com/ab9")

    sections = json.loads(record.specifications_json)
    assert sections["CPU / Chipsets"]["Details"] == [
        "Intel Core 2 Duo Intel P965",
        "Intel Core 2 Extreme Intel P965",
        "Intel Core 2 Quad Intel P965",
    ]

    # The flat CSV column holds the same data as a parseable JSON array
    # string — round-trips exactly, nothing lost or reflattened.
    assert json.loads(record.processor) == [
        "Intel Core 2 Duo Intel P965",
        "Intel Core 2 Extreme Intel P965",
        "Intel Core 2 Quad Intel P965",
    ]


def test_multiple_configuration_notes_stored_in_their_own_dedicated_column():
    soup = BeautifulSoup(MULTI_ITEM_HTML, "html.parser")
    record = extract_server_specs(soup, "ABIT - AB9 Wi-Fi Motherboard", "https://example.com/ab9")

    sections = json.loads(record.specifications_json)
    assert sections["Important Configuration Notes"]["Details"] == [
        "MODULES MUST BE ORDERED IN PAIRS.",
        "MAXIMUM OF 4 MODULES SUPPORTED.",
    ]

    # Its own column — a clean JSON array, no heading-name prefix baked
    # into the value (the column name already conveys that).
    assert json.loads(record.important_configuration_notes) == [
        "MODULES MUST BE ORDERED IN PAIRS.",
        "MAXIMUM OF 4 MODULES SUPPORTED.",
    ]


def test_processor_column_is_always_an_array_even_for_a_single_value():
    """Unlike a genuinely-labeled attribute (Memory's "Standard" vs
    "Maximum", which stay separate scalar keys), the free-form "Details"
    bucket behind `processor` (and `important_configuration_notes`)
    always renders as a JSON array — even for the single-item case (see
    SAMPLE_HTML's CPU / Chipsets card) — because it represents "a list of
    independent items", and a consistent shape beats one that depends on
    how many items happened to be on a given page."""
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    record = extract_server_specs(soup, "ABIT- AN9 32X Motherboard", "https://example.com/1")
    assert json.loads(record.processor) == ["AMD Athlon 64 (AM2) Nvidia nForce 590 SLI"]
    sections = json.loads(record.specifications_json)
    assert sections["CPU / Chipsets"]["Details"] == ["AMD Athlon 64 (AM2) Nvidia nForce 590 SLI"]


def test_repeated_h4_label_within_one_card_also_becomes_an_array():
    """Dynamic detection isn't limited to the free-form "Details" bucket —
    a genuinely labeled attribute (h4) that legitimately repeats within
    one card must be treated the same way: multiple distinct values ->
    array, never a "; "-joined string.
    """
    html = """
    <html><body>
    <div class="c-configuratorResultsCard">
      <div class="c-configuratorResultsCard__header"><h3 class="u-h5">Memory</h3></div>
      <div class="c-configuratorResultsCard__info">
        <ul class="u-list-unstyled">
          <li><h4 class="u-h6">Supported</h4><p>DDR4-2400</p></li>
          <li><h4 class="u-h6">Supported</h4><p>DDR4-2666</p></li>
          <li><h4 class="u-h6">Supported</h4><p>DDR4-2933</p></li>
        </ul>
      </div>
    </div>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    sections = build_sections(soup)
    assert sections["Memory"]["Supported"] == ["DDR4-2400", "DDR4-2666", "DDR4-2933"]

    record = extract_server_specs(soup, "Test Server", "https://example.com/x")
    # The flat column keeps the "Supported:" label (a real, present
    # attribute name — unlike the synthetic Details bucket) followed by a
    # clean JSON array for its value, e.g. 'Supported: ["DDR4-2400", ...]'.
    assert record.memory.startswith("Supported: [")
    json_part = record.memory.split("Supported: ", 1)[1]
    assert json.loads(json_part) == ["DDR4-2400", "DDR4-2666", "DDR4-2933"]


def test_embedded_double_quotes_are_normalized_to_avoid_csv_misalignment():
    """Regression test: Kingston's own text occasionally contains a
    literal double quote (e.g. a "K2" kit name in Important Configuration
    Notes). Once that text is JSON-encoded and the whole field is then
    CSV-quoted, the raw file ends up with a nested escape sequence
    (`\\""..."\\""`) that is valid RFC4180 CSV (Python's own csv module
    round-trips it fine) but has been observed to make Google Sheets
    misparse the row, shifting every later column. Verify the quote
    character never reaches that point — swapped for an apostrophe at
    the source (_clean()) instead — for both the flat column and
    specifications_json, so the two stay consistent.
    """
    html = """
    <html><body>
    <div class="c-configuratorResultsCard c-configuratorResultsCard--additionalInfo">
      <div class="c-configuratorResultsCard__header"><h3 class="u-h5 u-txt-uppercase">Important Configuration Notes</h3></div>
      <div class="c-configuratorResultsCard__info">
        <div class="l-row"><div class="l-row__col"><ul>
          <li>MODULES MUST BE ORDERED IN PAIRS. Kingston offers "K2" kit part numbers.</li>
        </ul></div></div>
      </div>
    </div>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    record = extract_server_specs(soup, "Test Server", "https://example.com/x")

    assert '"K2"' not in record.important_configuration_notes
    assert "'K2'" in record.important_configuration_notes
    notes = json.loads(record.important_configuration_notes)
    assert notes == ["MODULES MUST BE ORDERED IN PAIRS. Kingston offers 'K2' kit part numbers."]

    # specifications_json must carry the exact same (sanitized) text —
    # never a mismatch between the two representations of the same data.
    sections = json.loads(record.specifications_json)
    assert sections["Important Configuration Notes"]["Details"] == notes

    # And, concretely, the raw CSV row this record would produce must
    # never contain the risky nested-quote pattern.
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([record.important_configuration_notes])
    raw = buf.getvalue()
    assert '\\""' not in raw
