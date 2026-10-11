"""Pytest plugin copied into sandbox workspaces to record machine-readable test evidence.

Loaded with `-p asw_evidence`. Writes one JSON line per test to $ASW_EVIDENCE_FILE with the node id,
outcome, the acceptance criteria named by `@pytest.mark.criterion(...)` markers, and a short failure
message. Standard library only: it must run inside the sandbox toolchain.
"""

import json
import os


def pytest_configure(config):  # type: ignore[no-untyped-def]
    config.addinivalue_line("markers", "criterion(key): acceptance criterion verified by this test")


def pytest_runtest_setup(item):  # type: ignore[no-untyped-def]
    keys = sorted({m.args[0] for m in item.iter_markers(name="criterion") if m.args})
    item.user_properties.append(("criteria", keys))


def pytest_runtest_logreport(report):  # type: ignore[no-untyped-def]
    # Record the test call, or a setup failure (the call never happens in that case).
    if report.when != "call" and not (report.when == "setup" and report.failed):
        return
    message = ""
    if report.failed and report.longrepr:
        lines = [ln for ln in str(report.longrepr).splitlines() if ln.strip()]
        message = (lines[-1] if lines else "")[:500]
    record = {
        "nodeid": report.nodeid,
        "outcome": report.outcome if report.when == "call" else "error",
        "duration_s": round(report.duration, 4),
        "criteria": dict(report.user_properties).get("criteria", []),
        "message": message,
    }
    with open(os.environ.get("ASW_EVIDENCE_FILE", "asw-evidence.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
