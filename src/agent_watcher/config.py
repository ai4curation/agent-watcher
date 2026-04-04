from __future__ import annotations

import json
from pathlib import Path

from .models import TargetRepo


def load_targets(
    config_path: str | Path,
    *,
    lookback_days: int | None = None,
    max_items: int | None = None,
    only_repos: set[str] | None = None,
) -> list[TargetRepo]:
    path = Path(config_path)
    payload = json.loads(path.read_text())
    defaults = payload.get("defaults", {})
    targets: list[TargetRepo] = []

    for raw_target in payload.get("targets", []):
        repo = raw_target["repo"]
        if only_repos and repo not in only_repos:
            continue

        merged = {**defaults, **raw_target}
        targets.append(
            TargetRepo(
                repo=repo,
                display_name=merged.get("display_name", repo),
                short_name=merged.get("short_name", repo.split("/")[-1]),
                lookback_days=lookback_days or int(merged.get("lookback_days", 7)),
                max_items=max_items or int(merged.get("max_items", 20)),
                max_comments_per_item=int(merged.get("max_comments_per_item", 25)),
                report_timezone=merged.get("report_timezone", "America/Los_Angeles"),
                issue_mode=merged.get("issue_mode", "dated"),
                issue_title_template=merged.get(
                    "issue_title_template",
                    "{short_name} report for {report_date}",
                ),
                cadence=merged.get("cadence", "weekly"),
                preferred_weekday_utc=_optional_int(merged.get("preferred_weekday_utc")),
                preferred_hour_utc=_optional_int(merged.get("preferred_hour_utc")),
                extra_prompt=merged.get("extra_prompt", ""),
                agent_login_substrings=_normalize_strings(
                    merged.get("agent_login_substrings", [])
                ),
                agent_text_patterns=_normalize_strings(
                    merged.get("agent_text_patterns", [])
                ),
            )
        )

    return targets


def _normalize_strings(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        cleaned = value.strip().lower()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return tuple(normalized)


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
