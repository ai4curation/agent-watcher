from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_watcher.setup_review import generate_setup_review_reports, render_setup_review_summary


class FakeGitHubClient:
    def __init__(self, repo_info, files=None, directories=None):
        self.repo_info = repo_info
        self.files = files or {}
        self.directories = directories or {}

    def get_repo(self, repo):
        return self.repo_info[repo]

    def get_file_text(self, repo, path, *, ref=None):
        return self.files.get((repo, path))

    def list_directory_contents(self, repo, path, *, ref=None):
        return self.directories.get((repo, path))


class SetupReviewTests(TestCase):
    def test_collects_setup_signals_and_summary(self):
        config = {
            "defaults": {
                "report_timezone": "America/Los_Angeles",
                "include_in_setup_review": True,
            },
            "targets": [
                {
                    "repo": "example/ontology",
                    "display_name": "Example Ontology",
                    "short_name": "example-ontology",
                }
            ],
        }
        client = FakeGitHubClient(
            repo_info={"example/ontology": {"default_branch": "main"}},
            files={
                ("example/ontology", "CLAUDE.md"): "# Agent instructions\n\nPrefer narrow subagents.",
                (
                    "example/ontology",
                    ".github/workflows/agent-review.yml",
                ): "name: Agent Review\non: push\njobs:\n  review:\n    steps:\n      - uses: anthropics/claude-code-action@v1\n",
            },
            directories={
                ("example/ontology", ".claude/agents"): [
                    {"name": "curation.md", "path": ".claude/agents/curation.md", "type": "file"}
                ],
                ("example/ontology", ".claude/skills"): [
                    {"name": "term-editing", "path": ".claude/skills/term-editing", "type": "dir"}
                ],
                ("example/ontology", ".github/workflows"): [
                    {
                        "name": "agent-review.yml",
                        "path": ".github/workflows/agent-review.yml",
                        "type": "file",
                    }
                ],
            },
        )

        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "targets.json"
            config_path.write_text(json.dumps(config))
            reports = generate_setup_review_reports(
                client,
                config_path,
                generated_at=datetime(2026, 4, 7, 15, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(len(reports), 1)
        report = reports[0]
        self.assertEqual(report.default_branch, "main")
        self.assertEqual(len(report.instruction_files), 1)
        self.assertEqual(len(report.asset_directories), 2)
        self.assertEqual(len(report.agent_workflows), 1)
        self.assertIn("anthropics/claude-code-action@v1", report.agent_workflows[0].agent_signals)
        self.assertIn(".claude/skills", [item.path for item in report.asset_directories])
        self.assertIn("`example/ontology`", render_setup_review_summary(reports))

    def test_flags_missing_instruction_file(self):
        config = {
            "defaults": {"report_timezone": "America/Los_Angeles"},
            "targets": [{"repo": "example/no-instructions", "display_name": "No Instructions", "short_name": "no-instructions"}],
        }
        client = FakeGitHubClient(
            repo_info={"example/no-instructions": {"default_branch": "main"}},
            directories={("example/no-instructions", ".github/workflows"): []},
        )

        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "targets.json"
            config_path.write_text(json.dumps(config))
            reports = generate_setup_review_reports(
                client,
                config_path,
                generated_at=datetime(2026, 4, 7, 15, 0, tzinfo=timezone.utc),
            )

        self.assertIn("No primary agent instruction file detected", reports[0].findings[0])
