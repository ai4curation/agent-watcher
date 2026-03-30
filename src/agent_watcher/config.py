from __future__ import annotations

import json
from pathlib import Path

from .models import TargetRepo


def load_targets(
    config_path: str | Path,
    *,
    lookback_days: int | None = None,
    max_items: int | None = None,
    only_repo: str | None = None,
) -> list[TargetRepo]:
    path = Path(config_path)
    payload = json.loads(path.read_text())
    defaults = payload.get("defaults", {})
    targets: list[TargetRepo] = []

    for raw_target in payload.get("targets", []):
        repo = raw_target["repo"]
        if only_repo and repo != only_repo:
            continue

        merged = {**defaults, **raw_target}
        targets.append(
            TargetRepo(
                repo=repo,
                display_name=merged.get("display_name", repo),
                lookback_days=lookback_days or int(merged.get("lookback_days", 7)),
                max_items=max_items or int(merged.get("max_items", 20)),
                max_comments_per_item=int(merged.get("max_comments_per_item", 25)),
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
