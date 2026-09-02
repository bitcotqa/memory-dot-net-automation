# Kingston Server/Parts Scraper

A standalone Python + Playwright browser-automation agent that reads a list
of Kingston system URLs (motherboards/servers), visits each one, extracts
system-level specifications and every listed compatible part (memory
modules, SSDs, etc.), and writes the results to CSV.

**This project is fully independent of `intel_ark_scraper`** — separate
virtualenv, config, browser agent, extraction logic, CSV schema, and state.
Nothing is shared or imported between the two.

See [`docs/BROWSER_AGENT_WORKFLOW.md`](docs/BROWSER_AGENT_WORKFLOW.md) for a
stage-by-stage walkthrough of what the browser agent actually does, from
one input URL to finished CSV rows.

## How the target site actually works

Kingston doesn't have one catalog page for "servers" — the input file
instead gives one URL per system, each a
`kingston.com/en/memory/search/model/<id>/<slug>` page ("Memory for a
<System>"). Each of those pages is a **memory/parts compatibility lookup
tool** for that specific system, not a full spec sheet — so:

- **System-level info** comes from a row of collapsible "System
  Information" cards (Memory / Storage / Expansions / CPU-Chipsets /
  Upgrade Path — whichever apply to that system) plus an "Important
  Configuration Notes" card.
- **Compatible parts** come from a "Compatible Upgrades For Your System"
  section, organized into tabs (e.g. "ValueRAM", "Solid-state drives").
  Each part is a card carrying its part number, name, short description,
  a spec-sheet PDF link, and release/market-segment/family attributes.

Both are **server-rendered** — every card and every part is already present
in the initial HTML (Kingston just toggles CSS visibility client-side for
tabs/"show more"), confirmed by inspecting real pages before writing any
selector. So the agent does not need to click into a separate product page
per part; it does defensively click any collapsed accordion/tab/"load
more" control it finds, in case a given page renders differently, but
extraction never depends on that succeeding.

Because Kingston doesn't publish things like power supply, physical
dimensions, weight, environment, or warranty terms on these compatibility
pages — confirmed across every real server checked in this project —
`kingston_servers.csv` has no dedicated column for any of those; there's
no dead-weight always-blank column for a category this page type never
exposes. Nothing is lost if a future input file's pages ever do expose
one of these: every card heading is unconditionally captured in
`specifications_json` regardless of whether it has a dedicated flat
column — only the flat-column projection would need a field added back.

## Known limitation: Cloudflare bot management

`kingston.com` is fronted by Cloudflare, including an invisible/JS
challenge ("Performing security verification…" / Turnstile). In testing
from this environment, a handful of requests succeeded before the sandbox's
outbound IP got a hard `403`/challenge on every subsequent request — a
purely IP-reputation-based block, not something the scraper's logic can
out-Wait or out-click.

The agent is built to **detect and report this rather than silently fail**:
- A real Cloudflare interstitial (`Just a moment...` title, or its
  fingerprint markers on a short page) is classified as failure type
  `Blocked` and written to `kingston_failed_urls.csv`.
- Because Cloudflare sometimes serves a stale `403` on the initial response
  and then auto-resolves the JS challenge before our settle-wait finishes,
  the agent trusts the **actually rendered page content** over the raw
  HTTP status — so a `403` isn't fatal by itself if a full real page loaded
  behind it (verified against a real success case with this exact
  behavior during development).
- Retries use backoff (`MAX_RETRIES`, `RETRY_BACKOFF_SECONDS`) but
  deliberately don't hammer a blocked IP — that only gets it flagged
  harder, not un-flagged.

**Practical implication:** running this from a datacenter/CI IP (like most
sandboxes) will likely hit blocks quickly. Run it from a normal
residential/office network for best results, keep `REQUEST_DELAY_SECONDS`
generous (default 4s), and use `--retry-failed` later to reprocess whatever
got blocked in an earlier run.

