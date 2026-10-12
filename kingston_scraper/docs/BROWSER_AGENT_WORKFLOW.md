# Kingston Browser Agent Workflow

From one row of `input/server_urls.csv` to finished rows in three output
CSVs — every hop the browser makes, how a page is told apart from a
Cloudflare block, how the two very different part types (memory vs. SSD)
get extracted, and what happens when something fails.

- **Entry point:** one `https://www.kingston.com/en/memory/search/model/<id>/<slug>`
  URL per input row (there is no single Kingston catalog page — see
  [README "How the target site actually works"](../README.md#how-the-target-site-actually-works))
- **Engine:** Playwright / Chromium, one browser for the whole run (`agent/browser_agent.py`)
- **Extraction:** deterministic DOM/JSON parsing — no LLM involved anywhere
- **Output:** `output/kingston_servers.csv`, `output/kingston_memory_parts.csv`,
  `output/kingston_ssd_parts.csv`, plus `kingston_failed_urls.csv` /
  `kingston_scrape_errors.csv` for anything that didn't make it

Orchestration lives in `run()` in [`runner.py`](../runner.py), called from
[`main.py`](../main.py).

---

## The pipeline, end to end

```text
input/server_urls.csv
        │
        ▼
 read_input_servers()  ──── name/url column detection, blank-URL rows skipped
        │
        ▼
 BrowserAgent.start()  ──── one Chromium instance for the whole run
        │
        ▼
 ┌─────────────────────── per server, Phase 1 ───────────────────────┐
 │  open_server_page()                                                │
 │    ├─ navigate, settle (POST_LOAD_SETTLE_MS)                       │
 │    ├─ blocked?  ──yes──► BlockedError (see "Telling a real page    │
 │    │                      apart from a block" below)               │
 │    ├─ bad HTTP status with no real content? ──► ScrapeError        │
 │    └─ return parsed BeautifulSoup                                  │
 │        │                                                            │
 │        ▼                                                            │
 │  extract_server_specs()  ── System Information cards → ServerRecord│
 │        │                                                            │
 │        ▼                                                            │
 │  extract_parts()  ── one card per compatible part, dispatched by    │
 │        │              category:                                    │
 │        ├─ memory card  → extract_memory_part()  (no 2nd page)      │
 │        └─ SSD card     → follow "Learn more" → SsdProductCache →   │
 │                           fetch_product_variants() (cached per URL) │
 │        │                                                            │
 │        ▼                                                            │
 │  write server row + part rows, flush, validate, drop from          │
 │  failed-URL store if it was there                                   │
 └──────────────────────────────────────────────────────────────────┘
        │
        ▼
 Phase 2: automatic retry — whatever failed in Phase 1, re-run through
 the exact same per-server logic above, up to AUTO_RETRY_ATTEMPTS rounds
        │
        ▼
 validate_and_log_csv_file() on every output CSV
        │
        ▼
 final summary logged + returned
```

---

## The stages

### 1. Read the input file

`utils/io_utils.py::read_input_servers()` matches the name/URL columns
case-insensitively (`name`/`server_name`/`server name`,
`url`/`server_url`/`server url`), skips any row with a blank URL, and
assumes nothing about columns beyond those two.

*File:* `utils/io_utils.py`

### 2. Launch the browser

One `BrowserAgent` (Playwright/Chromium) is started for the entire run —
not one per server. It sets a desktop Chrome user agent, a 1440×900
viewport, patches `navigator.webdriver`, and disables Playwright's
automation-controlled flag. `HEADLESS` is configurable; a visible browser
is generally less fingerprintable to bot management than headless.

*File:* `agent/browser_agent.py`

### 3. Open one server page and settle

`agent/navigation.py::open_server_page()` navigates, waits
`POST_LOAD_SETTLE_MS`, then reads the **rendered** title/HTML rather than
trusting the raw HTTP status — Cloudflare's invisible challenge sometimes
answers the initial request with a stale `403` and then silently swaps in
the real page once the JS challenge auto-resolves, so a `403` is only
fatal if the content itself also looks wrong (see next section).

An optional, off-by-default pass (`ENABLE_EXPLORATORY_CLICKS`) can nudge
any collapsed accordion/tab/"load more" control — it exists only as a
fallback for a page shape not yet seen; nothing in extraction depends on
it, because everything is already present in the initial server-rendered
HTML (confirmed by inspecting real pages before writing any selector).

*File:* `agent/navigation.py`

#### Telling a real page apart from a block

| Check | What it catches |
|---|---|
| Title contains "just a moment", "attention required", "access denied", "are you a human" | Cloudflare interstitial titles |
| Page ≤ 20KB **and** contains a strong marker ("performing security verification", "cf-chl-widget", "cf-turnstile-response", ...) | The actual challenge holding-page body — length-gated so these strings appearing deep inside a large, fully-rendered real page (which legitimately loads Cloudflare's JS SDK for unrelated widgets) never false-positives |
| HTTP ≥ 400 **and** no real-content marker (`c-configuratorresultscard`, `product-gallery-card`, `s-productdetails`) present | A genuinely broken/removed URL — checked by content marker, not size alone, since Kingston's own "not found" page still renders full site chrome and would otherwise look "large enough" to be real |

A block raises `BlockedError` (subclass of `ScrapeError`,
`FailureType.BLOCKED`); a confirmed-broken URL raises
`ScrapeError(FailureType.NAVIGATION, ...)`. Both flow into the same
retry/cooldown/failed-URL machinery in stage 8 — extraction never sees a
blocked or broken page.

*File:* `agent/navigation.py`

### 4. Extract server-level specifications

`agent/extraction.py::extract_server_specs()` is strictly two-pass:

1. **`build_sections(soup)`** parses every `.c-configuratorResultsCard`
   into an ordered `{category_heading: {attribute: value}}` dict, keyed by
   *that card's own* `<h3>` text (Memory, Storage, Expansions,
   CPU/Chipsets, Upgrade Path, Important Configuration Notes, ...). Two
   cards sharing a generic attribute name (both "Memory" and "Storage"
   having a "Maximum") never collide — the attribute lives inside its own
   card's dict the whole time. This dict is preserved byte-for-byte as
   `specifications_json`.
2. Only afterward does a small heading→field lookup
   (`_HEADING_FIELD_MAP`) copy each category's already-scoped data into
   the matching legacy flat column (`memory`, `storage`, `processor`,
   `important_configuration_notes`, ...) — applied to the *heading*,
   never to an attribute name. A heading with no dedicated column is still
   fully captured in `specifications_json`; nothing is silently dropped.

A card whose list items carry no `<h4>` label at all (CPU/Chipsets,
Important Configuration Notes) collects into a synthetic `"Details"`
bucket — always a list, even for one item, since it represents "a list of
independent items on this card" rather than a single labeled attribute.

*File:* `agent/extraction.py`

### 5. Discover compatible parts and dispatch by type

`agent/parts_extractor.py::extract_parts()` walks every
`li.product-gallery-card` on the page (across every tab — ValueRAM,
Solid-state drives, ...) and decides per card, by its category text
(`is_ssd_category()`), which of two genuinely different extraction
workflows to hand it to. This dispatcher holds no spec-parsing logic of
its own.

*File:* `agent/parts_extractor.py`

### 6. Memory parts — no second page exists

Memory (ValueRAM) compatibility cards carry **no dedicated Kingston
product page** — confirmed by inspecting real pages, only a datasheet PDF
and a generic support link exist. `agent/memory_extractor.py` tokenizes
the card's free-text description (e.g. `"DDR2 800MT/s ECC Unbuffered DIMM
CL6 1.8V 240-pin"`) against a set of known Kingston memory-spec patterns
(capacity, memory type, speed, CAS latency, voltage, module type, ECC,
registered/unbuffered, pin count) — a pattern that doesn't match is simply
left out, never guessed at. Because different memory parts genuinely
expose different attribute sets, `kingston_memory_parts.csv` gets a
dynamic column per specification key discovered (`DynamicCsvWriter`,
stage 8) rather than one flat string.

*File:* `agent/memory_extractor.py`

### 7. SSD parts — follow the real product page, once per product

SSD/branded-product cards carry a real "Learn more" link. Its target page
embeds a small `KCMS.AddToCart.initialize(...)` JSON payload listing every
Capacity and Form Factor option, each with its own `PartNumbers` array —
the valid (capacity, form factor) combinations Kingston actually sells are
exactly the ones whose part-number lists intersect (e.g. KC600 2048GB
never appears in mSATA's list, so that combination is correctly never
generated — no clicking or guessing involved, it's static server-rendered
JSON). The same page also carries a complete specification table per form
factor, with capacity-scoped rows (`"512GB — 150TB"`, or a range like
`"512GB–2048GB — ..."`) resolved per variant rather than copied blindly
across capacities.

The same product (e.g. KC600) commonly appears as a compatible part on
dozens of different servers — `SsdProductCache` fetches and parses each
distinct product URL **at most once per run**; every later server
referencing it reuses the cached result while still getting its own
correctly-scoped `PartRecord` rows.

If the product-page fetch or its parsing fails at any stage, the
component still gets a row (server/part relationship preserved) built
from whatever the compatibility card itself carries, with the specific
failure recorded under `part_specifications_json["Extraction"]["Result"]`
(`PartStatus.COMPONENT_URL_FAILED` / `PRODUCT_PAGE_FAILED` /
`VARIANT_EXTRACTION_FAILED` / `SPECIFICATION_EXTRACTION_FAILED`) — kept
separate from `status`, which always stays Kingston's own availability
text, never an internal error code.

*File:* `agent/ssd_extractor.py`

### 8. Write, dedup, and validate

Three writer types in `utils/io_utils.py`, chosen per output's needs:

- **`CsvWriter`** (servers, SSD parts) — append-and-flush per row, loads
  existing keys at startup so a resumed run never double-writes, and
  **heals schema drift**: if the on-disk header no longer matches the
  current column set, every existing row is re-read by column *name* and
  the whole file rewritten under the current schema before anything new
  is appended — never allowing the header and the data beneath it to
  silently misalign.
- **`DynamicCsvWriter`** (memory parts) — column set discovered as rows
  arrive (different memory parts expose different spec keys); holds
  everything in memory and rewrites the whole file on `flush()`, called
  once per server rather than per row.
- **`AppendCsvLogger`** (`kingston_scrape_errors.csv`) — pure append,
  keeps every attempt including ones a later retry fixed.

`state/failed_store.py::FailedUrlStore` is the current, *unresolved*
failure set — a server is removed the instant its data is durably
written, and re-added if it fails again later; it is rewritten atomically
(temp file + rename) on every mutation so a mid-run crash can't lose or
corrupt it.

Every output CSV is read back and validated after being written
(`validate_and_log_csv_file`): every data row must have exactly as many
fields as the header, and any cell that looks like a JSON array must
actually parse as one. Logged, never silently swallowed.

*Files:* `utils/io_utils.py`, `state/failed_store.py`

### 9. Failure handling, cooldown, and automatic retry

Every server gets up to `MAX_RETRIES` immediate attempts (linear backoff,
`RETRY_BACKOFF_SECONDS × attempt`) before being recorded as failed for
this phase. Real-world runs showed a naive fixed-cadence retry never
adapting — once Cloudflare starts hard-blocking, it blocks *every*
subsequent server at the same cadence indefinitely — so a **circuit
breaker** tracks a consecutive-failure streak: after
`COOLDOWN_TRIGGER_STREAK` servers fail in a row, the runner pauses
`COOLDOWN_SECONDS` before continuing, instead of hammering at the same
pace forever.

A run is two phases (`runner.py::run()`):

- **Phase 1 (initial extraction)** — every server selected by
  `--input`/`--resume`/`--max`.
- **Phase 2 (automatic retry)** — once Phase 1 finishes completely, this
  run's own failures are retried, up to `--retry-attempts` (default
  `AUTO_RETRY_ATTEMPTS`) additional full passes. A server that succeeds on
  any round is written immediately and removed from the failed-URL store;
  one still failing after the last round stays there.

A failing *component* (almost always an SSD product-page fetch) never
fails the whole server — it's tracked independently via `PartStatus` (see
stage 7) and still produces a row.

