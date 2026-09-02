"""Launch/attach to a normal Chrome-family browser and run the scraper.

This is the recommended interactive entry point when Cloudflare Turnstile
loops in a browser process launched directly by Playwright. It starts Chrome
or Edge with a dedicated persistent profile and a local debugging endpoint,
then lets BrowserAgent attach to it. CAPTCHA completion remains manual.

Examples:
    python run_headed.py --resume
    python run_headed.py --retry-failed
    python run_headed.py --resume --max 10
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


BASE_DIR = Path(__file__).resolve().parent


def _browser_candidates():
    configured = os.getenv("BROWSER_EXECUTABLE_PATH", "").strip()
    if configured:
        yield Path(configured)

    if os.name == "nt":
        local_app_data = Path(os.getenv("LOCALAPPDATA", ""))
        program_files = Path(os.getenv("PROGRAMFILES", "C:/Program Files"))
        program_files_x86 = Path(os.getenv("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        yield program_files / "Google/Chrome/Application/chrome.exe"
        yield program_files_x86 / "Google/Chrome/Application/chrome.exe"
        yield local_app_data / "Google/Chrome/Application/chrome.exe"
        yield program_files / "Microsoft/Edge/Application/msedge.exe"
        yield program_files_x86 / "Microsoft/Edge/Application/msedge.exe"
        yield local_app_data / "Microsoft/Edge/Application/msedge.exe"
    else:
        for name in ("google-chrome", "google-chrome-stable", "microsoft-edge", "chromium"):
            resolved = shutil.which(name)
            if resolved:
                yield Path(resolved)


def _find_browser() -> Path:
    for candidate in _browser_candidates():
        if candidate.is_file():
            return candidate.resolve()
    raise SystemExit(
        "No supported Chrome/Edge executable was found. Set "
        "BROWSER_EXECUTABLE_PATH to its full path and try again."
    )


def _cdp_ready(endpoint: str) -> bool:
    try:
        with urlopen(f"{endpoint}/json/version", timeout=1) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _wait_for_cdp(endpoint: str, seconds: float = 15) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _cdp_ready(endpoint):
            return
        time.sleep(0.25)
    raise SystemExit(f"Browser debugging endpoint did not start at {endpoint}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the Kingston scraper through a normal visible Chrome/Edge session",
        add_help=False,
    )
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument(
        "--browser-profile",
        default=str(BASE_DIR / "state" / "manual_browser_profile"),
    )
    parser.add_argument("--help-headed", action="help", help="show headed-runner options")
    return parser.parse_known_args()


def main():
    args, scraper_args = parse_args()
    endpoint = f"http://127.0.0.1:{args.cdp_port}"

    if not _cdp_ready(endpoint):
        browser = _find_browser()
        profile = Path(args.browser_profile).resolve()
        profile.mkdir(parents=True, exist_ok=True)
        print(f"Starting visible browser: {browser}", flush=True)
        subprocess.Popen(
            [
                str(browser),
                f"--remote-debugging-port={args.cdp_port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--new-window",
                "https://www.kingston.com",
            ]
        )
        _wait_for_cdp(endpoint)
    else:
        print(f"Reusing browser already listening at {endpoint}", flush=True)

    print(
        "If Cloudflare asks, complete 'Verify you are human' in the visible browser. "
        "The scraper will continue automatically.",
        flush=True,
    )
    child_env = os.environ.copy()
    child_env["HEADLESS"] = "false"
    child_env["BROWSER_CDP_URL"] = endpoint
    command = [sys.executable, str(BASE_DIR / "main.py"), *(scraper_args or ["--resume"])]
    raise SystemExit(subprocess.run(command, cwd=BASE_DIR, env=child_env).returncode)


if __name__ == "__main__":
    main()
