"""Entry point for the standalone Kingston browser scraping agent.

    python main.py                                  # process every URL in input/server_urls.csv,
                                                      # then automatically retry whatever failed
    python main.py --input path/to/file.csv          # use a different input file
    python main.py --resume                          # skip servers already marked done
    python main.py --max 10                           # cap how many servers this run processes
    python main.py --retry-attempts 3                  # override the number of automatic retry rounds
    python main.py --retry-failed                    # manual utility: reprocess only the servers
                                                      # currently in kingston_failed_urls.csv, once,
                                                      # with no further automatic retry afterward
                                                      # (not needed for the normal workflow above)
"""

import argparse

from config.config import DEFAULT_INPUT_PATH, AUTO_RETRY_ATTEMPTS


def parse_args():
    parser = argparse.ArgumentParser(description="Kingston server/parts browser scraper")
    parser.add_argument(
        "--input", default=str(DEFAULT_INPUT_PATH), help="Path to the input CSV (name,url columns)"
    )
    parser.add_argument(
        "--resume", action="store_true", help="Skip servers already marked SERVER_SUCCESS/PARTIAL_SUCCESS"
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "Manual utility: process only URLs currently in kingston_failed_urls.csv, once, with no "
            "automatic follow-up retry phase. Not required for the normal workflow — a plain run "
            "already retries its own failures automatically."
        ),
    )
    parser.add_argument("--max", type=int, default=0, help="Cap the number of servers processed this run (0 = no cap)")
    parser.add_argument(
        "--retry-attempts",
        type=int,
        default=None,
        help=(
            "Number of automatic retry rounds run against this run's own failed servers after the "
            f"initial extraction phase finishes (default: {AUTO_RETRY_ATTEMPTS}, from AUTO_RETRY_ATTEMPTS). "
            "Set to 0 to disable automatic retry."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()

    from runner import run

    summary = run(
        args.input,
        resume=args.resume,
        retry_failed_only=args.retry_failed,
        max_servers=args.max,
        retry_attempts=args.retry_attempts,
    )

    print("\n--- Run summary ---")
    for key, value in summary.items():
        if key == "failed_urls":
            continue
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
