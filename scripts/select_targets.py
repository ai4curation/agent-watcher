#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_watcher.config import load_targets
from agent_watcher.scheduling import build_target_run_metadata, repo_slug, target_is_due


def main() -> int:
    args = parse_args()
    now = _resolve_now(args.now_utc)
    targets = load_targets(args.config)

    if args.target_repo:
        targets = [target for target in targets if target.repo == args.target_repo]
        if not targets:
            raise SystemExit(f"No configured target matched: {args.target_repo}")
    elif args.event_name == "schedule":
        targets = [target for target in targets if target_is_due(target, now)]

    matrix = {"include": []}
    for target in targets:
        run_metadata = build_target_run_metadata(target, now)
        matrix["include"].append(
            {
                "repo": target.repo,
                "display_name": target.display_name,
                "short_name": target.short_name,
                "slug": repo_slug(target.repo),
                "report_date": run_metadata.report_date,
                "issue_title": run_metadata.issue_title,
                "extra_prompt": target.extra_prompt,
            }
        )

    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(matrix, indent=2) + "\n")

    github_output = os.getenv("GITHUB_OUTPUT")
    if github_output:
        with Path(github_output).open("a", encoding="utf-8") as handle:
            print(f"matrix={json.dumps(matrix)}", file=handle)
            print(f"has_targets={'true' if matrix['include'] else 'false'}", file=handle)
            print(f"target_count={len(matrix['include'])}", file=handle)

    print(json.dumps(matrix))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select watcher targets for the current run.")
    parser.add_argument("--config", default="config/targets.json")
    parser.add_argument("--event-name", default="workflow_dispatch")
    parser.add_argument("--target-repo", default="")
    parser.add_argument("--output-file", default="build/selector/matrix.json")
    parser.add_argument("--now-utc", default="")
    return parser.parse_args()


def _resolve_now(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
