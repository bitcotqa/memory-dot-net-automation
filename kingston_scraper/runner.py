"""Orchestrates one full scrape run: reads the input file, drives the
browser agent across every server URL, extracts server + parts data,
writes CSVs, tracks resumable status, automatically retries failures, and
prints the final summary.

Run shape:

    Phase 1 (initial extraction) — process every server selected for this
    run (respecting --resume/--retry-failed/--max). Successes are written
    immediately; failures are recorded in the current-failures store
    (kingston_failed_urls.csv) and the historical error log
    (kingston_scrape_errors.csv).

    Phase 2 (automatic retry) — once Phase 1 is completely finished, the
    servers that failed *during this run's Phase 1* are retried, up to
    AUTO_RETRY_ATTEMPTS additional full passes. Every server that
    eventually succeeds is written to the output CSVs immediately and
    removed from the current-failures store; anything still failing after
    the last round stays there.

This is the only module that knows about the *sequence* of steps; the
individual steps themselves (navigate, extract server specs, extract
parts) live in agent/.
"""

from datetime import datetime, timezone

from agent.browser_agent import BrowserAgent
from agent.navigation import open_server_page
from agent.extraction import extract_server_specs
from agent.parts_extractor import extract_parts, is_ssd_category
from agent.memory_extractor import build_csv_row as build_memory_csv_row, migrate_legacy_row as migrate_legacy_memory_row
from agent.error_handler import ScrapeError, BlockedError, Status, FailureType
from config.config import (
    SERVERS_CSV_PATH,
    MEMORY_PARTS_CSV_PATH,
    SSD_PARTS_CSV_PATH,
    FAILED_CSV_PATH,
    ERRORS_CSV_PATH,
    MAX_RETRIES,
    RETRY_BACKOFF_SECONDS,
    REQUEST_DELAY_SECONDS,
    COOLDOWN_TRIGGER_STREAK,
    COOLDOWN_SECONDS,
    AUTO_RETRY_ATTEMPTS,
)
from models.models import SERVER_CSV_COLUMNS, PART_CSV_COLUMNS, FAILED_CSV_COLUMNS
from utils.io_utils import (
    read_input_servers,
    CsvWriter,
    DynamicCsvWriter,
    AppendCsvLogger,
    validate_and_log_csv_file,
)
from utils.logger import get_logger
from state.status_store import StatusStore
from state.failed_store import FailedUrlStore
from agent.ssd_extractor import SsdProductCache

import time

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process_one_server(agent, server: dict, debug_label: str, ssd_cache):
    """Navigate + extract for one server. Returns (server_record,
    part_records). Raises ScrapeError if the server itself could not be
    loaded/parsed at all (no partial data possible).

    ssd_cache is threaded through to extract_parts so every SSD product
    page (e.g. KC600) is fetched at most once per run no matter how many
    servers list it as a compatible part — see agent/ssd_extractor.py.
    """
    soup = open_server_page(agent, server["url"], debug_label=debug_label)
    server_record = extract_server_specs(soup, server["name"], server["url"])
    parts = extract_parts(soup, server["name"], server["url"], agent=agent, ssd_cache=ssd_cache)
    return server_record, parts


def _new_phase_counters() -> dict:
    return {
        "successful": 0,
        "partial": 0,
        "failed": 0,
        "total_parts_found": 0,
        "failed_urls": [],
        "succeeded_urls": [],
    }


