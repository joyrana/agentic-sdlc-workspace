#!/usr/bin/env python3
"""Drive the first vertical slice over HTTP against a running deployment.

    python3 scripts/smoke_slice.py --base-url http://localhost:8080

Uses only the standard library and the development sign-in tokens, so it runs against the
docker compose stack or a local `make api` + `make worker` setup. It exits non-zero at the first
step that does not behave as specified and prints what it saw. It never approves anything with an
agent identity: every decision is made with a human role token, as in the product.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BRD = ROOT / "samples" / "documents" / "task-title-validation-brd.docx"
TOKENS = {
    "owner": "dev_owner",
    "engineer": "dev_engineer",
    "reviewer": "dev_reviewer",
    "qa": "dev_qa",
    "approver": "dev_approver",
}


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/") + "/api/v1"

    def call(
        self,
        method: str,
        path: str,
        who: str,
        body: Any = None,
        raw: bytes | None = None,
        content_type: str = "application/json",
    ) -> tuple[int, Any]:
        data = (
            raw
            if raw is not None
            else (json.dumps(body).encode() if body is not None else None)
        )
        req = urllib.request.Request(self.base + path, data=data, method=method)  # noqa: S310
        req.add_header("Authorization", f"Bearer {TOKENS[who]}")
        if data is not None:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - operator-supplied URL
                text = resp.read().decode()
                return resp.status, json.loads(text) if text else None
        except urllib.error.HTTPError as e:
            text = e.read().decode()
            try:
                return e.code, json.loads(text)
            except json.JSONDecodeError:
                return e.code, text


def step(n: str, msg: str) -> None:
    print(f"[{n}] {msg}", flush=True)


def expect(cond: bool, what: str, seen: Any = None) -> None:
    if not cond:
        print(
            f"FAILED: {what}\n  saw: {json.dumps(seen, indent=2, default=str)[:2000]}",
            file=sys.stderr,
        )
        sys.exit(1)


def wait_for(fn: Any, what: str, timeout: float = 300.0) -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(2)
    expect(False, f"timed out waiting for {what}", last)


def multipart(
    fields: dict[str, str], name: str, filename: str, content: bytes, ctype: str
) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base-url", default="http://localhost:8080")
    ap.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="seconds to wait for each agent stage",
    )
    args = ap.parse_args()
    api = Api(args.base_url)

    status, p = api.call(
        "POST", "/projects", "owner", {"name": f"Smoke {int(time.time())}"}
    )
    expect(status == 201, "create project", p)
    pid = p["id"]
    step("1", f"project {p['key']} created")
    for email, role in [
        ("engineer@example.dev", "engineer"),
        ("reviewer@example.dev", "reviewer"),
        ("qa@example.dev", "qa"),
        ("approver@example.dev", "release_approver"),
    ]:
        status, body = api.call(
            "POST", f"/projects/{pid}/members", "owner", {"email": email, "role": role}
        )
        expect(status == 201, f"add member {email}", body)

    body_bytes, ctype = multipart(
        {"kind": "brd"},
        "file",
        BRD.name,
        BRD.read_bytes(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    status, body = api.call(
        "POST",
        f"/projects/{pid}/documents",
        "engineer",
        raw=body_bytes,
        content_type=ctype,
    )
    expect(status == 202, "upload BRD", body)
    step("2", "BRD uploaded")

    def requirements() -> Any:
        _, reqs = api.call("GET", f"/projects/{pid}/requirements", "reviewer")
        return reqs if isinstance(reqs, list) and len(reqs) >= 5 else None

    reqs = wait_for(requirements, "requirement extraction", args.timeout)
    expect(
        all(r["locator"].get("block_id") for r in reqs),
        "every requirement cites its source",
        reqs,
    )
    step("3", f"{len(reqs)} requirements extracted with source locators")

    nfr = next(r for r in reqs if r["key"] == "NFR-001")
    crit = nfr["criteria"][0]
    status, body = api.call(
        "PATCH",
        f"/projects/{pid}/acceptance-criteria/{crit['id']}",
        "reviewer",
        {
            "expected_version": crit["version"],
            "text": "Creating a task completes in under 50 ms.",
        },
    )
    expect(status == 200, "edit acceptance criterion", body)
    step("4", "acceptance criterion edited by a reviewer")

    status, repo = api.call(
        "POST",
        f"/projects/{pid}/repositories",
        "owner",
        {
            "name": "sample-task-service",
            "kind": "fixture",
            "location": "sample-task-service",
        },
    )
    expect(status == 201, "register sample repository", repo)
    status, r = api.call(
        "POST", f"/projects/{pid}/runs", "engineer", {"repository_id": repo["id"]}
    )
    expect(status == 202, "start delivery run", r)
    run_id = r["id"]
    step("5-6", "delivery run started")

    def run() -> dict[str, Any]:
        return api.call("GET", f"/projects/{pid}/runs/{run_id}", "owner")[1]

    def pending(who: str, gate: str) -> Any:
        _, items = api.call("GET", f"/projects/{pid}/approvals", who)
        found = (
            [a for a in items if a["gate"] == gate and a["status"] == "pending"]
            if isinstance(items, list)
            else []
        )
        if not found and run().get("status") in ("escalated", "failed", "cancelled"):
            stopped = run()
            expect(
                False,
                f"run {stopped['status']} before the {gate} gate",
                stopped.get("outcome", {}).get("escalation") or stopped,
            )
        return found[0] if found else None

    def decide(who: str, req: dict[str, Any], comment: str) -> None:
        status, body = api.call(
            "POST",
            f"/projects/{pid}/approvals/{req['id']}/decision",
            who,
            {"decision": "approved", "comment": comment},
        )
        expect(status == 200, f"{who} approves {req['gate']}", body)

    arch = wait_for(
        lambda: pending("reviewer", "architecture"),
        "architecture approval request",
        args.timeout,
    )
    _, backlog = api.call("GET", f"/projects/{pid}/backlog", "owner")
    stories = [i for i in backlog["items"] if i["kind"] == "story"]
    expect(
        len(stories) == 5 and backlog["analysis"]["valid"],
        "epic with dependent stories",
        backlog,
    )
    step(
        "5-7",
        f"{len(stories)} stories planned; architecture proposal v{arch['artifact_version']} awaits review",
    )
    decide("reviewer", arch, "Smoke test review")
    step("8", "architecture approved by a human reviewer")
    scope = wait_for(
        lambda: pending("owner", "implementation_scope"),
        "implementation scope approval",
        args.timeout,
    )
    decide("owner", scope, "Smoke test scope")

    release = wait_for(
        lambda: pending("approver", "release_staging"),
        "release approval request",
        args.timeout,
    )
    r = run()
    _, defects = api.call("GET", f"/projects/{pid}/defects?run_id={run_id}", "qa")
    step(
        "9-12",
        f"attempts used: {r['counters']['attempt']}; defects: "
        + ", ".join(f"{d['criterion_key']}={d['status']}" for d in defects),
    )
    _, rel = api.call(
        "GET", f"/projects/{pid}/releases/{release['artifact_id']}", "approver"
    )
    expect(
        rel["readiness"]["eligible"] is True, "release is eligible", rel["readiness"]
    )
    step(
        "13",
        f"release {rel['version']} eligible; warnings: {len(rel['readiness']['warnings'])}",
    )
    status, body = api.call(
        "POST",
        f"/projects/{pid}/approvals/{release['id']}/decision",
        "owner",
        {"decision": "approved", "comment": "not my role"},
    )
    expect(status == 403, "a non-approver cannot approve the release", body)
    decide("approver", release, "Smoke test release")
    step("14", "release approved by a release approver")

    final = wait_for(
        lambda: (
            lambda x: x if x["status"] in ("completed", "escalated", "failed") else None
        )(run()),
        "run completion",
        args.timeout,
    )
    expect(final["status"] == "completed", "run completes", final)
    _, rel = api.call(
        "GET", f"/projects/{pid}/releases/{release['artifact_id']}", "approver"
    )
    dep = rel["deployments"][0]
    expect(
        rel["status"] == "deployed_staging" and dep["status"] == "succeeded",
        "staging deployment",
        rel,
    )
    step(
        "15",
        f"deployed to staging via {dep['adapter']}; production: {final['outcome'].get('production')}",
    )
    print("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
