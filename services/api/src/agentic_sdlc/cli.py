"""Command line entry point: `agentic-sdlc <command>`."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentic-sdlc")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="seed agent accounts and (in development) dev users")
    api = sub.add_parser("api", help="run the API server")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    w = sub.add_parser("worker", help="run the workflow worker")
    w.add_argument("--once", action="store_true", help="process available jobs then exit")
    sub.add_parser("openapi", help="print the OpenAPI document")
    args = parser.parse_args(argv)

    if args.cmd == "seed":
        from agentic_sdlc.core.db import session_scope
        from agentic_sdlc.seed import seed_agents, seed_dev_users

        with session_scope() as db:
            seed_agents(db)
            users = seed_dev_users(db)
        for user, token in users:
            print(f"{user.email:28} token={token}")
        return 0
    if args.cmd == "api":
        import uvicorn

        uvicorn.run("agentic_sdlc.api.main:app", host=args.host, port=args.port)
        return 0
    if args.cmd == "worker":
        from agentic_sdlc.workflows.worker import run_worker

        run_worker(once=args.once)
        return 0
    if args.cmd == "openapi":
        from agentic_sdlc.api.app import create_app

        json.dump(create_app().openapi(), sys.stdout, indent=2)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
