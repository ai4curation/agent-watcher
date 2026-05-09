#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import mine_traces


RUN_URL_RE = re.compile(r"github\.com/[^/\s]+/[^/\s]+/actions/runs/(\d+)")
RUN_NUMBER_RE = re.compile(r"(?:^|[-_/])run(\d+)(?:$|[-_/])", re.I)
ISSUE_NUMBER_RE = re.compile(r"(?:^|[-_/])issue[-_/](\d+)(?:$|[-_/])", re.I)
DEFAULT_WORKFLOWS = ("ai-agent.yml",)


def main() -> int:
    args = parse_args()
    targets = load_targets(args)
    if not targets:
        print("No targets selected.")
        return 1

    out_dir = Path(args.out_dir)
    for target in targets:
        mine_repo_prs(
            repo=target["repo"],
            slug=target["slug"],
            out_dir=out_dir,
            author=args.author,
            workflows=args.workflow or list(DEFAULT_WORKFLOWS),
            limit_prs=args.limit_prs,
            max_workflow_pages=args.max_workflow_pages,
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mine traces for PRs authored by dragon-ai-agent.")
    parser.add_argument("--config", default="", help="agent-watcher config/targets.json path.")
    parser.add_argument("--all-targets", action="store_true", help="Mine every target in --config.")
    parser.add_argument("--target", action="append", default=[], help="Configured repo to include from --config.")
    parser.add_argument("--repo", action="append", default=[], help="Repository owner/name. Repeatable.")
    parser.add_argument("--slug", default="", help="Ontology output slug for a single --repo run.")
    parser.add_argument("--author", default="dragon-ai-agent", help="GitHub PR author login.")
    parser.add_argument("--workflow", action="append", default=[], help="Workflow file/name to map run numbers.")
    parser.add_argument("--out-dir", default="build/dragon-pr-traces", help="Output directory.")
    parser.add_argument("--limit-prs", type=int, default=1000, help="PRs to inspect per repo.")
    parser.add_argument("--max-workflow-pages", type=int, default=100, help="Workflow run pages to scan for run numbers.")
    return parser.parse_args()


def load_targets(args: argparse.Namespace) -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    if args.config:
        config = json.loads(Path(args.config).read_text())
        selected = set(args.target)
        for item in config.get("targets", []):
            repo = item["repo"]
            if not args.all_targets and repo not in selected:
                continue
            targets.append({"repo": repo, "slug": item.get("short_name") or repo.split("/")[-1]})

    for repo in args.repo:
        slug = args.slug if len(args.repo) == 1 and args.slug else repo.split("/")[-1]
        targets.append({"repo": repo, "slug": slug})

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for target in targets:
        if target["repo"] in seen:
            continue
        seen.add(target["repo"])
        deduped.append(target)
    return deduped


def mine_repo_prs(
    *,
    repo: str,
    slug: str,
    out_dir: Path,
    author: str,
    workflows: list[str],
    limit_prs: int,
    max_workflow_pages: int,
) -> None:
    repo_dir = out_dir / slug
    repo_dir.mkdir(parents=True, exist_ok=True)
    prs = list_prs(repo, author, limit_prs)
    wanted_run_numbers = sorted({run_number_from_pr(pr) for pr in prs if run_number_from_pr(pr)})
    run_number_map = map_workflow_run_numbers(repo, workflows, wanted_run_numbers, max_workflow_pages)

    results: list[dict[str, Any]] = []
    for pr in prs:
        result = mine_pr(repo, pr, repo_dir, run_number_map)
        results.append(result)

    index = {
        "repo": repo,
        "slug": slug,
        "author": author,
        "pr_count": len(prs),
        "trace_pr_count": sum(1 for result in results if result["trace_summaries"]),
        "missing_trace_count": sum(1 for result in results if not result["trace_summaries"]),
        "prs": results,
    }
    mine_traces.write_json(repo_dir / "index.json", index)
    print(f"{slug}: prs={len(prs)} traced={index['trace_pr_count']} missing={index['missing_trace_count']}")


def list_prs(repo: str, author: str, limit: int) -> list[dict[str, Any]]:
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--author",
            author,
            "--state",
            "all",
            "--limit",
            str(limit),
            "--json",
            "number,title,state,createdAt,updatedAt,url,headRefName,author,body",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout or "[]")


