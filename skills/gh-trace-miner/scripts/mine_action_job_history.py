#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import mine_traces


TRACE_JOB_RE = re.compile(r"(respond|claude|agent|goose)", re.I)


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir) / args.slug
    out_dir.mkdir(parents=True, exist_ok=True)

    runs = load_runs(args)
    original_run_count = len(runs)
    runs = filter_runs(runs, created_after=args.created_after, skip_run_skipped=not args.include_run_skipped)
    token = gh_token()
    job_results = {} if args.assume_trace_job else fetch_jobs_for_runs(args.repo, runs, token, args.workers)

    trace_summaries: list[dict[str, Any]] = []
    skipped_runs: list[dict[str, Any]] = []
    fetch_errors: list[dict[str, str]] = []
    trace_run_jobs = 0

    mine_inputs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for run in runs:
        run_id = str(run["databaseId"])
        trace_job = {"name": "assumed-trace-job"} if args.assume_trace_job else None
        if not trace_job:
            result = job_results.get(run_id, {})
            if result.get("error"):
                fetch_errors.append({"run_id": run_id, "error": result["error"]})
                continue
            trace_job = first_trace_job(result.get("jobs", []))
        if not trace_job:
            skipped_runs.append(skipped_summary(args.repo, run, "no non-skipped trace-like job"))
            continue
        trace_run_jobs += 1
        mine_inputs.append((run, trace_job))

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.mine_workers) as executor:
        future_to_run = {
            executor.submit(
                mine_trace_run,
                args.repo,
                run,
                out_dir / str(run["databaseId"]),
                args.skip_artifact_check,
            ): str(run["databaseId"])
            for run, _trace_job in mine_inputs
        }
        for future in concurrent.futures.as_completed(future_to_run):
            run_id = future_to_run[future]
            try:
                summary = future.result()
            except Exception as exc:  # noqa: BLE001
                fetch_errors.append({"run_id": run_id, "error": str(exc)})
                continue
            if summary["trace_record_count"] or summary["artifact_trace_files"]:
                trace_summaries.append(summary)
            else:
                skipped_runs.append(summary)

    index = {
        "repo": args.repo,
        "slug": args.slug,
        "workflow": args.workflow,
        "original_run_count": original_run_count,
        "created_after": args.created_after,
        "candidate_run_count": len(runs),
        "job_inspected_count": len(job_results),
        "trace_job_run_count": trace_run_jobs,
        "trace_run_count": len(trace_summaries),
        "skipped_run_count": len(skipped_runs),
        "fetch_error_count": len(fetch_errors),
        "trace_summaries": trace_summaries,
        "skipped_runs": skipped_runs,
        "fetch_errors": fetch_errors,
    }
    mine_traces.write_json(out_dir / "index.json", index)
    print(
        f"{args.slug}: runs={len(runs)} trace_jobs={index['trace_job_run_count']} "
        f"traces={len(trace_summaries)} skipped={len(skipped_runs)} errors={len(fetch_errors)}"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Concurrent full-history job scan for log-only Actions workflows.")
    parser.add_argument("--repo", required=True, help="Repository owner/name.")
    parser.add_argument("--slug", required=True, help="Output slug.")
    parser.add_argument("--workflow", required=True, help="Workflow file/name to scan.")
    parser.add_argument("--runs-json", default="", help="Optional paginated gh api workflow-runs JSON file.")
    parser.add_argument("--out-dir", default="build/full-agent-history/actions", help="Output parent directory.")
    parser.add_argument("--workers", type=int, default=12, help="Concurrent job metadata requests.")
    parser.add_argument("--mine-workers", type=int, default=4, help="Concurrent log/artifact mining requests.")
    parser.add_argument("--skip-artifact-check", action="store_true", help="Skip artifact lookup when mining matching runs.")
    parser.add_argument("--created-after", default="", help="Only inspect runs created at or after this ISO timestamp/date.")
    parser.add_argument("--include-run-skipped", action="store_true", help="Do not prefilter run-level skipped conclusions.")
    parser.add_argument("--assume-trace-job", action="store_true", help="Mine every filtered run without fetching job metadata.")
    return parser.parse_args()


def mine_trace_run(
    repo: str,
    run: dict[str, Any],
    run_dir: Path,
    skip_artifact_check: bool,
) -> dict[str, Any]:
    summary_path = run_dir / "summary.json"
    if summary_path.exists():
        return json.loads(summary_path.read_text(encoding="utf-8"))
    if skip_artifact_check:
        return mine_log_only_run(repo, run, run_dir)
    return mine_traces.mine_run(repo, run, run_dir, skip_artifact_check=skip_artifact_check)


def mine_log_only_run(repo: str, run: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    mine_traces.reset_dir(run_dir)
    mine_traces.write_json(run_dir / "run.json", run)
    log_error = ""
    try:
        log_trace_count, session_ids, type_counts = mine_traces.mine_logs(repo, str(run["databaseId"]), run_dir)
    except Exception as exc:  # noqa: BLE001
        log_trace_count = 0
        session_ids = set()
        type_counts = {}
        log_error = str(exc)

    summary = {
        "repo": repo,
        "run_id": str(run["databaseId"]),
        "run_url": run.get("url"),
        "created_at": run.get("createdAt"),
        "title": run.get("displayTitle"),
        "event": run.get("event"),
        "conclusion": run.get("conclusion"),
        "trace_job": {"name": "assumed-trace-job"},
        "artifact_trace_files": [],
        "trace_record_count": log_trace_count,
        "session_ids": sorted(session_ids),
        "type_counts": dict(sorted(type_counts.items())),
        "log_error": log_error,
    }
    mine_traces.write_json(run_dir / "summary.json", summary)
    return summary


def load_runs(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.runs_json:
        payloads = load_json_stream(Path(args.runs_json).read_text(encoding="utf-8"))
        runs = [
            mine_traces.normalize_api_run(run, args.workflow)
            for payload in payloads
            for run in payload.get("workflow_runs", [])
        ]
        return mine_traces.dedupe_runs(runs)
    return mine_traces.discover_runs(args.repo, [args.workflow], 0, all_runs=True, max_pages=0)


def filter_runs(
    runs: list[dict[str, Any]],
    *,
    created_after: str,
    skip_run_skipped: bool,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    cutoff = normalize_cutoff(created_after)
    for run in runs:
        if skip_run_skipped and run.get("conclusion") == "skipped":
            continue
        created_at = run.get("createdAt") or ""
        if cutoff and created_at < cutoff:
            continue
        filtered.append(run)
    return filtered


def normalize_cutoff(value: str) -> str:
    if not value:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return f"{value}T00:00:00Z"
    return value


def load_json_stream(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    index = 0
    payloads: list[dict[str, Any]] = []
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        payload, index = decoder.raw_decode(text, index)
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def gh_token() -> str:
    proc = subprocess.run(["gh", "auth", "token"], text=True, capture_output=True, check=True)
    return proc.stdout.strip()


def fetch_jobs_for_runs(
    repo: str,
    runs: list[dict[str, Any]],
    token: str,
    workers: int,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_run = {
            executor.submit(fetch_jobs, repo, str(run["databaseId"]), token): str(run["databaseId"])
            for run in runs
        }
        for future in concurrent.futures.as_completed(future_to_run):
            run_id = future_to_run[future]
            try:
                results[run_id] = {"jobs": future.result()}
            except Exception as exc:  # noqa: BLE001
                results[run_id] = {"jobs": [], "error": str(exc)}
    return results


def fetch_jobs(repo: str, run_id: str, token: str) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = fetch_json(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs", token, page)
        jobs.extend(payload.get("jobs", []))
        if len(jobs) >= payload.get("total_count", len(jobs)):
            return jobs
        page += 1


def fetch_json(url: str, token: str, page: int) -> dict[str, Any]:
    query = urllib.parse.urlencode({"per_page": 100, "page": page})
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "gh-trace-miner",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body[:300]}") from exc


def first_trace_job(jobs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for job in jobs:
        if job.get("conclusion") == "skipped":
            continue
        if TRACE_JOB_RE.search(job.get("name", "")):
            return job
    return None


def skipped_summary(repo: str, run: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "repo": repo,
        "run_id": str(run["databaseId"]),
        "run_url": run.get("url"),
        "created_at": run.get("createdAt"),
        "title": run.get("displayTitle"),
        "event": run.get("event"),
        "conclusion": run.get("conclusion"),
        "artifact_trace_files": [],
        "trace_record_count": 0,
        "session_ids": [],
        "type_counts": {},
        "skipped_reason": reason,
    }


if __name__ == "__main__":
    raise SystemExit(main())
