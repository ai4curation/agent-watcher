#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import mine_traces


SESSION_RE = re.compile(
    r"(?:/sessions/|session_id=)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)
COPILOT_AUTHOR_LOGINS = {"app/copilot-swe-agent", "Copilot", "copilot-swe-agent[bot]"}
AGENT_TASK_JSON_FIELDS = (
    "completedAt,createdAt,id,name,pullRequestNumber,pullRequestState,"
    "pullRequestTitle,pullRequestUrl,repository,state,updatedAt,user"
)


def main() -> int:
    args = parse_args()
    targets = load_targets(args)
    if not targets:
        print("No targets selected.", file=sys.stderr)
        return 1

    gh_agent = find_agent_task_gh(args.gh)
    if not gh_agent:
        print(
            "No GitHub CLI with agent-task support found. Install gh >= 2.80.0 "
            "or pass --gh /path/to/new/gh.",
            file=sys.stderr,
        )
        return 1

    out_dir = Path(args.out_dir)
    for target in targets:
        mine_repo(
            repo=target["repo"],
            slug=target["slug"],
            out_dir=out_dir,
            gh_agent=gh_agent,
            limit_prs=args.limit_prs,
            limit_agent_tasks=args.limit_agent_tasks,
            max_sessions=args.max_sessions,
            skip_logs=args.skip_logs,
        )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mine GitHub Copilot coding-agent session logs for PRs.")
    parser.add_argument("--config", default="", help="agent-watcher config/targets.json path.")
    parser.add_argument("--all-targets", action="store_true", help="Mine every target in --config.")
    parser.add_argument("--target", action="append", default=[], help="Configured repo to include from --config.")
    parser.add_argument("--repo", action="append", default=[], help="Repository owner/name. Repeatable.")
    parser.add_argument("--slug", default="", help="Ontology output slug for a single --repo run.")
    parser.add_argument("--out-dir", default="build/copilot-traces", help="Output directory.")
    parser.add_argument("--limit-prs", type=int, default=300, help="Recent PRs to inspect per repo.")
    parser.add_argument("--limit-agent-tasks", type=int, default=200, help="Visible agent tasks to list.")
    parser.add_argument("--max-sessions", type=int, default=0, help="Maximum sessions to download per repo; 0 means no limit.")
    parser.add_argument("--skip-logs", action="store_true", help="Write session metadata without downloading agent.log.")
    parser.add_argument("--gh", default="", help="Path to a GitHub CLI binary with gh agent-task support.")
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


def find_agent_task_gh(explicit: str) -> str:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    candidates.append("gh")

    script_path = Path(__file__).resolve()
    search_roots = [Path.cwd(), *script_path.parents]
    for root in search_roots:
        candidates.extend(
            str(path)
            for path in sorted((root / "build" / "tools").glob("gh-*/gh_*/bin/gh"), reverse=True)
        )

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if shutil.which(candidate) or Path(candidate).exists():
            proc = subprocess.run(
                [candidate, "agent-task", "view", "--help"],
                text=True,
                capture_output=True,
                check=False,
            )
            if proc.returncode == 0:
                return candidate
    return ""


def mine_repo(
    *,
    repo: str,
    slug: str,
    out_dir: Path,
    gh_agent: str,
    limit_prs: int,
    limit_agent_tasks: int,
    max_sessions: int,
    skip_logs: bool,
) -> None:
    repo_dir = out_dir / slug
    repo_dir.mkdir(parents=True, exist_ok=True)
    prs = list_copilot_prs(repo, limit_prs)
    visible_sessions = list_visible_agent_tasks(gh_agent, repo, limit_agent_tasks)
    prs = add_visible_task_prs(repo, prs, visible_sessions)
    sessions_by_pr = sessions_by_pull_request(visible_sessions)

    results: list[dict[str, Any]] = []
    downloaded = 0
    session_cache: dict[str, dict[str, Any]] = {}

    for pr in prs:
        if max_sessions and downloaded >= max_sessions:
            results.append(skipped_pr_result(pr, "max sessions reached"))
            continue

        context = get_pr_context(repo, int(pr["number"]))
        candidate_sessions = extract_sessions_from_pr(pr, context)
        for session_id in sessions_by_pr.get(int(pr["number"]), []):
            candidate_sessions.setdefault(session_id, []).append("agent-task-list")

        result, count = mine_pr_sessions(
            gh_agent=gh_agent,
            repo=repo,
            pr=pr,
            context=context,
            repo_dir=repo_dir,
            candidate_sessions=candidate_sessions,
            session_cache=session_cache,
            remaining_sessions=max_sessions - downloaded if max_sessions else 0,
            skip_logs=skip_logs,
        )
        downloaded += count
        results.append(result)

    index = {
        "repo": repo,
        "slug": slug,
        "copilot_pr_count": len(prs),
        "visible_agent_task_count": len(visible_sessions),
        "session_pr_count": sum(1 for result in results if result["sessions"]),
        "session_count": sum(len(result["sessions"]) for result in results),
        "log_count": sum(1 for result in results for session in result["sessions"] if session.get("log_path")),
        "prs": results,
    }
    mine_traces.write_json(repo_dir / "agent-tasks-visible.json", visible_sessions)
    mine_traces.write_json(repo_dir / "index.json", index)
    print(
        f"{slug}: copilot_prs={len(prs)} sessions={index['session_count']} "
        f"logs={index['log_count']} visible_tasks={len(visible_sessions)}"
    )


def list_copilot_prs(repo: str, limit: int) -> list[dict[str, Any]]:
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
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
    prs = json.loads(proc.stdout or "[]")
    return [pr for pr in prs if is_copilot_pr(pr)]


def add_visible_task_prs(
    repo: str,
    prs: list[dict[str, Any]],
    visible_sessions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prs_by_number = {int(pr["number"]): pr for pr in prs}
    for task in visible_sessions:
        number = task.get("pullRequestNumber")
        if not isinstance(number, int) or number in prs_by_number:
            continue
        pr = fetch_pr_summary(repo, number)
        if pr:
            prs_by_number[number] = pr
    return sorted(prs_by_number.values(), key=lambda pr: int(pr["number"]), reverse=True)


def fetch_pr_summary(repo: str, number: int) -> dict[str, Any] | None:
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "number,title,state,createdAt,updatedAt,url,headRefName,author,body",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return json.loads(proc.stdout or "{}")


def is_copilot_pr(pr: dict[str, Any]) -> bool:
    author_login = ((pr.get("author") or {}).get("login") or "").lower()
    head_ref = (pr.get("headRefName") or "").lower()
    return author_login in {login.lower() for login in COPILOT_AUTHOR_LOGINS} or head_ref.startswith("copilot/")


def get_pr_context(repo: str, number: int) -> dict[str, Any]:
    proc = subprocess.run(
        ["gh", "pr", "view", str(number), "--repo", repo, "--json", "body,comments,reviews,commits"],
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout or "{}")


def extract_sessions_from_pr(pr: dict[str, Any], context: dict[str, Any]) -> dict[str, list[str]]:
    sources: dict[str, list[str]] = {}
    add_sessions(sources, pr.get("body") or "", "pr-body")
    add_sessions(sources, context.get("body") or "", "pr-view-body")

    for index, comment in enumerate(context.get("comments", []), start=1):
        add_sessions(sources, comment.get("body") or "", f"comment-{index}")
    for index, review in enumerate(context.get("reviews", []), start=1):
        add_sessions(sources, review.get("body") or "", f"review-{index}")
    for index, commit in enumerate(context.get("commits", []), start=1):
        add_sessions(sources, commit.get("messageBody") or "", f"commit-{index}")
        add_sessions(sources, commit.get("messageHeadline") or "", f"commit-{index}-headline")
    return sources


def add_sessions(sources: dict[str, list[str]], text: str, source: str) -> None:
    for session_id in SESSION_RE.findall(text):
        sources.setdefault(session_id.lower(), []).append(source)


def list_visible_agent_tasks(gh_agent: str, repo: str, limit: int) -> list[dict[str, Any]]:
    proc = subprocess.run(
        [gh_agent, "agent-task", "list", "--limit", str(limit), "--json", AGENT_TASK_JSON_FIELDS],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    tasks = json.loads(proc.stdout or "[]")
    return [task for task in tasks if task.get("repository") == repo]


def sessions_by_pull_request(tasks: list[dict[str, Any]]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for task in tasks:
        number = task.get("pullRequestNumber")
        session_id = task.get("id")
        if isinstance(number, int) and isinstance(session_id, str):
            grouped.setdefault(number, []).append(session_id.lower())
    return grouped


def mine_pr_sessions(
    *,
    gh_agent: str,
    repo: str,
    pr: dict[str, Any],
    context: dict[str, Any],
    repo_dir: Path,
    candidate_sessions: dict[str, list[str]],
    session_cache: dict[str, dict[str, Any]],
    remaining_sessions: int,
    skip_logs: bool,
) -> tuple[dict[str, Any], int]:
    pr_dir = repo_dir / f"pr-{pr['number']}"
    pr_dir.mkdir(parents=True, exist_ok=True)
    pr_record = pr_record_from_context(pr, context, candidate_sessions)
    mine_traces.write_json(pr_dir / "pr.json", pr_record)

    sessions: list[dict[str, Any]] = []
    external_sessions: list[dict[str, Any]] = []
    errors: list[str] = []
    downloaded = 0

    for session_id, sources in sorted(candidate_sessions.items()):
        if remaining_sessions and downloaded >= remaining_sessions:
            break
        try:
            session_meta = view_session(gh_agent, repo, session_id, session_cache)
        except subprocess.CalledProcessError as exc:
            errors.append(f"{session_id}: {exc.stderr.strip() or exc.stdout.strip() or exc}")
            continue

        if session_meta.get("pullRequestNumber") != pr["number"]:
            external_sessions.append(session_summary(session_meta, sources, "", "session belongs to another PR"))
            continue

        session_dir = pr_dir / f"session-{session_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        mine_traces.write_json(session_dir / "session.json", session_meta)
        log_path = ""
        log_bytes = 0
        log_lines = 0
        if not skip_logs:
            try:
                log_path, log_bytes, log_lines = write_session_log(gh_agent, repo, session_id, session_dir)
            except subprocess.CalledProcessError as exc:
                errors.append(f"{session_id} log: {exc.stderr.strip() or exc.stdout.strip() or exc}")
        summary = session_summary(session_meta, sources, str(Path(f"session-{session_id}") / "agent.log") if log_path else "", "")
        summary["log_bytes"] = log_bytes
        summary["log_lines"] = log_lines
        sessions.append(summary)
        downloaded += 1

    result = {
        **pr_record,
        "sessions": sessions,
        "external_sessions": external_sessions,
        "errors": errors,
        "missing_reason": "" if sessions else missing_reason(candidate_sessions, external_sessions, errors),
    }
    mine_traces.write_json(pr_dir / "summary.json", result)
    return result, downloaded


def pr_record_from_context(
    pr: dict[str, Any],
    context: dict[str, Any],
    candidate_sessions: dict[str, list[str]],
) -> dict[str, Any]:
    return {
        "number": pr["number"],
        "title": pr["title"],
        "state": pr["state"],
        "created_at": pr["createdAt"],
        "updated_at": pr["updatedAt"],
        "url": pr["url"],
        "head_ref": pr["headRefName"],
        "author": (pr.get("author") or {}).get("login"),
        "candidate_sessions": [{"id": key, "sources": value} for key, value in sorted(candidate_sessions.items())],
        "comment_count": len(context.get("comments", [])),
        "review_count": len(context.get("reviews", [])),
        "commit_count": len(context.get("commits", [])),
    }


def view_session(
    gh_agent: str,
    repo: str,
    session_id: str,
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if session_id in cache:
        return cache[session_id]
    proc = subprocess.run(
        [gh_agent, "agent-task", "view", "--repo", repo, session_id, "--json", AGENT_TASK_JSON_FIELDS],
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(proc.stdout or "{}")
    cache[session_id] = payload
    return payload


def write_session_log(gh_agent: str, repo: str, session_id: str, session_dir: Path) -> tuple[str, int, int]:
    proc = subprocess.run(
        [gh_agent, "agent-task", "view", "--repo", repo, session_id, "--log"],
        text=True,
        capture_output=True,
        check=True,
    )
    path = session_dir / "agent.log"
    path.write_text(proc.stdout, encoding="utf-8")
    return str(path), len(proc.stdout.encode("utf-8")), proc.stdout.count("\n")


def session_summary(session_meta: dict[str, Any], sources: list[str], log_path: str, note: str) -> dict[str, Any]:
    return {
        "id": session_meta.get("id"),
        "name": session_meta.get("name"),
        "state": session_meta.get("state"),
        "repository": session_meta.get("repository"),
        "pull_request_number": session_meta.get("pullRequestNumber"),
        "pull_request_url": session_meta.get("pullRequestUrl"),
        "user": session_meta.get("user"),
        "created_at": session_meta.get("createdAt"),
        "updated_at": session_meta.get("updatedAt"),
        "sources": sorted(set(sources)),
        "log_path": log_path,
        "note": note,
    }


def skipped_pr_result(pr: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "number": pr["number"],
        "title": pr["title"],
        "state": pr["state"],
        "created_at": pr["createdAt"],
        "updated_at": pr["updatedAt"],
        "url": pr["url"],
        "head_ref": pr["headRefName"],
        "author": (pr.get("author") or {}).get("login"),
        "candidate_sessions": [],
        "comment_count": 0,
        "review_count": 0,
        "commit_count": 0,
        "sessions": [],
        "external_sessions": [],
        "errors": [],
        "missing_reason": reason,
    }


def missing_reason(
    candidate_sessions: dict[str, list[str]],
    external_sessions: list[dict[str, Any]],
    errors: list[str],
) -> str:
    if external_sessions and not candidate_sessions:
        return "only external sessions found"
    if external_sessions and len(external_sessions) == len(candidate_sessions):
        return "candidate sessions belong to other PRs"
    if candidate_sessions and errors:
        return "candidate sessions found but retrieval failed"
    if candidate_sessions:
        return "candidate sessions found but no matching session for this PR"
    return "no session id found in PR metadata or visible agent-task list"


if __name__ == "__main__":
    raise SystemExit(main())