def mine_pr(
    repo: str,
    pr: dict[str, Any],
    repo_dir: Path,
    run_number_map: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    pr_dir = repo_dir / f"pr-{pr['number']}"
    pr_dir.mkdir(parents=True, exist_ok=True)
    comments_payload = get_pr_context(repo, int(pr["number"]))
    run_ids = sorted(extract_run_ids(pr, comments_payload))
    run_number = run_number_from_pr(pr)
    mapped_run = run_number_map.get(run_number) if run_number else None
    if mapped_run:
        run_ids.append(str(mapped_run["id"]))
    run_ids = sorted(set(run_ids), key=int)

    pr_record = {
        "number": pr["number"],
        "title": pr["title"],
        "state": pr["state"],
        "created_at": pr["createdAt"],
        "updated_at": pr["updatedAt"],
        "url": pr["url"],
        "head_ref": pr["headRefName"],
        "issue_number": issue_number_from_pr(pr),
        "run_number": run_number,
        "run_ids": run_ids,
    }
    mine_traces.write_json(pr_dir / "pr.json", pr_record)

    trace_summaries: list[dict[str, Any]] = []
    errors: list[str] = []
    for run_id in run_ids:
        try:
            run = mine_traces.explicit_runs(repo, [run_id])[0]
            summary = mine_traces.mine_run(repo, run, pr_dir / f"run-{run_id}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{run_id}: {exc}")
            continue
        if summary["trace_record_count"] or summary["artifact_trace_files"]:
            trace_summaries.append(summary)

    result = {
        **pr_record,
        "trace_summaries": trace_summaries,
        "errors": errors,
        "missing_reason": "" if trace_summaries else missing_reason(run_ids, errors),
    }
    mine_traces.write_json(pr_dir / "summary.json", result)
    return result


def get_pr_context(repo: str, number: int) -> dict[str, Any]:
    proc = subprocess.run(
        ["gh", "pr", "view", str(number), "--repo", repo, "--json", "body,comments,reviews"],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout or "{}")


def extract_run_ids(pr: dict[str, Any], context: dict[str, Any]) -> set[str]:
    texts = [pr.get("body") or "", context.get("body") or ""]
    texts.extend(comment.get("body") or "" for comment in context.get("comments", []))
    texts.extend(review.get("body") or "" for review in context.get("reviews", []))
    run_ids: set[str] = set()
    for text in texts:
        run_ids.update(RUN_URL_RE.findall(text))
    return run_ids


def run_number_from_pr(pr: dict[str, Any]) -> int | None:
    match = RUN_NUMBER_RE.search(pr.get("headRefName") or "")
    return int(match.group(1)) if match else None


def issue_number_from_pr(pr: dict[str, Any]) -> int | None:
    match = ISSUE_NUMBER_RE.search(pr.get("headRefName") or "")
    return int(match.group(1)) if match else None


def map_workflow_run_numbers(
    repo: str,
    workflows: list[str],
    wanted_run_numbers: list[int],
    max_pages: int,
) -> dict[int, dict[str, Any]]:
    if not wanted_run_numbers:
        return {}
    wanted = set(wanted_run_numbers)
    found: dict[int, dict[str, Any]] = {}
    for workflow in workflows:
        for page in range(1, max_pages + 1):
            proc = subprocess.run(
                [
                    "gh",
                    "api",
                    "--method",
                    "GET",
                    f"repos/{repo}/actions/workflows/{workflow}/runs",
                    "-f",
                    "per_page=100",
                    "-f",
                    f"page={page}",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0:
                break
            payload = json.loads(proc.stdout or "{}")
            runs = payload.get("workflow_runs", [])
            if not runs:
                break
            for run in runs:
                run_number = run.get("run_number")
                if run_number in wanted:
                    found[run_number] = run
            if wanted.issubset(found):
                return found
    return found


def missing_reason(run_ids: list[str], errors: list[str]) -> str:
    if not run_ids:
        return "no run id found from PR text/comments or branch run number"
    if errors:
        return "run id found but trace retrieval failed or logs/artifacts are no longer retained"
    return "run id found but no trace-like records detected"


if __name__ == "__main__":
    raise SystemExit(main())
