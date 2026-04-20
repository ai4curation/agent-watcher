from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_watcher.github_api import GitHubClient


class FakeGitHubClient(GitHubClient):
    def __init__(self, issue_pages, pull_pages):
        super().__init__(token=None)
        self.issue_pages = issue_pages
        self.pull_pages = pull_pages

    def _request_json(self, method: str, path_or_url: str, *, data=None):
        if "/issues?" in path_or_url:
            page = _page_number(path_or_url)
            return self.issue_pages.get(page, [])
        if "/pulls?" in path_or_url:
            page = _page_number(path_or_url)
            return self.pull_pages.get(page, [])
        raise AssertionError(f"Unexpected request: {path_or_url}")


class GitHubApiTests(TestCase):
    def test_recent_items_merge_issue_and_pull_feeds(self):
        client = FakeGitHubClient(
            issue_pages={
                1: [
                    _issue(31912, "2026-04-18T15:38:00Z"),
                    _issue(19185, "2026-04-17T07:59:00Z"),
                    _issue(31869, "2026-04-16T01:04:00Z"),
                ]
            },
            pull_pages={
                1: [
                    _pull(31920, "2026-04-18T12:00:00Z", author="cmungall"),
                    _pull(31911, "2026-04-17T08:22:37Z", author="dragon-ai-agent"),
                    _pull(31907, "2026-04-16T00:26:18Z", author="dragon-ai-agent"),
                    _pull(31801, "2026-04-01T11:12:00Z", author="dragon-ai-agent"),
                ]
            },
        )

        items = client.list_recent_issues_and_prs(
            "geneontology/go-ontology",
            since="2026-04-05T22:51:16Z",
            max_items=5,
        )

        self.assertEqual(
            [(item["number"], bool(item.get("pull_request"))) for item in items],
            [
                (31912, False),
                (31920, True),
                (31911, True),
                (19185, False),
                (31869, False),
            ],
        )


def _page_number(path_or_url: str) -> int:
    marker = "page="
    return int(path_or_url.rsplit(marker, 1)[1].split("&", 1)[0])


def _issue(number: int, updated_at: str) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "html_url": f"https://github.com/example/repo/issues/{number}",
        "state": "open",
        "created_at": updated_at,
        "updated_at": updated_at,
        "body": "",
        "user": {"login": "curator"},
    }


def _pull(number: int, updated_at: str, *, author: str) -> dict:
    return {
        "number": number,
        "title": f"PR {number}",
        "html_url": f"https://github.com/example/repo/pull/{number}",
        "url": f"https://api.github.com/repos/example/repo/pulls/{number}",
        "state": "closed",
        "created_at": updated_at,
        "updated_at": updated_at,
        "body": "",
        "user": {"login": author},
    }
