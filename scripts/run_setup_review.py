#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_watcher.github_api import GitHubClient
from agent_watcher.setup_review import generate_setup_review_reports, write_setup_review_reports


def main() -> int:
    args = _parse_args()
    source_token = os.getenv("WATCHER_SOURCE_TOKEN") or os.getenv("GITHUB_TOKEN")
    client = GitHubClient(source_token)
    only_repos = set(args.target) if args.target else None

    reports = generate_setup_review_reports(client, args.config, only_repos=only_repos)
    if not reports:
        raise SystemExit("No setup-review targets matched the supplied configuration and filters.")

    write_setup_review_reports(args.output_dir, reports)

    print(f"Wrote {len(reports)} setup review report(s) to {Path(args.output_dir).resolve()}")
    for report in reports:
        print(
            f"- {report.repo}: "
            f"instructions={len(report.instruction_files)} "
            f"assets={len(report.asset_directories)} "
            f"agent_workflows={len(report.agent_workflows)} "
            f"findings={len(report.findings)} "
            f"errors={len(report.errors)}"
        )
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect cross-repo agentic setup review context.")
    parser.add_argument("--config", default="config/targets.json", help="Path to watcher config JSON.")
    parser.add_argument("--output-dir", default="build/setup-review", help="Directory for markdown and JSON output.")
    parser.add_argument(
        "--target",
        action="append",
        default=None,
        help="Include only selected configured repo(s); repeat the flag to add more repos.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
