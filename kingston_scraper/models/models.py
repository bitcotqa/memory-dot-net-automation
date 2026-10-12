"""Data models for the Kingston scraper: one server record, one part
(component) record, and one failed-URL record. Kept dependency-free
(plain dataclasses) so they're trivially unit-testable.
"""

from dataclasses import dataclass, fields, asdict


# --- Server record -----------------------------------------------------

SERVER_CSV_COLUMNS = [
    "brand",
    "server_name",
    "server_url",
    "server_model",
    "product_name",
    "server_type",
    "processor",
    "memory",
    "storage",
    "networking",
    "expansion",
    "compatibility",
    "important_configuration_notes",
    "specifications_json",
    "parts_found",
    "status",
]


@dataclass
class ServerRecord:
    """One record per input server/system page. Every field defaults to ''
    (never None) so a missing spec never breaks CSV writing — it just
    leaves the cell empty.

    power_supply/dimensions/weight/environment/warranty/part_number/
    description/form_factor were removed entirely (not just left blank)
    after confirming, across every real server checked across this
    project (dozens of live pages), that Kingston's memory-compatibility
    pages never expose a System Information card for any of these —
    there's no dead-weight column for a category this page type never
    publishes (part_number/description describe a compatible *part*, not
    the server itself, and are never set at the server level at all — see
    models.PartRecord for the part-level equivalents). If a future input
    file's pages ever do expose one of these, its data is still never
    lost: every card heading is unconditionally captured in
    specifications_json regardless of whether it has a dedicated flat
    column (see agent/extraction.py) — only the flat-column projection
    would need a field added back.
    """

    brand: str = ""
    server_name: str = ""
    server_url: str = ""
    server_model: str = ""
    product_name: str = ""
    server_type: str = ""
    processor: str = ""
    memory: str = ""
    storage: str = ""
    networking: str = ""
    expansion: str = ""
    compatibility: str = ""
    important_configuration_notes: str = ""
    specifications_json: str = ""
    parts_found: str = ""
    status: str = ""

    def to_row(self) -> dict:
        data = asdict(self)
        return {col: data.get(col, "") for col in SERVER_CSV_COLUMNS}

    def dedup_key(self) -> str:
        return (self.server_url or self.server_name).strip().lower()


assert [f.name for f in fields(ServerRecord)] == SERVER_CSV_COLUMNS, (
    "ServerRecord dataclass fields must match SERVER_CSV_COLUMNS exactly and in order"
)


# --- Part / component record ---------------------------------------------

# component_url is the actual Kingston HTML product page (e.g.
# https://www.kingston.com/en/ssd/kc600-sata-solid-state-drive) — never a
# PDF. datasheet_url is the separate spec-sheet PDF, when one exists. Most
# memory (ValueRAM) parts have no dedicated product page on kingston.com
# at all (confirmed by inspecting real pages — see agent/memory_extractor.py),
# so component_url is legitimately blank for those; datasheet_url is still
# populated when the compatibility card links one.
#
# capacity/form_factor are populated for SSD variants (one row per actual
# purchasable capacity+form-factor+part-number combination — see
# agent/ssd_extractor.py) and, for memory, mirror whatever Kingston's own
# part description states (capacity always; form_factor holds the memory
# module type — DIMM/SODIMM/UDIMM/RDIMM — when the description states one,
# since that *is* memory's physical form factor).
PART_CSV_COLUMNS = [
    "brand",
    "server_name",
    "server_url",
    "component_type",
    "component_name",
    "component_model",
    "part_number",
    "capacity",
    "form_factor",
    "component_url",
    "datasheet_url",
    "description",
    "specifications",
    "compatibility",
    "part_specifications_json",
    "status",
]


@dataclass
class PartRecord:
    brand: str = ""
    server_name: str = ""
    server_url: str = ""
    component_type: str = ""
    component_name: str = ""
    component_model: str = ""
    part_number: str = ""
    capacity: str = ""
    form_factor: str = ""
    component_url: str = ""
    datasheet_url: str = ""
    description: str = ""
    specifications: str = ""
    compatibility: str = ""
    part_specifications_json: str = ""
    status: str = ""

    def to_row(self) -> dict:
        data = asdict(self)
        return {col: data.get(col, "") for col in PART_CSV_COLUMNS}

    def dedup_key(self) -> str:
        return (
            f"{(self.server_url or self.server_name).strip().lower()}::"
            f"{(self.part_number or self.component_name).strip().lower()}"
        )


assert [f.name for f in fields(PartRecord)] == PART_CSV_COLUMNS, (
    "PartRecord dataclass fields must match PART_CSV_COLUMNS exactly and in order"
)


# --- Failed URL record ------------------------------------------------------
# Used for both output/kingston_failed_urls.csv (rewritten each run to hold
# only *currently* unresolved failures — see state/failed_store.py) and
# output/kingston_scrape_errors.csv (an append-only historical log of every
# failure attempt, resolved or not). `attempt_number` is the top-level
# phase attempt this failure came from: 1 = initial extraction, 2 = first
# automatic retry round, 3 = second automatic retry round, etc.

FAILED_CSV_COLUMNS = [
    "server_name",
    "server_url",
    "failure_type",
    "failure_reason",
    "error_message",
    "attempt_number",
    "timestamp",
]


@dataclass
class FailedRecord:
    server_name: str = ""
    server_url: str = ""
    failure_type: str = ""
    failure_reason: str = ""
    error_message: str = ""
    attempt_number: str = ""
    timestamp: str = ""

    def to_row(self) -> dict:
        data = asdict(self)
        return {col: data.get(col, "") for col in FAILED_CSV_COLUMNS}


assert [f.name for f in fields(FailedRecord)] == FAILED_CSV_COLUMNS, (
    "FailedRecord dataclass fields must match FAILED_CSV_COLUMNS exactly and in order"
)
