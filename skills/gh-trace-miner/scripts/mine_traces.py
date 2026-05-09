#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any


TRACE_ARTIFACT_RE = re.compile(r"(claude-response|claude-execution|trace|execution-output)", re.I)
TRACE_MARKER_RE = re.compile(r'"(session_id|tool_use_result|total_cost_usd|type)"|Log saved to .*claude-execution-output', re.I)
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s*")
DEFAULT_WORKFLOWS = ("ai-agent.yml",)


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    targets = load_targets(args)

    if not targets:
        print("No targets selected.", file=sys.stderr)
        return 1

    for target in targets:
        mine_target(
            repo=target["repo"],
            slug=target["slug"],
            out_dir=out_dir,
            workflows=args.workflow or list(DEFAULT_WORKFLOWS),
            run_ids=args.run_id,
            limit_runs=args.limit_runs,
            max_samples=args.max_samples,
            all_runs=args.all_runs,
            max_pages=args.max_pages,
            from_artifacts=args.from_artifacts,
            skip_artifact_check=args.skip_artifact_check,
        )

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mine AI-agent traces from GitHub Actions artifacts or logs.")
    parser.add_argument("--repo", action="append", default=[], help="Repository owner/name. Repeatable.")
    parser.add_argument("--slug", default="", help="Ontology output slug for a single --repo run.")
    parser.add_argument("--config", default="", help="agent-watcher config/targets.json path.")
    parser.add_argument("--all-targets", action="store_true", help="Mine every target in --config.")
    parser.add_argument("--target", action="append", default=[], help="Configured repo to include from --config. Repeatable.")
    parser.add_argument("--workflow", action="append", default=[], help="Workflow file/name to scan. Repeatable.")
    parser.add_argument("--run-id", action="append", default=[], help="Known run id to mine. Repeatable.")
    parser.add_argument("--out-dir", default="build/trace-samples", help="Output directory.")
    parser.add_argument("--limit-runs", type=int, default=40, help="Recent runs to inspect per workflow.")
    parser.add_argument("--all-runs", action="store_true", help="Inspect every available run for each workflow.")
    parser.add_argument("--from-artifacts", action="store_true", help="Inspect runs that have retained trace-like artifacts.")
    parser.add_argument("--skip-artifact-check", action="store_true", help="Skip per-run artifact lookup for log-only workflows.")
    parser.add_argument("--max-pages", type=int, default=0, help="Workflow run pages to scan with --all-runs; 0 means until empty.")
    parser.add_argument("--max-samples", type=int, default=2, help="Runs with traces to keep per repo; 0 means no limit.")
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
        key = target["repo"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    return deduped


def mine_target(
    *,
    repo: str,
    slug: str,
    out_dir: Path,
    workflows: list[str],
    run_ids: list[str],
    limit_runs: int,
    max_samples: int,
    all_runs: bool,
    max_pages: int,
    from_artifacts: bool,
    skip_artifact_check: bool,
) -> None:
    target_dir = out_dir / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    candidate_runs = (
        explicit_runs(repo, run_ids)
        if run_ids
        else discover_artifact_runs(repo)
        if from_artifacts
        else discover_runs(repo, workflows, limit_runs, all_runs=all_runs, max_pages=max_pages)
    )
    trace_summaries: list[dict[str, Any]] = []
    skipped_runs: list[dict[str, Any]] = []
    inspected = 0
    errors: list[str] = []

    for run in candidate_runs:
        if max_samples and len(trace_summaries) >= max_samples:
            break
        inspected += 1
        run_id = str(run["databaseId"])
        run_dir = target_dir / run_id
        try:
            summary = mine_run(repo, run, run_dir, skip_artifact_check=skip_artifact_check)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{run_id}: {exc}")
            continue
        if summary["trace_record_count"] or summary["artifact_trace_files"]:
            trace_summaries.append(summary)
        else:
            skipped_runs.append(summary)

    index = {
        "repo": repo,
        "slug": slug,
        "workflows": workflows,
        "all_runs": all_runs,
        "from_artifacts": from_artifacts,
        "skip_artifact_check": skip_artifact_check,
        "candidate_run_count": len(candidate_runs),
        "inspected_runs": inspected,
        "trace_run_count": len(trace_summaries),
        "sample_count": len(trace_summaries),
        "skipped_run_count": len(skipped_runs),
        "samples": trace_summaries,
        "trace_summaries": trace_summaries,
        "skipped_runs": skipped_runs,
        "errors": errors,
    }
    write_json(target_dir / "index.json", index)
    print(f"{slug}: inspected={inspected} traces={len(trace_summaries)} skipped={len(skipped_runs)}")


def discover_runs(
    repo: str,
    workflows: list[str],
    limit_runs: int,
    *,
    all_runs: bool = False,
    max_pages: int = 0,
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for workflow in workflows:
        if all_runs:
            runs.extend(discover_runs_paginated(repo, workflow, max_pages))
            continue
        cmd = [
            "gh",
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            workflow,
            "--limit",
            str(limit_runs),
            "--json",
            "databaseId,createdAt,displayTitle,url,event,conclusion,status,workflowName",
        ]
        proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
        if proc.returncode != 0:
            continue
        for run in json.loads(proc.stdout or "[]"):
            run["workflowQuery"] = workflow
            runs.append(run)
    return dedupe_runs(runs)


def discover_artifact_runs(repo: str) -> list[dict[str, Any]]:
    runs_by_id: dict[str, dict[str, Any]] = {}
    page = 1
    while True:
        proc = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "GET",
                f"repos/{repo}/actions/artifacts",
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
        artifacts = payload.get("artifacts", [])
        if not artifacts:
            break
        for artifact in artifacts:
            if artifact.get("expired") or not TRACE_ARTIFACT_RE.search(artifact.get("name", "")):
                continue
            workflow_run = artifact.get("workflow_run") or {}
            run_id = workflow_run.get("id")
            if not run_id:
                continue
            key = str(run_id)
            run = runs_by_id.setdefault(
                key,
                {
                    "databaseId": run_id,
                    "createdAt": artifact.get("created_at"),
                    "displayTitle": workflow_run.get("head_branch") or artifact.get("name"),
                    "url": workflow_run.get("html_url"),
                    "event": None,
                    "conclusion": None,
                    "status": None,
                    "workflowName": None,
                    "workflowQuery": "repo-artifacts",
                    "artifactCandidates": [],
                },
            )
            run["artifactCandidates"].append(
                {
                    "id": artifact.get("id"),
                    "name": artifact.get("name"),
                    "size_in_bytes": artifact.get("size_in_bytes"),
                    "created_at": artifact.get("created_at"),
                    "expires_at": artifact.get("expires_at"),
                }
            )
        page += 1
    return sorted(runs_by_id.values(), key=lambda run: run.get("createdAt") or "", reverse=True)


def discover_runs_paginated(repo: str, workflow: str, max_pages: int) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    page = 1
    while True:
        if max_pages and page > max_pages:
            break
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
        page_runs = payload.get("workflow_runs", [])
        if not page_runs:
            break
        for run in page_runs:
            runs.append(normalize_api_run(run, workflow))
        page += 1
    return runs


def normalize_api_run(run: dict[str, Any], workflow: str) -> dict[str, Any]:
    return {
        "databaseId": run.get("id"),
        "createdAt": run.get("created_at"),
        "displayTitle": run.get("display_title") or run.get("name"),
        "url": run.get("html_url"),
        "event": run.get("event"),
        "conclusion": run.get("conclusion"),
        "status": run.get("status"),
        "workflowName": run.get("name"),
        "workflowQuery": workflow,
        "runNumber": run.get("run_number"),
    }


def explicit_runs(repo: str, run_ids: list[str]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for run_id in run_ids:
        payload = gh_json(["api", f"repos/{repo}/actions/runs/{run_id}"])
        runs.append(
            {
                "databaseId": payload["id"],
                "createdAt": payload.get("created_at"),
                "displayTitle": payload.get("display_title") or payload.get("name"),
                "url": payload.get("html_url"),
                "event": payload.get("event"),
                "conclusion": payload.get("conclusion"),
                "status": payload.get("status"),
                "workflowName": payload.get("name"),
            }
        )
    return runs


def dedupe_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for run in runs:
        run_id = str(run["databaseId"])
        if run_id in seen:
            continue
        seen.add(run_id)
        deduped.append(run)
    return deduped


def mine_run(
    repo: str,
    run: dict[str, Any],
    run_dir: Path,
    *,
    skip_artifact_check: bool = False,
) -> dict[str, Any]:
    run_id = str(run["databaseId"])
    trace_artifacts = []
    if not skip_artifact_check:
        artifact_payload = gh_json(["api", f"repos/{repo}/actions/runs/{run_id}/artifacts"])
        trace_artifacts = matching_trace_artifacts(artifact_payload)
    trace_job = find_trace_job(repo, run_id)

    if not trace_artifacts and not trace_job:
        return {
            "repo": repo,
            "run_id": run_id,
            "run_url": run.get("url"),
            "created_at": run.get("createdAt"),
            "title": run.get("displayTitle"),
            "event": run.get("event"),
            "conclusion": run.get("conclusion"),
            "artifact_trace_files": [],
            "trace_record_count": 0,
            "session_ids": [],
            "type_counts": {},
            "skipped_reason": "no trace artifact and no trace-like job",
        }

    reset_dir(run_dir)
    write_json(run_dir / "run.json", run)
    artifact_files = download_trace_artifacts(repo, run_id, run_dir / "artifact", trace_artifacts)
    log_error = ""
    try:
        log_trace_count, session_ids, type_counts = mine_logs(repo, run_id, run_dir)
    except Exception as exc:  # noqa: BLE001
        log_trace_count = 0
        session_ids = set()
        type_counts = collections.Counter()
        log_error = str(exc)

    summary = {
        "repo": repo,
        "run_id": run_id,
        "run_url": run.get("url"),
        "created_at": run.get("createdAt"),
        "title": run.get("displayTitle"),
        "event": run.get("event"),
        "conclusion": run.get("conclusion"),
        "trace_job": trace_job,
        "artifact_trace_files": [str(path.relative_to(run_dir)) for path in artifact_files],
        "trace_record_count": log_trace_count,
        "session_ids": sorted(session_ids),
        "type_counts": dict(sorted(type_counts.items())),
        "log_error": log_error,
    }
    write_json(run_dir / "summary.json", summary)
    return summary

def matching_trace_artifacts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        artifact
        for artifact in payload.get("artifacts", [])
        if not artifact.get("expired") and TRACE_ARTIFACT_RE.search(artifact.get("name", ""))
    ]


def find_trace_job(repo: str, run_id: str) -> dict[str, Any] | None:
    payload = gh_json(["api", f"repos/{repo}/actions/runs/{run_id}/jobs"])
    for job in payload.get("jobs", []):
        name = job.get("name", "")
        if job.get("conclusion") == "skipped":
            continue
        if re.search(r"(respond|claude|agent|goose)", name, re.I):
            return {
                "id": job.get("id"),
                "name": name,
                "conclusion": job.get("conclusion"),
                "status": job.get("status"),
                "html_url": job.get("html_url"),
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
            }
    return None


def download_trace_artifacts(
    repo: str,
    run_id: str,
    artifact_dir: Path,
    matches: list[dict[str, Any]],
) -> list[Path]:
    if not matches:
        return []

    artifact_dir.mkdir(parents=True, exist_ok=True)
    for artifact in matches:
        subprocess.run(
            [
                "gh",
                "run",
                "download",
                "--repo",
                repo,
                run_id,
                "-n",
                artifact["name"],
                "-D",
                str(artifact_dir / artifact["name"]),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
    return [path for path in artifact_dir.rglob("*") if path.is_file()]


def mine_logs(repo: str, run_id: str, run_dir: Path) -> tuple[int, set[str], collections.Counter[str]]:
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    zip_path = run_dir / "logs.zip"
    with zip_path.open("wb") as handle:
        subprocess.run(["gh", "api", f"repos/{repo}/actions/runs/{run_id}/logs"], stdout=handle, check=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(logs_dir)

    trace_path = run_dir / "log-trace.jsonl"
    session_ids: set[str] = set()
    type_counts: collections.Counter[str] = collections.Counter()
    count = 0

    with trace_path.open("w", encoding="utf-8") as output:
        for log_file in logs_dir.rglob("*.txt"):
            text = log_file.read_text(errors="replace")
            if not TRACE_MARKER_RE.search(text):
                continue
            for record in parse_json_records(text):
                count += 1
                record_type = record.get("type")
                if isinstance(record_type, str):
                    type_counts[record_type] += 1
                session_id = record.get("session_id")
                if isinstance(session_id, str):
                    session_ids.add(session_id)
                output.write(json.dumps(record, sort_keys=True) + "\n")

    if count == 0:
        trace_path.unlink(missing_ok=True)
    return count, session_ids, type_counts


def parse_json_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    buffer: list[str] = []
    for raw_line in text.splitlines():
        line = TIMESTAMP_RE.sub("", raw_line)
        if not buffer:
            if line.startswith("{"):
                buffer.append(line)
            continue
        buffer.append(line)
        try:
            parsed = json.loads("\n".join(buffer))
        except json.JSONDecodeError:
            if len(buffer) > 20000:
                buffer = []
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
        buffer = []
    return records


def gh_json(args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(["gh", *args], text=True, capture_output=True, check=True)
    return json.loads(proc.stdout)


def reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
