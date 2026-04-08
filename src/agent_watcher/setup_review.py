from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from .config import load_setup_review_targets
from .github_api import GitHubClient
from .scheduling import repo_slug

PRIMARY_INSTRUCTION_PATHS = (
    "CLAUDE.md",
    "AGENTS.md",
    ".github/copilot-instructions.md",
)
ASSET_DIRECTORY_KINDS = (
    (".claude/agents", "subagents"),
    (".codex/agents", "subagents"),
    (".github/agents", "subagents"),
    (".codex/skills", "skills"),
    (".github/skills", "skills"),
    ("skills", "skills"),
    (".claude/commands", "commands"),
)
AGENT_WORKFLOW_KEYWORDS = (
    "claude",
    "copilot",
    "codex",
    "dragon-ai-agent",
    "openhands",
    "swe-agent",
)
MAX_EXCERPT_LINES = 12


@dataclass(frozen=True)
class SetupInstructionFile:
    path: str
    excerpt: str


@dataclass(frozen=True)
class SetupAssetDirectory:
    path: str
    kind: str
    entry_count: int
    sample_entries: list[str]


@dataclass(frozen=True)
class SetupWorkflowFile:
    path: str
    name: str
    agent_signals: list[str]


@dataclass
class SetupRepoReport:
    repo: str
    display_name: str
    short_name: str
    report_date: str
    default_branch: str
    generated_at: datetime
    instruction_files: list[SetupInstructionFile] = field(default_factory=list)
    asset_directories: list[SetupAssetDirectory] = field(default_factory=list)
    agent_workflows: list[SetupWorkflowFile] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    extra_prompt: str = ""

    def to_dict(self) -> dict:
        return _serialize(asdict(self))


def generate_setup_review_reports(
    client: GitHubClient,
    config_path: str | Path,
    *,
    generated_at: datetime | None = None,
    only_repos: set[str] | None = None,
) -> list[SetupRepoReport]:
    generated_at = generated_at or datetime.now(timezone.utc)
    targets = load_setup_review_targets(config_path, only_repos=only_repos)
    reports = [_collect_repo_setup(client, target, generated_at=generated_at) for target in targets]
    reports.sort(key=lambda report: report.repo)
    return reports


