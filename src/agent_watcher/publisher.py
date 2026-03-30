from __future__ import annotations

from .github_api import GitHubClient
from .models import RepoReport
from .reporting import render_report


LABEL_NAME = "agent-watcher"


def upsert_repo_issue(client: GitHubClient, sink_repo: str, report: RepoReport) -> int:
    label_available = _ensure_label(client, sink_repo)
    title = f"Watcher: {report.target.repo}"
    existing = client.find_issue_by_title(sink_repo, title)
    body = _issue_body(report)

    if existing is None:
        issue = client.create_issue(
            sink_repo,
            title=title,
            body=body,
            labels=[LABEL_NAME] if label_available else None,
        )
    else:
        issue = client.update_issue(
            sink_repo,
            int(existing["number"]),
            body=body,
            state="open",
        )

    issue_number = int(issue["number"])
    client.create_issue_comment(sink_repo, issue_number, body=render_report(report))
    return issue_number


def _issue_body(report: RepoReport) -> str:
    return "\n".join(
        [
            f"# Watcher: {report.target.repo}",
            "",
            "This issue is maintained by `agent-watcher`.",
            "",
            f"- Target repo: `{report.target.repo}`",
            f"- Latest assessment: `{report.assessment}`",
            f"- Latest scan: `{report.generated_at.isoformat()}`",
            f"- Headline: {report.headline}",
            "",
            "Each new scheduled run appends a comment with the detailed findings.",
        ]
    )


def _ensure_label(client: GitHubClient, sink_repo: str) -> None:
    try:
        client.ensure_label(
            sink_repo,
            name=LABEL_NAME,
            color="0E8A16",
            description="Rolling issues created by the agent watcher",
        )
        return True
    except Exception:
        # Publishing the issue is more important than ensuring the label exists.
        return False
