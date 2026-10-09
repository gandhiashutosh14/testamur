"""Suite-wide rule: a skipped test is not a passed test.

Any skip not on the allow-list below (with its reason) fails the run, so a test that silently stopped running
cannot leave the build green."""
from __future__ import annotations

ALLOWED_SKIPS: dict[str, str] = {
    # "tests/test_x.py::test_y": "why skipping is legitimate",
}


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    unexpected = [r.nodeid for r in reporter.stats.get("skipped", []) if r.nodeid not in ALLOWED_SKIPS]
    if unexpected:
        reporter.write_line(f"FAILED: {len(unexpected)} test(s) skipped without an allow-listed reason:", red=True)
        for nodeid in unexpected:
            reporter.write_line(f"  {nodeid}", red=True)
        session.exitstatus = 1