**Real-world update:** a full 259-server run (including from a normal
office network, not just a sandbox) showed the naive retry loop never
adapting — server 1 succeeds, then *every* subsequent server fails all 3
attempts at the same ~10-25s cadence, for 30+ servers straight. That
pattern points at Cloudflare escalating against the session's request
pattern (many distinct pages in quick succession), not purely IP
reputation — so two things changed:

- **Cooldown / circuit breaker** (`COOLDOWN_TRIGGER_STREAK`,
  `COOLDOWN_SECONDS` — default: after 2 servers fail in a row, pause 90s):
  once a real block streak is detected, the runner stops hammering at the
  same cadence and gives it real time before continuing, logging clearly
  when it does. See `runner.py`.
- **`ENABLE_EXPLORATORY_CLICKS`** (default `false`): the "expand collapsed
  sections" interaction pass in `navigation.py` was never needed for
  extraction (see above — content is already in the initial HTML) and is
  pure extra automated-looking DOM interaction for no data benefit, so
  it's now off by default.

If you're still seeing a hard block after these changes, the next levers
to try (in `.env`): a much larger `COOLDOWN_SECONDS` (e.g. 300+), a larger
`REQUEST_DELAY_SECONDS`, or `HEADLESS=false` if you have a real display — a
visible stable browser is generally less fingerprintable to bot management
than headless Chromium.

### If headed Chrome loops on "Verify you are human"

The scraper never clicks or solves a CAPTCHA automatically. In headed mode it
waits while you complete the visible checkbox and then continues when Kingston
renders the requested page. Headed mode auto-selects installed Google Chrome or
Microsoft Edge, uses a persistent profile, and omits Playwright's
browser-automation signal by default so a legitimate manual verification has
a chance to persist.

CAPTCHA **detection and continuation are automatic and per page load**. After
every server, audit, or SSD-product navigation, the scraper inspects that
page's current title and rendered HTML. A normal page proceeds immediately. A
page carrying Cloudflare challenge markers waits only for that page, checks it
again once per second, and continues as soon as real content replaces the
challenge. A challenge that is not manually cleared before the configured
deadline is recorded as `Blocked`; it is never parsed as product data.

If Cloudflare still loops after you click the checkbox, attach the scraper to a
normal Chrome/Edge process using the included headed runner. Close any previous
scraper browser, then run this from `kingston_scraper`:

```powershell
.\.venv\Scripts\python.exe run_headed.py --resume
```

It auto-selects installed Chrome or Edge, uses a dedicated persistent profile,
opens `kingston.com`, and connects the scraper to that same browser. Complete
the checkbox manually if it appears; the scraper continues automatically. All
normal scraper options can be appended, such as `--retry-failed` or `--max 10`.

The equivalent manual setup is shown below for troubleshooting. Start Chrome:

```powershell
$chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
Start-Process $chrome -ArgumentList '--remote-debugging-port=9222', "--user-data-dir=$PWD\state\manual_chrome_profile"
```

If Chrome is not installed, use Edge instead:

```powershell
$edge = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
Start-Process $edge -ArgumentList '--remote-debugging-port=9222', "--user-data-dir=$PWD\state\manual_edge_profile"
```

In that browser window, complete the checkbox once and keep it open. Then
configure and resume the scraper:

```powershell
$env:BROWSER_CDP_URL = "http://127.0.0.1:9222"
$env:HEADLESS = "false"
.\.venv\Scripts\python.exe main.py --resume
```

## Project layout

