from __future__ import annotations

from pathlib import Path

from .models import RepoReport, TrackedItem
from .scheduling import repo_slug


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
        f"# Agent Activity Context: {report.target.display_name}",
        "",
        f"- Repo: `{report.target.repo}`",
        f"- Generated: `{report.generated_at.isoformat()}`",
        f"- Window start: `{report.window_start.isoformat()}`",
        f"- Report date: `{report.report_date}`",
        f"- Suggested issue title: `{report.issue_title}`",
        f"- Lookback days: `{report.target.lookback_days}`",
        f"- Agent-touched items found: `{report.metrics.get('agent_items', 0)}`",
        f"- Agent summons or textual references: `{report.metrics.get('agent_summons', 0)}`",
        "",
        "This file is structured context for a qualitative reviewer. Treat the counts as supporting context, not a final judgment.",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]

    for key, value in report.metrics.items():
        lines.append(f"| {key.replace('_', ' ')} | {value} |")

    if report.errors:
        lines.extend(["", "## Errors", ""])
        for error in report.errors:
            lines.append(f"- {error}")

    lines.extend(["", "## Agent-Related Items", ""])
    if not report.tracked_items:
        lines.append("No agent-related items detected in this run.")
        return "\n".join(lines) + "\n"

    for item in report.tracked_items:
        lines.extend(
            [
                f"### {_item_link(item)}",
                "",
                f"- Status: `{item.status}`",
                f"- Author: `{item.author}`",
                f"- Updated: `{item.updated_at.strftime('%Y-%m-%d %H:%M UTC')}`",
                f"- Agent actors: {_csv_or_none(item.agent_actor_logins)}",
                f"- Agent summons / references: `{item.agent_reference_hits}`",
                f"- Latest actor: `{item.latest_actor}`",
                f"- Signals: {escape_cell('; '.join(item.signals))}",
                f"- Latest note excerpt: {escape_cell(item.latest_excerpt or '(none)')}",
                "",
                "#### Event Timeline",
                "",
            ]
        )
        for event in item.events:
            markers: list[str] = []
            if event.agent_actor:
                markers.append("agent actor")
            if event.agent_reference:
                markers.append("agent reference")
            marker_suffix = f" [{', '.join(markers)}]" if markers else ""
            lines.append(
                f"- `{event.created_at.strftime('%Y-%m-%d %H:%M UTC')}` "
                f"`{event.kind}` by `{event.actor}`{marker_suffix}: {escape_cell(_excerpt(event.body))}"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def render_summary(reports: list[RepoReport]) -> str:
    lines = [
        "# Agent Watcher Summary",
        "",
        "| Repo | Agent items | Summons | Closed/Merged | Open | Human follow-up after agent | Errors |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for report in reports:
        lines.append(
            "| "
            f"`{report.target.repo}` | "
            f"{report.metrics.get('agent_items', 0)} | "
            f"{report.metrics.get('agent_summons', 0)} | "
            f"{report.metrics.get('closed_items', 0)} | "
            f"{report.metrics.get('open_items', 0)} | "
            f"{report.metrics.get('stalled_items', 0)} | "
            f"{len(report.errors)} |"
        )

    return "\n".join(lines) + "\n"

def escape_cell(value: str) -> str:
    return value.replace("\n", " ").replace("|", "\\|")


def _item_link(item: TrackedItem) -> str:
    prefix = "PR" if item.kind == "pr" else "Issue"
    title = escape_cell(item.title)
    return f"[{prefix} #{item.number}: {title}]({item.url})"


def _csv_or_none(values: list[str]) -> str:
    if not values:
        return "(none)"
    return ", ".join(f"`{value}`" for value in values)


def _excerpt(text: str, *, limit: int = 180) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return "(no body)"
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _json(report: RepoReport) -> str:
    import json

    return json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
