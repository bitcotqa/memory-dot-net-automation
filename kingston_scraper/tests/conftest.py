"""Shared pytest config. Registers the 'live' marker used by
test_live_playwright_extraction.py so pytest doesn't warn about an unknown
marker when that module is collected (it self-skips via pytestmark unless
opted into — see that file's docstring)."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: hits the real kingston.com over the network via Playwright"
    )