```text
kingston_scraper/
├── agent/
│   ├── browser_agent.py     # Playwright wrapper (launch/goto/click/content)
│   ├── navigation.py        # open a page, detect blocking, settle
│   ├── extraction.py        # server-level spec extraction
│   ├── parts_extractor.py   # dispatcher: routes each compatible-part card
│   │                        # to memory_extractor or ssd_extractor
│   ├── memory_extractor.py  # memory (ValueRAM) part extraction — the
│   │                        # compatibility card itself is the whole
│   │                        # data source; no separate product page exists
│   ├── ssd_extractor.py     # SSD product-page extraction: capacity x
│   │                        # form-factor x part-number variant matrix,
│   │                        # complete per-variant specifications,
│   │                        # SsdProductCache (fetch each product once)
│   └── error_handler.py     # failure taxonomy (server- and part-level),
│                            # retry helper, statuses
├── models/models.py         # ServerRecord / PartRecord / FailedRecord
├── utils/
│   ├── io_utils.py          # input CSV reader, dedup-aware CsvWriter,
│   │                        # append-only AppendCsvLogger
│   └── logger.py
├── state/
│   ├── status_store.py      # resume tracking (JSON, per-server status)
│   └── failed_store.py      # current-failures CSV store (rewritable —
│                            # see "Output" below)
├── config/config.py          # all tunables (timeouts, delays, paths, retry)
├── audit/                    # occasional data-freshness job — re-fetches a
│                              # sample of already-shipped output URLs and
│                              # diffs fresh extraction against what's on disk
│                              # (see docs/BROWSER_AGENT_WORKFLOW.md)
├── docs/
│   └── BROWSER_AGENT_WORKFLOW.md  # stage-by-stage browser agent walkthrough
├── input/server_urls.csv     # uploaded input (name,url)
├── output/
│   ├── kingston_servers.csv
│   ├── kingston_memory_parts.csv
│   ├── kingston_ssd_parts.csv
│   ├── kingston_failed_urls.csv   # current, unresolved failures only
│   ├── kingston_scrape_errors.csv # permanent history of every attempt
│   └── debug/                     # HTML dumps saved on blocked/failed pages
├── logs/kingston_scraper.log
├── runner.py                 # orchestrates one full run (two phases —
│                              # initial extraction, then automatic retry)
├── main.py                   # CLI entry point
├── requirements.txt
└── tests/                    # pure-logic unit tests (no network) +
                               # tests/fixtures/ (real captured HTML)
```

## Setup

```bash
cd kingston_scraper
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env   # optional, tune timeouts/delays
```

## Input file format

CSV with (at least) a name column and a URL column — header names are
matched case-insensitively against `name`/`server_name`/`server name` and
`url`/`server_url`/`server url`. The uploaded file
(`input/server_urls.csv`) uses `name,url`. Columns beyond these two are
ignored; nothing about the rest of the input's structure is assumed.

## Running

```bash
# Full run over every URL in the input file — automatically retries
# whatever fails, in the same run, no extra command needed.
python main.py --input input/server_urls.csv

# Resume: skip servers already marked done in state/processing_status.json
python main.py --input input/server_urls.csv --resume

# Cap this run to the first N servers (smoke-testing)
python main.py --input input/server_urls.csv --max 10

# Override how many automatic retry rounds run after the initial phase
# (default: AUTO_RETRY_ATTEMPTS in config.py, currently 2). 0 disables it.
python main.py --retry-attempts 3

# Manual utility only — reprocess whatever is currently in
# kingston_failed_urls.csv, once, with no further automatic retry
# afterward. Not needed for the normal workflow above.
python main.py --retry-failed
```

Every run is two phases: **Phase 1** processes the servers selected by
`--input`/`--resume`/`--max`; **Phase 2** then automatically retries
whichever of *this run's own* servers failed, up to `--retry-attempts`
additional full passes, before the run's final summary. A server that
succeeds on retry is written to the output CSVs immediately and removed
from `kingston_failed_urls.csv`; one that keeps failing stays there.

## Output