*Files:* `runner.py`, `agent/error_handler.py`, `config/config.py`

### 10. Final summary

`runner.py::_print_summary()` logs initial vs. retry vs. final
successful/failed counts, records written per output file, SSD products
fetched (cache size), remaining failed URLs, and whether CSV validation
passed — everything needed to confirm a run's health without opening the
CSVs by hand.

---

## Two extractor types, side by side

| | Memory parts | SSD parts |
|---|---|---|
| Data source | The compatibility card only | A separate, real Kingston product page |
| Extra navigation | None | One fetch per distinct product URL (cached) |
| Spec source | Free-text description, tokenized | Server-rendered JSON catalog + HTML spec table |
| Rows per card | Exactly one | One per real (capacity × form factor × part number) variant |
| CSV | `kingston_memory_parts.csv` (dynamic columns) | `kingston_ssd_parts.csv` (fixed `PART_CSV_COLUMNS`) |
| Extraction module | `agent/memory_extractor.py` | `agent/ssd_extractor.py` |

---

## Status taxonomy

**Server-level** (`agent/error_handler.py::Status`, tracked in
`state/status_store.py` for `--resume`):

- `SERVER_SUCCESS` — specs and/or parts extracted normally.
- `PARTIAL_SUCCESS` — page loaded but very little structured data came
  back (still written, never silently dropped).