def _process_servers(
    agent,
    servers: list,
    *,
    phase_attempt_number: int,
    servers_writer,
    memory_parts_writer,
    ssd_parts_writer,
    failed_store,
    errors_logger,
    status_store,
    ssd_cache,
):
    """Process one batch of servers end to end (navigate, extract, write,
    record status) and return a fresh counters dict for just this batch.
    Used for both the initial-extraction phase and every automatic-retry
    round — the per-server logic is identical either way; only which
    servers go in, and what attempt number gets recorded, differs.
    """
    counters = _new_phase_counters()
    consecutive_blocked = 0

    for index, server in enumerate(servers, start=1):
        name, url = server["name"], server["url"]
        debug_label = f"attempt{phase_attempt_number}_server_{index}"

        if consecutive_blocked >= COOLDOWN_TRIGGER_STREAK:
            logger.warning(
                "%d servers in a row failed — cooling down for %.0fs before continuing "
                "(the site appears to be hard-blocking this session rather than a "
                "one-off transient failure; see README 'Known limitation: Cloudflare').",
                consecutive_blocked,
                COOLDOWN_SECONDS,
            )
            time.sleep(COOLDOWN_SECONDS)
            consecutive_blocked = 0  # give it a fresh streak after cooling off

        logger.info("")
        logger.info("Processing %d/%d", index, len(servers))
        logger.info("Server Name: %s", name)
        logger.info("Server URL: %s", url)
        logger.info("Opening URL...")

        attempt = 0
        last_error = None
        server_record = None
        parts = []

        while attempt < MAX_RETRIES:
            attempt += 1
            try:
                server_record, parts = _process_one_server(agent, server, debug_label, ssd_cache)
                last_error = None
                break
            except ScrapeError as exc:
                last_error = exc
                logger.warning(
                    "Attempt %d/%d failed [%s]: %s", attempt, MAX_RETRIES, exc.failure_type, exc.reason
                )
                if attempt < MAX_RETRIES:
                    # Recycle the page after any failure (a blocked
                    # page in particular shouldn't be reused as-is).
                    agent.new_page()
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)
            except Exception as exc:  # noqa: BLE001
                last_error = ScrapeError(FailureType.UNKNOWN, "Unexpected error", str(exc))
                logger.warning("Attempt %d/%d hit an unexpected error: %s", attempt, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES:
                    agent.new_page()
                    time.sleep(RETRY_BACKOFF_SECONDS * attempt)

        if last_error is not None:
            logger.error("FAILED")
            logger.error("Server Name: %s", name)
            logger.error("URL: %s", url)
            logger.error("Failure Type: %s", last_error.failure_type)
            logger.error("Reason: %s", last_error.reason)

            failed_row = {
                "server_name": name,
                "server_url": url,
                "failure_type": last_error.failure_type,
                "failure_reason": last_error.reason,
                "error_message": last_error.error_message,
                "attempt_number": str(phase_attempt_number),
                "timestamp": _now_iso(),
            }
            # Historical log: always appended, regardless of what happens
            # to this URL later.
            errors_logger.append_row(failed_row)
            # Current-failures view: upserted now; only removed once (and
            # if) a later attempt actually persists successful data.
            failed_store.upsert(failed_row)
            status_store.record(url, Status.SERVER_FAILED, {"failure_type": last_error.failure_type})

            counters["failed"] += 1
            counters["failed_urls"].append(url)
            consecutive_blocked += 1
            agent.new_page()
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        consecutive_blocked = 0

        logger.info("Page loaded successfully")
        logger.info("Discovering specifications...")
        spec_count = sum(1 for v in server_record.to_row().values() if v)
        logger.info("Specifications found: %d fields populated", spec_count)

        logger.info("Discovering components...")
        logger.info("Components found: %d", len(parts))

        written_parts = 0
        for part_index, part in enumerate(parts, start=1):
            logger.info(
                "Processing component %d/%d: %s", part_index, len(parts), part.part_number or part.component_name
            )
            if is_ssd_category(part.component_type):
                if ssd_parts_writer.write_row(part.to_row()):
                    written_parts += 1
            else:
                # Memory rows get their own dynamic per-specification-key
                # columns (see agent/memory_extractor.build_csv_row) rather
                # than PartRecord's fixed PART_CSV_COLUMNS shape — that
                # shape stays exactly as-is for SSD rows above.
                if memory_parts_writer.write_row(build_memory_csv_row(part)):
                    written_parts += 1

        server_record.parts_found = str(len(parts))
        servers_writer.write_row(server_record.to_row())
        # memory_parts_writer only ever builds its row set up in memory
        # (its column set can still grow as new specification keys are
        # discovered) — flush it to disk now, at the same per-server
        # granularity as every other persistence point here, so a crash
        # later in the run never loses this server's memory-part rows.
        memory_parts_writer.flush()

        # Data for this server is now durably on disk (servers_writer,
        # ssd_parts_writer append-and-flush per row; memory_parts_writer
        # flushed just above) — only now is it safe to drop it from the
        # current-failures view. If the process crashed anywhere above
        # this line, the server simply stays in kingston_failed_urls.csv
        # from whatever its last recorded failure was, ready to be
        # retried on the next run.
        failed_store.remove(url)

        status = Status.SERVER_SUCCESS if parts or spec_count > 3 else Status.PARTIAL_SUCCESS
        if status == Status.PARTIAL_SUCCESS:
            counters["partial"] += 1
            logger.info("Server completed with partial data (few/no specs or parts found)")
        else:
            counters["successful"] += 1
            logger.info("SUCCESS")

        status_store.record(url, status)
        counters["total_parts_found"] += len(parts)
        counters["succeeded_urls"].append(url)

        time.sleep(REQUEST_DELAY_SECONDS)

    return counters


def run(
    input_path,
    resume: bool = False,
    retry_failed_only: bool = False,
    max_servers: int = 0,
    retry_attempts: int = None,
):
    servers = read_input_servers(input_path)
    total_input = len(servers)

    status_store = StatusStore()
    failed_store = FailedUrlStore(FAILED_CSV_PATH)
    errors_logger = AppendCsvLogger(ERRORS_CSV_PATH, FAILED_CSV_COLUMNS)
    # One cache for the whole run: the same Kingston SSD product (e.g.
    # KC600) commonly appears as a compatible part on many different
    # servers — this ensures its product page is fetched/parsed at most
    # once regardless of how many servers reference it (see
    # agent/ssd_extractor.py SsdProductCache).
    ssd_cache = SsdProductCache()

    retry_attempts = AUTO_RETRY_ATTEMPTS if retry_attempts is None else retry_attempts

    if retry_failed_only:
        # Manual utility path (kept for scripting/debugging): reprocess
        # whatever is currently in kingston_failed_urls.csv, once, with no
        # automatic follow-up retry phase — the normal workflow below
        # (plain `python main.py`) no longer requires this flag at all.
        servers = [s for s in servers if failed_store.is_failed(s["url"])]
        logger.info("Retry-failed mode: %d currently-failed servers to retry", len(servers))
    elif resume:
        before = len(servers)
        servers = [s for s in servers if not status_store.is_done(s["url"])]
        logger.info("Resume mode: skipping %d already-completed servers", before - len(servers))

    if max_servers:
        servers = servers[:max_servers]

    servers_writer = CsvWriter(
        SERVERS_CSV_PATH, SERVER_CSV_COLUMNS, key_fn=lambda r: (r.get("server_url") or "").lower()
    )
    # SSD rows still go through PartRecord.to_row() -> the fixed, snake_case
    # PART_CSV_COLUMNS keys (unchanged from the previous task). Memory rows
    # go through build_memory_csv_row() -> human-readable, capitalized
    # column names (see agent/memory_extractor.build_csv_row) — the two key
    # functions must match each writer's actual row shape, or every row
    # collapses onto the same "missing key" dedup key and gets dropped.
    _ssd_part_key_fn = lambda r: (  # noqa: E731
        f"{(r.get('server_url') or '').lower()}::{(r.get('part_number') or r.get('component_name') or '').lower()}"
    )
    _memory_part_key_fn = lambda r: (  # noqa: E731
        f"{(r.get('Server URL') or '').lower()}::{(r.get('Part Number') or r.get('Component Name') or '').lower()}"
    )
    memory_parts_writer = DynamicCsvWriter(
        MEMORY_PARTS_CSV_PATH, key_fn=_memory_part_key_fn, row_migrator=migrate_legacy_memory_row
    )
    ssd_parts_writer = CsvWriter(SSD_PARTS_CSV_PATH, PART_CSV_COLUMNS, key_fn=_ssd_part_key_fn)

    logger.info("=" * 50)
    logger.info("KINGSTON SCRAPER")
    logger.info("=" * 50)
    logger.info("")
    logger.info(
        "PHASE 1: %s", "MANUAL RETRY OF FAILED URLS" if retry_failed_only else "INITIAL EXTRACTION"
    )
    logger.info("Total servers to process this run: %d (of %d in input file)", len(servers), total_input)

    with BrowserAgent() as agent:
        phase1 = _process_servers(
            agent,
            servers,
            phase_attempt_number=1,
            servers_writer=servers_writer,
            memory_parts_writer=memory_parts_writer,
            ssd_parts_writer=ssd_parts_writer,
            failed_store=failed_store,
            errors_logger=errors_logger,
            status_store=status_store,
            ssd_cache=ssd_cache,
        )

        logger.info("")
        logger.info("=" * 50)
        logger.info("INITIAL EXTRACTION COMPLETE" if not retry_failed_only else "MANUAL RETRY COMPLETE")
        logger.info("=" * 50)
        logger.info("Total servers  : %d", len(servers))
        logger.info("Successful     : %d", phase1["successful"])
        logger.info("Partial        : %d", phase1["partial"])
        logger.info("Failed         : %d", phase1["failed"])

        retry_rounds_run = 0
        retry_candidate_urls = set(phase1["failed_urls"])
        retry_successful_urls = []
        still_failed_urls = set(retry_candidate_urls)

        if not retry_failed_only and retry_attempts > 0 and retry_candidate_urls:
            logger.info("")
            logger.info("Starting automatic retry (%d failed server(s))...", len(retry_candidate_urls))

            servers_by_url = {s["url"]: s for s in servers}
            pending_urls = set(retry_candidate_urls)

            for round_num in range(1, retry_attempts + 1):
                if not pending_urls:
                    break
                retry_rounds_run += 1

                logger.info("")
                logger.info("=" * 50)
                logger.info("PHASE 2: AUTOMATIC RETRY (round %d/%d)", round_num, retry_attempts)
                logger.info("=" * 50)
                logger.info("Retrying %d failed server(s)", len(pending_urls))

                retry_batch = [servers_by_url[u] for u in retry_candidate_urls if u in pending_urls]
                round_result = _process_servers(
                    agent,
                    retry_batch,
                    phase_attempt_number=1 + round_num,
                    servers_writer=servers_writer,
                    memory_parts_writer=memory_parts_writer,
                    ssd_parts_writer=ssd_parts_writer,
                    failed_store=failed_store,
                    errors_logger=errors_logger,
                    status_store=status_store,
                    ssd_cache=ssd_cache,
                )

                retry_successful_urls.extend(round_result["succeeded_urls"])
                pending_urls = set(round_result["failed_urls"])

                logger.info("")
                logger.info(
                    "Retry round %d/%d complete — succeeded: %d, still failed: %d",
                    round_num,
                    retry_attempts,
                    len(round_result["succeeded_urls"]),
                    len(pending_urls),
                )

            still_failed_urls = pending_urls

    # Guarantee kingston_failed_urls.csv exists and reflects the current
    # state even when nothing ever failed this run (header-only / empty),
    # rather than being absent or stale from an unrelated earlier run.
    failed_store.flush()
    # Defensive final flush — every server already flushed memory_parts_writer
    # itself, but this guarantees the file is current even if servers were 0.
    memory_parts_writer.flush()

    # Post-generation validation: read every output CSV back and confirm
    # no row's field count drifted from its header — the concrete,
    # checkable guarantee that no multi-value specification (Processor,
    # Important Configuration Notes, ...) ever spilled into a neighboring
    # column. Never silent: any problem found is logged, not swallowed.
    csv_validation_clean = True
    for csv_path in (SERVERS_CSV_PATH, MEMORY_PARTS_CSV_PATH, SSD_PARTS_CSV_PATH, FAILED_CSV_PATH):
        if not validate_and_log_csv_file(csv_path):
            csv_validation_clean = False

    summary = {
        "total_input_servers": total_input,
        "servers_to_process": len(servers),
        "initial_successful": phase1["successful"],
        "initial_partial": phase1["partial"],
        "initial_failed": phase1["failed"],
        "initial_total_parts_found": phase1["total_parts_found"],
        "auto_retry_rounds_run": retry_rounds_run,
        "retry_candidates": len(retry_candidate_urls),
        "retry_successful": len(retry_successful_urls),
        "retry_failed": len(still_failed_urls),
        "final_successful": phase1["successful"] + phase1["partial"] + len(retry_successful_urls),
        "final_failed": len(still_failed_urls),
        "server_records_written": len(servers_writer.seen_keys),
        "memory_part_records_written": len(memory_parts_writer.seen_keys),
        "ssd_part_records_written": len(ssd_parts_writer.seen_keys),
        "ssd_products_fetched": len(ssd_cache),
        "remaining_failed_urls": len(failed_store),
        "failed_urls": sorted(still_failed_urls),
        "csv_validation_clean": csv_validation_clean,
    }

    _print_summary(summary)
    return summary


def _print_summary(summary: dict):
    logger.info("")
    logger.info("=" * 50)
    logger.info("FINAL KINGSTON SCRAPING SUMMARY")
    logger.info("=" * 50)
    logger.info("")
    logger.info("Initial Servers        : %d", summary["servers_to_process"])
    logger.info("")
    logger.info("Initial Successful     : %d", summary["initial_successful"])
    logger.info("Initial Partial        : %d", summary["initial_partial"])
    logger.info("Initial Failed         : %d", summary["initial_failed"])
    logger.info("")
    logger.info("Automatic Retry Rounds : %d", summary["auto_retry_rounds_run"])
    logger.info("Automatic Retries      : %d", summary["retry_candidates"])
    logger.info("Retry Successful       : %d", summary["retry_successful"])
    logger.info("Retry Failed           : %d", summary["retry_failed"])
    logger.info("")
    logger.info("Final Successful       : %d", summary["final_successful"])
    logger.info("Final Failed           : %d", summary["final_failed"])
    logger.info("")
    logger.info("Server Records         : %d", summary["server_records_written"])
    logger.info("Memory Part Records    : %d", summary["memory_part_records_written"])
    logger.info("SSD Part Records       : %d", summary["ssd_part_records_written"])
    logger.info("SSD Products Fetched   : %d", summary["ssd_products_fetched"])
    logger.info("")
    logger.info("Remaining Failed URLs  : %d", summary["remaining_failed_urls"])
    logger.info("")
    logger.info(
        "CSV Validation         : %s", "PASSED" if summary["csv_validation_clean"] else "FAILED — see warnings above"
    )
    if summary["failed_urls"]:
        logger.info("")
        logger.info("Still-failed URLs:")
        for i, url in enumerate(summary["failed_urls"], start=1):
            logger.info("%d. %s", i, url)
    logger.info("")
    logger.info("Output Files:")
    logger.info("- %s", SERVERS_CSV_PATH)
    logger.info("- %s", MEMORY_PARTS_CSV_PATH)
    logger.info("- %s", SSD_PARTS_CSV_PATH)
    logger.info("- %s", FAILED_CSV_PATH)
    logger.info("- %s", ERRORS_CSV_PATH)