### `output/kingston_servers.csv`
One row per successfully-loaded system: brand, server name/URL/model,
product name, server type, processor/memory/storage/networking/expansion/
compatibility, its own dedicated **`important_configuration_notes`**
column (see below), **`specifications_json`**, `parts_found` count, and
`status`. There's no `power_supply`/`dimensions`/`weight`/`environment`/
`warranty`/`part_number`/`description`/`form_factor` column — Kingston
never publishes those attributes at the *server* level on this page type
(`part_number`/`description` describe a compatible *part*, not the
server itself — see `kingston_memory_parts.csv`/`kingston_ssd_parts.csv`
for those) (see "How the target site actually works" above).

There's no generic "other_specifications" catch-all column — every card
heading on a page is unconditionally captured in `specifications_json`
regardless of whether it has a dedicated flat column (see
`_HEADING_FIELD_MAP` in `agent/extraction.py`), so nothing is silently
dropped either way; a heading with no dedicated column (e.g. a
hypothetical future "Power Supply" card) just has no flat-column
projection of its own — it would still show up in `specifications_json`.

Each legacy column (memory, storage, processor, ...) holds only that
*category's own* attributes — extraction parses the page as `{category:
{attribute: value}}` first, keyed by each accordion card's own heading, and
only afterwards copies a whole category's already-correctly-scoped data
into its matching column. Two categories sharing a generic attribute name
(e.g. both "Memory" and "Storage" having a "Maximum") never collide,
because the attribute lives inside its own category's dict the entire
time — the column mapping is applied to the *heading*, never to an
attribute name. See `agent/extraction.py` (`build_sections` /
`_parse_card`) for the two-pass parse this relies on.

`specifications_json` is the authoritative, hierarchy-preserving
representation of the same parse — valid JSON (`json.loads(...)` round-
trips cleanly), one key per category actually present on that server's
page (nothing invented, nothing merged), e.g.:

```json
{
  "Memory": {"Standard": "0 MB (Removable)", "Maximum": "8 GB"},
  "Storage": {"Bus Architecture": "PCI / PCI Express / SSD - SATA 2.5-inch 9.5mm"},
  "CPU / Chipsets": {"Details": ["Intel Core 2 Duo Intel P965", "Intel Core 2 Extreme Intel P965", "Intel Core 2 Quad Intel P965"]}
}
```

`"Details"` is a synthetic bucket used only when a card's list items carry
no attribute label at all on the source page — "CPU / Chipsets" (→
`processor`) and "Important Configuration Notes" (→ its own
`important_configuration_notes` column) are both this shape: free-form
text with no `<h4>`, often listing several independent items. Unlike a
genuinely-labeled attribute (Memory's "Standard" vs "Maximum", which stay
separate scalar keys — untouched by any of this), "Details" is **always
a JSON array, even for a single item** — it fundamentally represents "a
list of independent items on this card", so its shape never depends on
how many happened to be on a given page. The legacy flat column (e.g.
`processor`, `important_configuration_notes`) renders the same array as a
JSON array *string* in that one CSV cell (never as `"Details: [...]"`,
which would misattribute a label Kingston itself never used, and never
prefixed with the heading name — the column name already conveys that).
`csv`'s own quoting means any commas/quotes inside that JSON string can
never create extra columns, and `json.loads(row["processor"])` /
`json.loads(row["important_configuration_notes"])` always give back the
exact original list. A genuinely-labeled attribute that happens to repeat
within one card (rare) still gets the dynamic single-value-stays-scalar
treatment via `_scalar_or_array()` — that's a different, unrelated case.
See `agent/extraction.py` (`_dedupe`, `_scalar_or_array`, `_format_value`).

Every generated CSV is validated after being written (see
`utils/io_utils.validate_and_log_csv_file`): every data row must have
exactly as many fields as the header, and any cell that looks like a JSON
array must actually parse as one. Any problem is logged, never silently
swallowed — check `logs/kingston_scraper.log` for `CSV validation` if a
run's summary reports anything other than `PASSED`.

Memory and SSD parts are extracted by two different workflows and land in
two different files — their data shapes are different enough (SSD parts
carry a real product page with a capacity x form-factor x part-number
matrix; memory parts don't) that one shared file would just make both
harder to use. Both files share the same column layout (`PART_CSV_COLUMNS`
in `models/models.py`) so they're easy to process the same way, but a
column irrelevant to one type (e.g. `form_factor` for most memory parts)
is simply left blank rather than forced.

Every row in both files carries: `brand`, `server_name`/`server_url` (so
every part traces back to its system unambiguously), `component_type`,
`component_name`/`component_model`, `part_number`, `capacity`,
`form_factor`, **`component_url`** (the actual Kingston HTML product page
— never a PDF), **`datasheet_url`** (the separate spec-sheet PDF, when one
exists), `description`, `specifications` (flat, human-readable), `status`
(Kingston's own availability text, e.g. "Available" / "Discontinued: Get
support" — never overwritten by our internal extraction-outcome codes),
and **`part_specifications_json`**.

### `output/kingston_memory_parts.csv`
One row per compatible memory (ValueRAM) part. Memory parts have **no
dedicated Kingston product page** (confirmed by inspecting real pages —
only a datasheet PDF and a generic support link exist), so
`component_url` is legitimately blank and extraction never leaves the
server's own compatibility page. Kingston doesn't expose memory
attributes (speed, CAS latency, voltage, ECC, module type, ...) as
separate fields for these parts — they only appear in one free-text
description line (e.g. "DDR2 800MT/s ECC Unbuffered DIMM CL6 1.8V
240-pin"), so `agent/memory_extractor.py` tokenizes that line with a set
of known Kingston memory-spec patterns; anything it can't confidently
recognize is left in `description` rather than guessed at.

`part_specifications_json` groups the data into buckets — `General`
(part number/name/model/type), `Specifications` (data-attributes plus the
description-tokenized fields), `Description`, `Compatibility`, `Status` —
each included only when non-empty:

```json
{
  "General": {"Part Number": "KVR800D2E6/1G", "Component Type": "Memory"},
  "Specifications": {
    "Release Date": "05/02/2008 00:00:00.0000",
    "Capacity": "1GB", "Memory Type": "DDR2", "Speed": "800MT/s",
    "Module Type": "DIMM", "ECC": "ECC", "Registered/Unbuffered": "Unbuffered",
    "CAS Latency": "CL6", "Voltage": "1.8V", "Pin Count": "240"
  },
  "Description": {"Description": "DDR2 800MT/s ECC Unbuffered DIMM CL6 1.8V 240-pin"},
  "Compatibility": {"Server": "ABIT- AN9 32X Motherboard", "Server URL": "https://..."},
  "Status": {"Status": "Discontinued: Get support"}
}
```

### `output/kingston_ssd_parts.csv`
**One row per actual purchasable SSD variant** — every real
(capacity, form factor, part number) combination Kingston sells, not one
row per product. A card's "Learn more" link is followed to the real
Kingston product page (`agent/ssd_extractor.py`), which embeds a small
`KCMS.AddToCart.initialize(...)` JSON catalog listing each capacity's and
form factor's own part numbers — the valid variants are exactly the
combinations whose part-number lists intersect (e.g. KC600 2.5" supports
256/512/1024/2048GB while mSATA only supports 256/512/1024GB, so
2048GB+mSATA is correctly never generated). The same product page also
carries the *complete* specification table for every form factor, with
capacity-scoped values (e.g. TBW, sequential read/write) preserved
per-capacity rather than copied blindly across variants.

The same Kingston product commonly appears as a compatible part on many
different servers — `SsdProductCache` fetches and parses each distinct
product URL at most once per run; every server referencing it afterward
reuses that data while still getting its own correctly-scoped rows.

`part_specifications_json` for an SSD row:

```json
{
  "Product": {"Name": "KC600", "Component Type": "Solid-State Drives"},
  "Variant": {"Form Factor": "2.5\"", "Capacity": "2048GB", "Part Number": "SKC600/2048G"},
  "Specifications": {
    "Interface": "SATA Rev. 3.0 (6Gb/s) ...", "Controller": "SM2259", "NAND": "3D TLC",
    "Sequential Read/Write": "up to 550/520MB/s", "Total Bytes Written (TBW)": "1200TB",
    "Dimensions": "100.1mm x 69.85mm x 7mm", "Weight": "40g", "...": "..."
  },
  "Compatibility": {"Server": "...", "Server URL": "https://..."},
  "Status": {"Status": "Available"}
}
```

If following the product page fails or its variant/specification data
can't be parsed, the row falls back to whatever basic info the server's
own compatibility card carries — the component is never dropped — and a
`part_specifications_json["Extraction"]["Result"]` key records which
`PartStatus` outcome happened (`COMPONENT_URL_FAILED` /
`PRODUCT_PAGE_FAILED` / `VARIANT_EXTRACTION_FAILED` /
`SPECIFICATION_EXTRACTION_FAILED`) — kept separate from `Status`, which
always stays Kingston's own availability text.

### `output/kingston_failed_urls.csv`
The **current, unresolved** set of server URLs that couldn't be reliably
processed — not a historical log. A server is removed from this file the
moment a later attempt (initial or automatic retry) successfully persists
its data, and re-added if a later run fails it again. Columns: server
name/URL, failure type (`Timeout` / `Navigation Error` / `Blocked` /
`Unexpected Structure` / `Extraction Error` / `Unknown Error`), reason,
error message, attempt number (1 = initial phase, 2+ = retry rounds),
timestamp.

### `output/kingston_scrape_errors.csv`
The permanent history of **every** failure attempt, resolved or not —
append-only, same columns as `kingston_failed_urls.csv`. Use this when you
need to see what actually happened across a run (including transient
failures that a later retry fixed); use `kingston_failed_urls.csv` to see
what's still broken right now.

## Error handling & statuses

Per-server processing status (tracked in `state/processing_status.json`)
is one of:

- `SERVER_SUCCESS` — specs and/or parts extracted normally.
- `PARTIAL_SUCCESS` — the page loaded but very little structured data came
  back (still written to `kingston_servers.csv`, not silently dropped).
- `SERVER_FAILED` — every retry failed this phase; row written/kept in
  `kingston_failed_urls.csv` (removed automatically if a later automatic
  retry round succeeds).

A failing *component* within an otherwise-good server page never fails the
whole server — parts are extracted independently, and a component-level
failure (most commonly an SSD product-page fetch) still produces a row
(server/part relationship preserved) rather than being dropped or aborting
the rest of the server. These are tracked separately, per component, via
`PartStatus` in `agent/error_handler.py`:

- `SUCCESS`
- `COMPONENT_URL_FAILED` — the card had no usable product-page link at all.
- `PRODUCT_PAGE_FAILED` — a link existed but navigating to it failed.
- `VARIANT_EXTRACTION_FAILED` — the page loaded but its capacity/form-factor
  catalog couldn't be found/parsed.
- `SPECIFICATION_EXTRACTION_FAILED` — variants were found but the
  specification table couldn't be parsed.

## Duplicate handling

All three part/server CSV writers load any keys already on disk at
startup (server URL for `kingston_servers.csv`;
`server_url::part_number` for both `kingston_memory_parts.csv` and
`kingston_ssd_parts.csv`) so re-running the same output files — including
across `--resume` runs and the automatic retry phase — never produces
duplicate rows. The same SSD product referenced by many servers still
produces one row per (server, variant) pair, never one row per server per
product-page-fetch.

## Tests

```bash
python -m pytest tests/ -q
```

Covers the pure-logic pieces (brand/model splitting, server-type
detection, spec-card mapping, part-card parsing/dedup, input CSV reading,
CSV writer dedup) against captured sample HTML fragments — no live network
access required, no live-site flakiness in CI.