def write_setup_review_reports(output_dir: str | Path, reports: list[SetupRepoReport]) -> None:
    base = Path(output_dir)
    base.mkdir(parents=True, exist_ok=True)

    for report in reports:
        slug = repo_slug(report.repo)
        (base / f"{slug}.md").write_text(render_setup_review_report(report))
        (base / f"{slug}.json").write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")

    metadata = {
        "report_date": reports[0].report_date if reports else datetime.now(timezone.utc).date().isoformat(),
        "issue_title": (
            f"ontology agentic setup review for {reports[0].report_date}"
            if reports
            else f"ontology agentic setup review for {datetime.now(timezone.utc).date().isoformat()}"
        ),
        "repos": [report.repo for report in reports],
    }
    (base / "summary.md").write_text(render_setup_review_summary(reports))
    (base / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")


def render_setup_review_report(report: SetupRepoReport) -> str:
    lines = [
        f"# Agentic Setup Context: {report.display_name}",
        "",
        f"- Repo: `{report.repo}`",
        f"- Default branch: `{report.default_branch}`",
        f"- Generated: `{report.generated_at.isoformat()}`",
        f"- Report date: `{report.report_date}`",
        f"- Instruction files found: `{len(report.instruction_files)}`",
        f"- Instruction asset directories found: `{len(report.asset_directories)}`",
        f"- Agent-related workflows found: `{len(report.agent_workflows)}`",
    ]

    if report.errors:
        lines.extend(["", "## Errors", ""])
        for error in report.errors:
            lines.append(f"- {error}")

    lines.extend(["", "## Findings", ""])
    if report.findings:
        for finding in report.findings:
            lines.append(f"- {finding}")
    else:
        lines.append("- No obvious setup gaps detected from the collected signals.")

    lines.extend(["", "## Instruction Files", ""])
    if report.instruction_files:
        for item in report.instruction_files:
            lines.extend(
                [
                    f"### `{item.path}`",
                    "",
                    "```text",
                    item.excerpt,
                    "```",
                    "",
                ]
            )
    else:
        lines.append("No primary instruction files were detected.")

    lines.extend(["## Instruction Assets", ""])
    if report.asset_directories:
        for item in report.asset_directories:
            sample = ", ".join(f"`{name}`" for name in item.sample_entries) if item.sample_entries else "(none)"
            lines.extend(
                [
                    f"- `{item.path}` ({item.kind})",
                    f"  entries: `{item.entry_count}`",
                    f"  sample: {sample}",
                ]
            )
    else:
        lines.append("No subagent, skill, or command directories were detected.")

    lines.extend(["", "## Agent Workflows", ""])
    if report.agent_workflows:
        for workflow in report.agent_workflows:
            signal_text = ", ".join(f"`{signal}`" for signal in workflow.agent_signals)
            lines.append(f"- `{workflow.path}` ({workflow.name}) -> {signal_text}")
    else:
        lines.append("No agent-related workflow files were detected.")

    if report.extra_prompt:
        lines.extend(["", "## Repo-Specific Guidance", "", report.extra_prompt])

    return "\n".join(lines) + "\n"


def render_setup_review_summary(reports: list[SetupRepoReport]) -> str:
    lines = [
        "# Ontology Agentic Setup Summary",
        "",
        "| Repo | Instructions | Subagents | Skills/Commands | Agent Workflows | Findings |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for report in reports:
        subagents = _count_assets(report.asset_directories, "subagents")
        skills_commands = _count_assets(report.asset_directories, "skills") + _count_assets(
            report.asset_directories, "commands"
        )
        lines.append(
            "| "
            f"`{report.repo}` | "
            f"{len(report.instruction_files)} | "
            f"{subagents} | "
            f"{skills_commands} | "
            f"{len(report.agent_workflows)} | "
            f"{len(report.findings)} |"
        )

    missing_instructions = [f"`{report.repo}`" for report in reports if not report.instruction_files]
    missing_decomposition = [
        f"`{report.repo}`"
        for report in reports
        if report.instruction_files and not _count_assets(report.asset_directories, "subagents")
        and not _count_assets(report.asset_directories, "skills")
        and not _count_assets(report.asset_directories, "commands")
    ]
    with_subagents = [
        f"`{report.repo}`" for report in reports if _count_assets(report.asset_directories, "subagents")
    ]

    lines.extend(["", "## Cross-Repo Comparison Notes", ""])
    if missing_instructions:
        lines.append(f"- No primary agent instruction file detected: {', '.join(missing_instructions)}")
    if missing_decomposition:
        lines.append(
            f"- Has a primary instruction file but no obvious skill/subagent decomposition: {', '.join(missing_decomposition)}"
        )
    if with_subagents:
        lines.append(f"- Repos with explicit subagent directories: {', '.join(with_subagents)}")
    if not any([missing_instructions, missing_decomposition, with_subagents]):
        lines.append("- Cross-repo structure looks broadly uniform from the collected signals.")

    return "\n".join(lines) + "\n"


def _collect_repo_setup(client: GitHubClient, target: dict, *, generated_at: datetime) -> SetupRepoReport:
    report = SetupRepoReport(
        repo=target["repo"],
        display_name=target["display_name"],
        short_name=target["short_name"],
        report_date=generated_at.astimezone(ZoneInfo(target["report_timezone"])).date().isoformat(),
        default_branch="unknown",
        generated_at=generated_at,
        extra_prompt=target.get("extra_prompt", ""),
    )

    try:
        repo_info = client.get_repo(report.repo)
        report.default_branch = repo_info.get("default_branch") or "unknown"

        for path in PRIMARY_INSTRUCTION_PATHS:
            text = client.get_file_text(report.repo, path, ref=report.default_branch)
            if text is not None:
                report.instruction_files.append(
                    SetupInstructionFile(path=path, excerpt=_excerpt_lines(text, limit=MAX_EXCERPT_LINES))
                )

        for path, kind in ASSET_DIRECTORY_KINDS:
            contents = client.list_directory_contents(report.repo, path, ref=report.default_branch)
            if not contents:
                continue
            sample_entries = [item.get("name", "") for item in contents[:5] if item.get("name")]
            report.asset_directories.append(
                SetupAssetDirectory(
                    path=path,
                    kind=kind,
                    entry_count=len(contents),
                    sample_entries=sample_entries,
                )
            )

        workflow_entries = client.list_directory_contents(report.repo, ".github/workflows", ref=report.default_branch) or []
        for item in workflow_entries:
            path = item.get("path", "")
            if item.get("type") != "file" or not path.endswith((".yml", ".yaml")):
                continue
            text = client.get_file_text(report.repo, path, ref=report.default_branch) or ""
            signals = _detect_agent_workflow_signals(text)
            if signals:
                report.agent_workflows.append(
                    SetupWorkflowFile(
                        path=path,
                        name=_extract_workflow_name(text) or item.get("name", path),
                        agent_signals=signals,
                    )
                )
    except Exception as exc:  # pragma: no cover - network/operational safety
        report.errors.append(str(exc))

    report.findings = _build_setup_findings(report)
    return report


def _build_setup_findings(report: SetupRepoReport) -> list[str]:
    findings: list[str] = []
    instruction_paths = {item.path for item in report.instruction_files}
    asset_kinds = {item.kind for item in report.asset_directories}

    if not report.instruction_files:
        findings.append("No primary agent instruction file detected (`CLAUDE.md`, `AGENTS.md`, or `.github/copilot-instructions.md`).")
    elif instruction_paths == {".github/copilot-instructions.md"}:
        findings.append("Only Copilot instructions were detected; there is no obvious repo-wide CLAUDE/AGENTS instruction file.")

    if len(report.instruction_files) > 1:
        findings.append("Multiple primary instruction files are present; check them for drift and overlapping guidance.")

    if report.instruction_files and "subagents" not in asset_kinds and "skills" not in asset_kinds and "commands" not in asset_kinds:
        findings.append("Has a primary instruction file but no obvious skill, command, or subagent decomposition.")

    if ("subagents" in asset_kinds or "skills" in asset_kinds or "commands" in asset_kinds) and not report.instruction_files:
        findings.append("Has decomposed agent assets but no obvious top-level instruction file tying them together.")

    if not report.agent_workflows:
        findings.append("No agent-related workflows were detected in `.github/workflows`.")

    return findings


def _detect_agent_workflow_signals(text: str) -> list[str]:
    lowered = text.lower()
    signals: list[str] = []
    for keyword in AGENT_WORKFLOW_KEYWORDS:
        if keyword in lowered:
            signals.append(keyword)
    action_uses = re.findall(r"uses:\s*([^\s]+)", text)
    for action in action_uses:
        lowered_action = action.lower()
        if any(keyword in lowered_action for keyword in AGENT_WORKFLOW_KEYWORDS):
            signals.append(action)
    deduped: list[str] = []
    for signal in signals:
        if signal not in deduped:
            deduped.append(signal)
    return deduped


def _extract_workflow_name(text: str) -> str | None:
    match = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip("'\"")


def _excerpt_lines(text: str, *, limit: int) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    trimmed = [line for line in lines if line.strip()][:limit]
    return "\n".join(trimmed) if trimmed else "(file is empty)"


def _count_assets(items: Iterable[SetupAssetDirectory], kind: str) -> int:
    return sum(item.entry_count for item in items if item.kind == kind)


def _serialize(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value
