"""Command-line interface for configuration checks, scans and the local UI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import uvicorn
from pydantic import ValidationError

from jobscout.config import load_config
from jobscout.db import create_db_engine, init_db
from jobscout.scanner import Scanner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobscout", description="Collect and rank local job postings")
    subcommands = parser.add_subparsers(dest="command", required=True)

    config_parser = subcommands.add_parser("config", help="Configuration utilities")
    config_commands = config_parser.add_subparsers(dest="config_command", required=True)
    check = config_commands.add_parser("check", help="Validate the TOML configuration")
    check.add_argument("--config", type=str)

    scan = subcommands.add_parser("scan", help="Collect and rank configured job sources")
    scan.add_argument("--config", type=str)
    scan.add_argument("--profile", type=str)
    scan.add_argument("--source", type=str)

    serve = subcommands.add_parser("serve", help="Start the local web interface")
    serve.add_argument("--config", type=str)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        loaded = load_config(args.config)
        if args.command == "config":
            enabled = sum(source.enabled for source in loaded.settings.sources)
            print(
                f"Configuration valid: {loaded.path} "
                f"({len(loaded.settings.profiles)} profiles, {enabled} enabled sources)"
            )
            return 0

        if args.command == "serve":
            from jobscout.web.app import create_app

            uvicorn.run(create_app(loaded), host=args.host, port=args.port)
            return 0

        engine = create_db_engine(loaded.database_path)
        init_db(engine)
        summary = Scanner(loaded, engine).run(profile_id=args.profile, source_id=args.source)
        print(
            f"Scan {summary.run_id}: {summary.status}; "
            f"sources {summary.sources_succeeded}/{summary.sources_total}; jobs {summary.jobs_saved}"
        )
        for error in summary.errors:
            print(f"Source error: {error}", file=sys.stderr)
        return 0 if summary.status == "success" else (2 if summary.status == "partial" else 1)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
