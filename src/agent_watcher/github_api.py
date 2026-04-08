from __future__ import annotations

import base64
import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str | None, *, user_agent: str = "agent-watcher/0.1") -> None:
        self.token = token
        self.user_agent = user_agent

    def list_recent_issues_and_prs(
        self,
        repo: str,
        *,
        since: str,
        max_items: int,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1

        while len(items) < max_items:
            payload = self._request_json(
                "GET",
                f"/repos/{repo}/issues?state=all&sort=updated&direction=desc&since={quote(since)}&per_page=100&page={page}",
            )
            if not payload:
                break

            for item in payload:
                items.append(item)
                if len(items) >= max_items:
                    break

            page += 1

        return items

    def list_issue_comments(
        self,
        repo: str,
        number: int,
        *,
        max_comments: int,
    ) -> list[dict[str, Any]]:
        return self._request_json(
            "GET",
            f"/repos/{repo}/issues/{number}/comments?per_page={max_comments}",
        )

    def list_pr_reviews(
        self,
        repo: str,
        number: int,
        *,
        max_reviews: int,
    ) -> list[dict[str, Any]]:
        return self._request_json(
            "GET",
            f"/repos/{repo}/pulls/{number}/reviews?per_page={max_reviews}",
        )

    def get_pr(self, repo: str, number: int) -> dict[str, Any]:
        return self._request_json("GET", f"/repos/{repo}/pulls/{number}")

    def get_repo(self, repo: str) -> dict[str, Any]:
        return self._request_json("GET", f"/repos/{repo}")

    def list_directory_contents(
        self,
        repo: str,
        path: str,
        *,
        ref: str | None = None,
    ) -> list[dict[str, Any]] | None:
        encoded_path = quote(path, safe="/")
        suffix = f"?ref={quote(ref, safe='')}" if ref else ""
        try:
            payload = self._request_json("GET", f"/repos/{repo}/contents/{encoded_path}{suffix}")
        except RuntimeError as exc:
            if "404" in str(exc):
                return None
            raise
        if isinstance(payload, list):
            return payload
        return None

    def get_file_text(
        self,
        repo: str,
        path: str,
        *,
        ref: str | None = None,
    ) -> str | None:
        encoded_path = quote(path, safe="/")
        suffix = f"?ref={quote(ref, safe='')}" if ref else ""
        try:
            payload = self._request_json("GET", f"/repos/{repo}/contents/{encoded_path}{suffix}")
        except RuntimeError as exc:
            if "404" in str(exc):
                return None
            raise
        if not isinstance(payload, dict) or payload.get("type") != "file":
            return None
        content = payload.get("content", "")
        if not content:
            return ""
        if payload.get("encoding") == "base64":
            return base64.b64decode(content).decode("utf-8", errors="replace")
        return str(content)

    def find_issue_by_title(self, repo: str, title: str) -> dict[str, Any] | None:
        page = 1
        while True:
            issues = self._request_json(
                "GET",
                f"/repos/{repo}/issues?state=all&per_page=100&page={page}",
            )
            if not issues:
                return None
            for issue in issues:
                if issue.get("pull_request"):
                    continue
                if issue.get("title") == title:
                    return issue
            page += 1

    def ensure_label(
        self,
        repo: str,
        *,
        name: str,
        color: str,
        description: str,
    ) -> None:
        encoded = quote(name, safe="")
        try:
            self._request_json("GET", f"/repos/{repo}/labels/{encoded}")
            return
        except RuntimeError as exc:
            if "404" not in str(exc):
                raise

        self._request_json(
            "POST",
            f"/repos/{repo}/labels",
            data={"name": name, "color": color, "description": description},
        )

    def create_issue(
        self,
        repo: str,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"title": title, "body": body}
        if labels:
            data["labels"] = labels
        return self._request_json("POST", f"/repos/{repo}/issues", data=data)

    def update_issue(
        self,
        repo: str,
        number: int,
        *,
        body: str | None = None,
        state: str | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if body is not None:
            data["body"] = body
        if state is not None:
            data["state"] = state
        return self._request_json("PATCH", f"/repos/{repo}/issues/{number}", data=data)

    def create_issue_comment(self, repo: str, number: int, *, body: str) -> dict[str, Any]:
        return self._request_json(
            "POST",
            f"/repos/{repo}/issues/{number}/comments",
            data={"body": body},
        )

    def _request_json(
        self,
        method: str,
        path_or_url: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> Any:
        url = path_or_url if path_or_url.startswith("http") else f"{API_ROOT}{path_or_url}"
        payload = None if data is None else json.dumps(data).encode("utf-8")

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": self.user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request = Request(url, headers=headers, data=payload, method=method)

        try:
            with urlopen(request) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else None
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"GitHub API {method} {url} failed with {exc.code}: {detail[:400]}"
            ) from exc
