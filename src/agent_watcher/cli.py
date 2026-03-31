from __future__ import annotations

import argparse
import os
from pathlib import Path

from .config import load_targets
from .github_api import GitHubClient
from .reporting import write_reports
from .watcher import scan_target, utc_now


def main() -> int:
    args = _parse_args()
    targets = load_targets(
        args.config,
        lookback_days=args.lookback_days,
        max_items=args.max_items,
        only_repo=args.target,
    )
    if not targets:
        raise SystemExit("No targets matched the supplied configuration and filters.")

    generated_at = utc_now()
    source_token = os.getenv("WATCHER_SOURCE_TOKEN") or os.getenv("GITHUB_TOKEN")
    source_client = GitHubClient(source_token)

    reports = [scan_target(source_client, target, generated_at=generated_at) for target in targets]
    write_reports(args.output_dir, reports)

    print(f"Wrote {len(reports)} report(s) to {Path(args.output_dir).resolve()}")
    for report in reports:
        print(
            f"- {report.target.repo}: "
            f"agent_items={report.metrics.get('agent_items', 0)} "
            f"summons={report.metrics.get('agent_summons', 0)} "
            f"errors={len(report.errors)}"
        )

    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan watched repositories for agent activity.")
    parser.add_argument("--config", default="config/targets.json", help="Path to watcher config JSON.")
    parser.add_argument("--output-dir", default="build/reports", help="Directory for markdown and JSON output.")
    parser.add_argument("--lookback-days", type=int, default=None, help="Override lookback window for all targets.")
    parser.add_argument("--max-items", type=int, default=None, help="Override max items scanned per target.")
    parser.add_argument("--target", default=None, help="Scan only one configured repo, e.g. owner/name.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Accepted for readability in workflows and local runs; no publish side effects exist in the CLI.",
    )
    return parser.parse_args()