- `SERVER_FAILED` — every retry this phase failed.

**Component-level** (`agent/error_handler.py::PartStatus`, SSD-relevant —
memory parts have no second page to fail against):

`SUCCESS` → `COMPONENT_URL_FAILED` → `PRODUCT_PAGE_FAILED` →
`VARIANT_EXTRACTION_FAILED` → `SPECIFICATION_EXTRACTION_FAILED`, each one
level deeper into the SSD product-page pipeline than the last.

---

## Related tooling

- **`refresh_cookies.py`** *(removed)* — an earlier attempt at reusing a
  human-verified browser session's cookies to reduce blocking. Real-world
  testing showed Kingston's protection scores the *live* session's request
  behavior, not just a one-time "already verified" stamp, so a copied
  cookie didn't hold up — see README "Known limitation: Cloudflare" for
  the full account.
- **`audit/run_audit.py`** — a separate, occasional data-freshness job: re-
  fetches a rotating sample of URLs already represented in the output
  CSVs, re-extracts them with these exact same functions
  (`extract_server_specs` / `extract_parts`), and diffs the fresh result
  against what's on disk — a recurring QA check on *already-shipped*
  output, distinct from `tests/test_live_playwright_extraction.py`'s
  one-fixture-URL correctness check run when extraction logic changes.

## See also

- [README.md](../README.md) — setup, CLI usage, full output-column
  reference, the Cloudflare limitation in detail, duplicate handling.
- [`tests/`](../tests/) — unit tests against captured fixture HTML
  (`tests/fixtures/`) for every stage above, no live network required.
