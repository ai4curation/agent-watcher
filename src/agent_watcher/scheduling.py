from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .models import TargetRepo


@dataclass(frozen=True)
class TargetRunMetadata:
    report_date: str
    issue_title: str


def build_target_run_metadata(target: TargetRepo, now_utc: datetime) -> TargetRunMetadata:
    local_now = now_utc.astimezone(ZoneInfo(target.report_timezone))
    report_date = local_now.date().isoformat()
    issue_title = target.issue_title_template.format(
        repo=target.repo,
        repo_slug=repo_slug(target.repo),
        short_name=target.short_name,
        display_name=target.display_name,
        report_date=report_date,
    )
    return TargetRunMetadata(report_date=report_date, issue_title=issue_title)


def target_is_due(target: TargetRepo, now_utc: datetime) -> bool:
    now_utc = now_utc.astimezone(timezone.utc)

    cadence = target.cadence.lower()
    if cadence == "daily":
        return True
    if cadence == "weekly":
        if target.preferred_weekday_utc is None:
            return True
        return now_utc.weekday() == target.preferred_weekday_utc
    if cadence == "manual":
        return False

    raise ValueError(f"Unsupported cadence: {target.cadence}")


def repo_slug(repo: str) -> str:
    return repo.replace("/", "__")
