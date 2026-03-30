from __future__ import annotations

from pathlib import Path

from .models import RepoReport, TrackedItem


def write_reports(output_dir: str | Path, reports: list[RepoReport]) -> None:
    base = Path(output_dir)
    base.mkdir(parents=True, exist_ok=True)

    for report in reports:
        slug = repo_slug(report.target.repo)
        (base / f"{slug}.md").write_text(render_report(report))
        (base / f"{slug}.json").write_text(_json(report))

    (base / "summary.md").write_text(render_summary(reports))


def render_report(report: RepoReport) -> str:
    lines = [
        f"# Watcher Report: {report.target.display_name}",
        "",
        f"- Repo: `{report.target.repo}`",
        f"- Generated: `{report.generated_at.isoformat()}`",
        f"- Window start: `{report.window_start.isoformat()}`",
        f"- Assessment: `{report.assessment}`",
        "",
        report.headline,
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]

    for key, value in report.metrics.items():
        lines.append(f"| {key.replace('_', ' ')} | {value} |")

    if report.recommendations:
        lines.extend(["", "## Recommendations", ""])
        for recommendation in report.recommendations:
            lines.append(f"- {recommendation}")

    if report.errors:
        lines.extend(["", "## Errors", ""])
        for error in report.errors:
            lines.append(f"- {error}")

    lines.extend(["", "## Agent-Related Items", ""])
    if not report.tracked_items:
        lines.append("No agent-related items detected in this run.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| Item | Status | Updated | Signals | Latest note |",
            "| --- | --- | --- | --- | --- |",
        ]
    )

    for item in report.tracked_items:
        lines.append(
            "| "
            f"{_item_link(item)} | "
            f"{escape_cell(item.status)} | "
            f"{item.updated_at.strftime('%Y-%m-%d %H:%M UTC')} | "
            f"{escape_cell('; '.join(item.signals))} | "
            f"{escape_cell(item.latest_excerpt)} |"
        )

    return "\n".join(lines) + "\n"


def render_summary(reports: list[RepoReport]) -> str:
    lines = [
        "# Agent Watcher Summary",
        "",
        "| Repo | Assessment | Agent items | Closed/Merged | Stalled | Errors |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]

    for report in reports:
        lines.append(
            "| "
            f"`{report.target.repo}` | "
            f"`{report.assessment}` | "
            f"{report.metrics.get('agent_items', 0)} | "
            f"{report.metrics.get('closed_items', 0)} | "
            f"{report.metrics.get('stalled_items', 0)} | "
            f"{len(report.errors)} |"
        )

    return "\n".join(lines) + "\n"


def repo_slug(repo: str) -> str:
    return repo.replace("/", "__")


def escape_cell(value: str) -> str:
    return value.replace("\n", " ").replace("|", "\\|")


def _item_link(item: TrackedItem) -> str:
    prefix = "PR" if item.kind == "pr" else "Issue"
    title = escape_cell(item.title)
    return f"[{prefix} #{item.number}: {title}]({item.url})"


def _json(report: RepoReport) -> str:
    import json

    return json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
